from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from datetime import datetime, timezone
from dataclasses import replace
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from src.common.settings import (
    DEFAULT_CONFIG_PATH,
    ROOT_DIR,
    AppSettings,
    apply_settings_patch,
    ensure_data_directories,
    load_settings,
    resolve_app_path,
    save_settings,
)
from src.common.network import client_connect_host
from src.common.discovery import DiscoveryService, validate_discovery
from src.common.network_manager import NetworkManager
from src.core.orchestration import CoreOrchestrator
from src.core.power_save import PowerSaveState
from src.common.system_resources import SystemResourceMonitor
from src.common.alarm_history import AlarmHistory, MAP_VERSION
from src.common.status import (
    AlarmBook,
    CommandCode,
    CommandResult,
    CommandRuntime,
    CommandStatus,
    ControllerState,
    DeviceSummaryBit,
    TrackingState,
    now_ms,
)
from src.communication.json_tcp import JsonTcpResponse, JsonLineServer
from src.communication.plc_memory_mapping import (
    BYTE_COUNT,
    CameraMasks,
    PlcInput,
    PlcOutput,
    ProcessFlags,
    words_from_bytes,
)
from src.communication.plc_shared_memory import (
    FLAG_LAST_READ_OK,
    FLAG_LAST_WRITE_OK,
    FLAG_PLC_CONNECTED,
    PlcSharedMemoryClient,
    SharedMemoryError,
    SharedMemoryHeader,
)
from src.communication.xgt_gateway_client import XgtGatewayClient
from src.devices import DeviceRegistry, LaserService, PtmService, VisionDeviceService
from src.processes import ProcessManager
from src.pt_motor.motor_control import MotorApiClient
from src.vision.vision_client import VisionSnapshot, VisionTcpClient


LOGGER = logging.getLogger("tracker_core")


class CoreApplication:
    def __init__(self, settings_path: Path = DEFAULT_CONFIG_PATH, *, managed_worker: bool = False,
                 plc_restart_seq: int | None = None) -> None:
        self._managed_worker = managed_worker
        self._suspended_processes: set[str] = set()
        self.settings_path = settings_path
        self.settings = load_settings(settings_path)
        ensure_data_directories(self.settings)
        if not managed_worker:
            save_settings(self.settings, settings_path)
        self._log_handler: RotatingFileHandler | None = None

        self.alarms = AlarmBook()
        self.alarm_history = AlarmHistory(
            resolve_app_path(self.settings.paths.logs_dir) / "alarms.sqlite3",
            ROOT_DIR / "web/assets/js/plc_map_data.js",
        )
        self._monitor_plc_cache: dict[str, Any] = {}
        self.command = CommandRuntime()
        self._command_seen = False
        self._controller_restart_request: dict[str, Any] | None = None
        if plc_restart_seq is not None:
            if not managed_worker or type(plc_restart_seq) is not int or not 0 <= plc_restart_seq <= 65535:
                raise ValueError("PLC restart sequence requires a managed worker and a UINT16 value")
            self.command.last_code = CommandCode.CONTROLLER_RESTART
            self.command.last_seq = plc_restart_seq
            self.command.status = CommandStatus.COMPLETE
            self.command.result = CommandResult.OK
            self.command.message = "Main Core restarted"
            self._command_seen = True
        self.plc_input = PlcInput()
        self.vision_snapshot = VisionSnapshot()
        self._vision_result_missing_since_ms: int | None = None
        self.output = PlcOutput(alarms=self.alarms, command=self.command)

        self._server = JsonLineServer(
            self.settings.core.host,
            self.settings.core.port,
            self.handle_tcp_command,
            static_root=resolve_app_path(self.settings.paths.web_dir),
            allow_remote_process_control=self.settings.core.allow_remote_process_control,
            banner={
                "event": "service.ready",
                "data": {
                    "protocol": "Tracker-Core",
                    "version": "0.1",
                    "mode": "headless",
                },
            },
        )
        self._http_server: JsonLineServer | None = None
        if (
            self.settings.core.http_port
            and self.settings.core.http_port != self.settings.core.port
        ):
            self._http_server = JsonLineServer(
                self.settings.core.host,
                self.settings.core.http_port,
                self.handle_tcp_command,
                static_root=resolve_app_path(self.settings.paths.web_dir),
                allow_remote_process_control=self.settings.core.allow_remote_process_control,
            )
        self._run_task: asyncio.Task[None] | None = None
        self._gateway_config_task: asyncio.Task[None] | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._http_server_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._started = False
        self.system_resources = SystemResourceMonitor(ROOT_DIR)
        self.discovery = DiscoveryService(self.settings.discovery, self.settings.core.port,
                                          self.settings.core.http_port or self.settings.core.port)
        self.network_manager = NetworkManager("eth0")

        self._gateway_config: dict[str, Any] = {}
        self._gateway_config_error = ""
        self._gateway_refresh_ms = 0
        self._gateway_update_in_progress = False
        self._settings_lock = asyncio.Lock()
        self._config_request_lock = asyncio.Lock()
        self._rebuild_clients()
        self._gateway_configured = not self._plc_enabled()
        self._last_plc_heartbeat: int | None = None
        self._last_plc_heartbeat_change_ms = now_ms()
        self._position_ok_since_ms: int | None = None
        self._safe_stop_applied = False
        self._safe_stop_task: asyncio.Task[bool] | None = None
        self._safe_stop_retry_ms = 0
        self._safe_stop_errors: dict[str, str] = {}
        self.power_save = PowerSaveState(self.settings.runtime.power_save_timeout_s)
        self._wake_up_previous = False
        self._power_laser_task: asyncio.Task[None] | None = None
        self._power_laser_retry_ms = 0
        self._laser_permit_previous = False
        self._power_sleeping_previous = False
        self._laser_control_lock = asyncio.Lock()
        self._command_task: asyncio.Task[None] | None = None
        self._command_request_params: dict[str, Any] = {}
        self._pending_request: tuple[int, int, str, dict[str, Any]] | None = None
        self._active_motion: dict[str, Any] | None = None
        self._active_recipe_id = 0
        self._active_point_id = 0
        self._last_status: dict[str, Any] = {}
        self._cycle_count = 0
        self._last_cycle_ms = 0
        self._last_cycle_duration_ms = 0
        self._last_cycle_error = ""
        self._plc_read_error = ""
        self._diagnostic_states: dict[str, str] = {}
        self._bypass_stop_tasks: set[asyncio.Task[None]] = set()
        self._last_bypass_state: dict[str, bool] = {}
        self.nutrunner_result: dict[str, Any] | None = None
        self._sync_bypass_state(initial=True)
        if managed_worker:
            endpoint = self.settings.processes["core"]
            self._server.host = str(endpoint["health_host"])
            self._server.port = int(endpoint["health_port"])
            self._server.static_root = None
            self._http_server = None

    @staticmethod
    def _validate_operator_settings(settings: AppSettings) -> None:
        timeout = settings.runtime.power_save_timeout_s
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 1 <= timeout <= 86400:
            raise ValueError("Power-save timeout must be between 1 and 86400 seconds")
        for key in ("core", "plc_gateway", "ptm", "vision"):
            module = settings.processes.get(key, {})
            if not 1 <= int(module.get("health_port", 1)) <= 65535:
                raise ValueError(f"{key} TCP port must be between 1 and 65535")
            if "http_port" in module:
                minimum = 1 if key in {"plc_gateway", "ptm"} else 0
                if not minimum <= int(module["http_port"]) <= 65535:
                    raise ValueError(f"{key} HTTP port must be between {minimum} and 65535")
        if settings.logging.level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("Invalid logging level")
        if not 1 <= settings.logging.max_file_mb <= 100:
            raise ValueError("Log file size must be between 1 and 100 MB")
        if not 1 <= settings.logging.backup_count <= 30:
            raise ValueError("Log backup count must be between 1 and 30")
        if not 1 <= settings.system.memory_warn_percent <= 100:
            raise ValueError("Memory warning threshold must be between 1 and 100")
        if not 1 <= settings.system.disk_warn_percent <= 100:
            raise ValueError("Disk warning threshold must be between 1 and 100")
        if not 100 <= settings.runtime.plc_heartbeat_warn_ms < settings.runtime.plc_heartbeat_fault_ms <= 60000:
            raise ValueError("PLC fault timeout must exceed warning timeout (100-60000 ms)")
        if not 100 <= settings.runtime.vision_status_max_age_ms <= 60000:
            raise ValueError("Vision status timeout must be between 100 and 60000 ms")

    def _configure_logging(self) -> None:
        root_logger = logging.getLogger()
        settings = self.settings.logging
        new_handler = None
        if settings.file_enabled and not self._managed_worker:
            log_path = resolve_app_path(self.settings.paths.logs_dir) / "core.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            new_handler = RotatingFileHandler(
                log_path, maxBytes=settings.max_file_mb * 1024 * 1024,
                backupCount=settings.backup_count, encoding="utf-8",
            )
            new_handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s"
            ))
        if self._log_handler is not None:
            root_logger.removeHandler(self._log_handler)
            self._log_handler.close()
        self._log_handler = new_handler
        if new_handler is not None:
            root_logger.addHandler(new_handler)
        root_logger.setLevel(getattr(logging, settings.level))

    def _rebuild_clients(self) -> None:
        motor_settings = replace(self.settings.motor, enabled=self._motor_enabled())
        laser_settings = replace(self.settings.laser, enabled=self._laser_enabled())
        vision_settings = replace(self.settings.vision, enabled=self._vision_enabled())
        self.xgt_gateway = XgtGatewayClient(self.settings.xgt)
        self.plc_memory = PlcSharedMemoryClient(
            self._gateway_config.get("shared_memory", {}).get("name")
            or self.settings.xgt.shared_memory.name
        )
        self._reset_plc_write_tracking()
        self.motor_api = MotorApiClient(motor_settings)
        self.vision_api = VisionTcpClient(vision_settings)
        self.ptm_service = PtmService(motor_settings, self.motor_api)
        self.laser_service = LaserService(
            laser_settings,
            self.ptm_service,
            self.motor_api,
        )
        self.vision_service = VisionDeviceService(vision_settings, self.vision_api)
        self.orchestrator = CoreOrchestrator(self.vision_service)
        self.devices = DeviceRegistry(
            [self.ptm_service, self.laser_service, self.vision_service]
        )
        modules = {} if self._managed_worker else {
            key: value for key, value in self.settings.processes.items()
            if key != "core" or getattr(self, "_web_host", False)
        }
        if hasattr(self, "process_manager"):
            self.process_manager.update_modules(modules)
        else:
            self.process_manager = ProcessManager(modules, manage_persisted_processes=not self._managed_worker)
        self.process_manager.update_communication_endpoints(
            self._process_communication_endpoints()
        )

    def _process_enabled(self, key: str) -> bool:
        return key not in self._suspended_processes and self.settings.processes.get(key, {}).get("enabled", True) is not False

    def _plc_enabled(self) -> bool:
        return self._process_enabled("plc_gateway")

    def _motor_enabled(self) -> bool:
        return self.settings.motor.enabled and self._process_enabled("ptm")

    def _laser_enabled(self) -> bool:
        return (
            self.settings.laser.enabled
            and (self.settings.laser.mode.lower() not in {"ptm", "gpio"} or self._motor_enabled())
        )

    def _vision_enabled(self) -> bool:
        return self.settings.vision.enabled and self._process_enabled("vision")

    def _bypass_status(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for name, settings in (("ptm", self.settings.motor), ("vision", self.settings.vision)):
            plc = bool(getattr(self.plc_input, f"{name}_bypass", False))
            web = bool(settings.bypass)
            result[name] = {"active": plc or web, "source": "plc" if plc else "web" if web else None,
                            "plc": plc, "web": web}
        vision_state = self.vision_service.state
        vision_local = bool(
            vision_state.enabled and vision_state.last_ok_ms and not vision_state.last_error
            and now_ms() - vision_state.last_ok_ms <= self.settings.runtime.vision_status_max_age_ms
            and vision_state.status.get("tracking_bypass") is True
        )
        result["vision"]["device"] = vision_local
        if vision_local and not result["vision"]["active"]:
            result["vision"].update(active=True, source="device")
        laser_local = bool(
            (self.laser_service.state.online and self.laser_service.state.status.get("bypass") is True)
            or (self.settings.laser.mode.lower() in {"ptm", "gpio"} and self.ptm_service.state.online
                and self.ptm_service.state.status.get("laser_bypass") is True)
        )
        result["laser"] = {"active": result["ptm"]["active"] or laser_local,
                           "source": "ptm" if result["ptm"]["active"] else "device" if laser_local else None}
        return result

    def _device_bypassed(self, device: str) -> bool:
        return bool(self._bypass_status().get(device, {}).get("active"))

    def _complete_bypassed_command(self, device: str) -> None:
        codes = ({CommandCode.TARGET_APPLY_MOVE, CommandCode.HOME} if device == "ptm"
                 else {CommandCode.NUTRUNNER_DATA_REQUEST})
        if self.command.last_code not in codes or self.command.status not in {
            CommandStatus.RECEIVED, CommandStatus.BUSY, CommandStatus.ERROR, CommandStatus.REJECTED,
        }:
            return
        fault_active = self.alarms.fault_summary(self.settings.runtime.fault_latch_enabled)
        if self.plc_input.force_stop or not self.plc_input.run_enable or fault_active:
            result = (CommandResult.FORCE_STOP_ACTIVE if self.plc_input.force_stop
                      else CommandResult.FAULT_ACTIVE if fault_active else CommandResult.SYSTEM_NOT_READY)
            self._abort_active_command(result)
            return
        if self.command.status in {CommandStatus.ERROR, CommandStatus.REJECTED} and self.command.result in {
            CommandResult.INVALID_COMMAND, CommandResult.FORCE_STOP_ACTIVE,
        }:
            return
        params = (self._active_motion or {}).get("params", self._command_request_params)
        if self._pending_request is not None:
            params = self._pending_request[3]
        if self._command_task is not None and not self._command_task.done():
            self._command_task.cancel()
        self._command_task = None
        self._command_request_params = {}
        self._pending_request = None
        if device == "ptm":
            self._active_motion = None
            if self.command.last_code == CommandCode.TARGET_APPLY_MOVE:
                self._active_recipe_id = int(params.get("recipe_id", self.plc_input.req_recipe_id))
                self._active_point_id = int(params.get("point_id", self.plc_input.req_point_id))
        else:
            self.nutrunner_result = None
        self.command.status = CommandStatus.COMPLETE
        self.command.result = CommandResult.OK
        self.command.message = f"{device} bypassed; no device operation"

    def _sync_bypass_state(self, *, initial: bool = False) -> None:
        bypass = self._bypass_status()
        suppressed = set()
        if bypass["ptm"]["active"]:
            suppressed.update((DeviceSummaryBit.PAN_TILT_MOTOR,
                               DeviceSummaryBit.TILT_MOTOR, DeviceSummaryBit.LASER))
        if bypass["vision"]["active"]:
            suppressed.update((DeviceSummaryBit.VISION_COMMON, DeviceSummaryBit.TRACKER,
                               DeviceSummaryBit.CAMERA_1, DeviceSummaryBit.CAMERA_2,
                               DeviceSummaryBit.CAMERA_3, DeviceSummaryBit.CAMERA_4))
        self.alarms.suppressed_devices = {int(device) for device in suppressed}
        for name, service in self.devices.services.items():
            active = bool(bypass["ptm" if name == "laser" else name]["active"])
            previous = self._last_bypass_state.get(name, active)
            # An app-side bypass must keep polling so its release is observed.
            service.state.bypassed = active and not (name == "vision" and bypass[name]["source"] == "device")
            if active:
                service.state.last_error = ""
            elif previous and service.state.bypass_source != "device":
                service.state.online = False
                service.state.last_ok_ms = 0
                service.state.status = {}
                if name == "vision":
                    self.vision_service.snapshot_value = VisionSnapshot(online=False)
            service.state.bypass_source = bypass[name]["source"] if active else None
            self._last_bypass_state[name] = active
            if name == "vision" and active != previous:
                self.nutrunner_result = None
            if active and name != "laser":
                self._complete_bypassed_command(name)
            if initial or active == previous or not active or name == "laser":
                continue
            if name == "vision":
                self.nutrunner_result = None
                self.orchestrator.cancel_tracking()
            if self._started or service.state.online or service.state.status.get("moving") or service.state.status.get("tracking_active"):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    continue
                task = loop.create_task(self._stop_for_bypass(name))
                self._bypass_stop_tasks.add(task)
                task.add_done_callback(self._bypass_stop_tasks.discard)

    async def _stop_for_bypass(self, device: str) -> None:
        operations = [(self.ptm_service, "motion.stop"),
                      (self.laser_service, self.settings.laser.off_command)] if device == "ptm" else [
                          (self.vision_service, "tracking.stop")]
        for service, command in operations:
            if not service.state.enabled:
                if (device == "ptm" and service is self.laser_service
                        and self.ptm_service.state.enabled
                        and (self.settings.laser.mode.lower() in {"ptm", "gpio"}
                             or self.ptm_service.state.status.get("laser_on")
                             or self.ptm_service.state.status.get("laser_connected") is True)):
                    try:
                        await self.ptm_service.command("laser.off")
                    except Exception:
                        pass
                continue
            try:
                await service.command(command)
            except Exception:
                # A bypassed device may be absent; this cannot create a process alarm.
                pass

    def _process_communication_endpoints(self) -> dict[str, dict[str, Any]]:
        endpoints: dict[str, dict[str, Any]] = {}

        def add(config_key: str, host: str, port: int, timeout_s: float, command: str, params: dict[str, Any] | None = None) -> None:
            process_name = str(
                self.settings.processes.get(config_key, {}).get("process_name") or ""
            ).strip()
            if process_name:
                endpoints[process_name] = {
                    "host": host,
                    "port": port,
                    "timeout_s": timeout_s,
                    "command": command,
                }
                if params is not None:
                    endpoints[process_name]["params"] = params

        add(
            "plc_gateway",
            self.settings.xgt.control.host,
            self.settings.xgt.control.port,
            self.settings.xgt.control.timeout_s,
            "ping",
        )
        add(
            "ptm",
            self.settings.motor.host,
            self.settings.motor.port,
            self.settings.motor.timeout_s,
            "system.ping",
        )
        add(
            "vision",
            self.settings.vision.host,
            self.settings.vision.port,
            self.settings.vision.timeout_s,
            self.settings.vision.status_command,
            {},
        )
        core = self.settings.processes.get("core", {})
        if core:
            add("core", str(core["health_host"]), int(core["health_port"]), 2.0, "ping")
        return endpoints

    def _only_bypass_changed(self, settings: AppSettings) -> bool:
        return settings != self.settings and replace(
            settings,
            motor=replace(settings.motor, bypass=self.settings.motor.bypass),
            vision=replace(settings.vision, bypass=self.settings.vision.bypass),
        ) == self.settings

    async def _replace_settings(self, settings: AppSettings) -> None:
        async with self._settings_lock:
            if self._only_bypass_changed(settings):
                self.settings = settings
                self.ptm_service.settings = settings.motor
                self.vision_service.settings = settings.vision
                self._sync_device_snapshots()
                return
            self._gateway_update_in_progress = True
            try:
                await self._replace_settings_locked(settings)
            finally:
                self._gateway_update_in_progress = False
            if self._started and self._plc_enabled():
                self._gateway_config_task = asyncio.create_task(self._configure_gateway_on_start())

    async def _replace_settings_locked(self, settings: AppSettings) -> None:
        validate_discovery(settings.discovery)
        self._validate_operator_settings(settings)
        if self._power_laser_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._power_laser_task
        plc_was_enabled = self._plc_enabled()
        gateway_reconfigure = (
            settings.xgt.control != self.settings.xgt.control
            or plc_was_enabled != (settings.processes.get("plc_gateway", {}).get("enabled", True) is not False)
        )
        disabling_plc = plc_was_enabled and settings.processes.get(
            "plc_gateway", {}
        ).get("enabled", True) is False
        disabling_motor = self.ptm_service.state.enabled and not (
            settings.motor.enabled
            and settings.processes.get("ptm", {}).get("enabled", True)
        )
        disabling_laser = self.laser_service.state.enabled and not (
            settings.laser.enabled
            and (
                settings.laser.mode.lower() not in {"ptm", "gpio"}
                or (
                    settings.motor.enabled
                    and settings.processes.get("ptm", {}).get("enabled", True)
                )
            )
        )
        had_motion = self._active_motion is not None or (
            self.command.status == CommandStatus.BUSY
            and self.command.last_code in {CommandCode.TARGET_APPLY_MOVE, CommandCode.HOME}
        )
        ptm_bypass = settings.motor.bypass or getattr(self.plc_input, "ptm_bypass", False)
        vision_bypass = settings.vision.bypass or getattr(self.plc_input, "vision_bypass", False)
        activating_ptm = ptm_bypass and not self._last_bypass_state.get("ptm", False)
        activating_vision = vision_bypass and not self._last_bypass_state.get("vision", False)
        if ptm_bypass:
            self._complete_bypassed_command("ptm")
            if activating_ptm and (had_motion or self._started or self.ptm_service.state.online):
                await self._stop_for_bypass("ptm")
            self.ptm_service.state.bypassed = True
            self.laser_service.state.bypassed = True
        if vision_bypass:
            self._complete_bypassed_command("vision")
            if activating_vision and (self._started or self.vision_service.state.online):
                await self.orchestrator.stop()
                await self._stop_for_bypass("vision")
            self.vision_service.state.bypassed = True
        self.nutrunner_result = None
        if not (ptm_bypass and self.command.status == CommandStatus.COMPLETE
                and self.command.last_code in {CommandCode.TARGET_APPLY_MOVE, CommandCode.HOME}):
            self._abort_active_command(CommandResult.SYSTEM_NOT_READY)
        if self._safe_stop_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._safe_stop_task
            self._safe_stop_task = None
        if had_motion or disabling_plc or disabling_motor or disabling_laser:
            self._safe_stop_applied = await self._perform_safe_stop()
        if self._gateway_config_task is not None:
            self._gateway_config_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._gateway_config_task
            self._gateway_config_task = None
        restart_discovery = (
            settings.discovery != self.settings.discovery
            or settings.core.port != self.settings.core.port
            or settings.core.http_port != self.settings.core.http_port
        )
        if hasattr(self, "orchestrator"):
            await self.orchestrator.stop()
        old_devices = getattr(self, "devices", None)
        old_plc_memory = getattr(self, "plc_memory", None)
        if old_devices is not None:
            await old_devices.stop_all()
        if old_plc_memory is not None:
            old_plc_memory.close()
        if restart_discovery:
            await self.discovery.stop()

        self.settings = settings
        if activating_ptm:
            self._last_bypass_state["ptm"] = True
            self._last_bypass_state["laser"] = True
        if activating_vision:
            self._last_bypass_state["vision"] = True
        if gateway_reconfigure:
            self._gateway_config = {}
            self._gateway_config_error = ""
        if self._started:
            self._configure_logging()
        if restart_discovery:
            self.discovery = DiscoveryService(settings.discovery, settings.core.port,
                                              settings.core.http_port or settings.core.port)
        self._server.static_root = None if self._managed_worker else resolve_app_path(self.settings.paths.web_dir)
        self._server.allow_remote_process_control = settings.core.allow_remote_process_control
        if self._http_server is not None:
            self._http_server.static_root = self._server.static_root
            self._http_server.allow_remote_process_control = (
                settings.core.allow_remote_process_control
            )
        self._rebuild_clients()
        if gateway_reconfigure:
            self._gateway_configured = not self._plc_enabled()
        if plc_was_enabled != self._plc_enabled():
            self._last_plc_heartbeat = None
            self._last_plc_heartbeat_change_ms = now_ms()
        if not self._plc_enabled():
            self.plc_input = PlcInput()
            self.alarms.clear_device(DeviceSummaryBit.PLC_COMMUNICATION)
        self._sync_device_snapshots()
        if self._started:
            # Saving configuration must not block while starting every missing
            # process. Process lifecycle is controlled by the dedicated UI
            # buttons; reconcile here only applies retire/disable changes.
            await self.process_manager.reconcile(start_missing=False)
            await self.devices.start_all()
            if restart_discovery and not self._managed_worker:
                await self.discovery.start()

    async def start(self) -> None:
        self._validate_operator_settings(self.settings)
        self._configure_logging()
        await self.alarm_history.start()
        await self._server.start()
        self._server_task = asyncio.create_task(self._server.serve_forever())
        if self._http_server is not None:
            await self._http_server.start()
            self._http_server_task = asyncio.create_task(
                self._http_server.serve_forever()
            )
        await self.process_manager.reconcile()
        if self._plc_enabled():
            self._gateway_config_task = asyncio.create_task(self._configure_gateway_on_start())
        self.process_manager.start_watchdog()
        await self.devices.start_all()
        if not self._managed_worker:
            await self.discovery.start()
        self._run_task = asyncio.create_task(self._run_loop())
        self._started = True
        LOGGER.info(
            "Tracker Core started on %s:%s",
            self.settings.core.host,
            self.settings.core.port,
        )
        if self._http_server is not None:
            LOGGER.info(
                "Tracker Core Web started on http://%s:%s",
                self.settings.core.host,
                self.settings.core.http_port,
            )

    async def stop(self) -> None:
        was_started = self._started
        self._started = False
        self._stop_event.set()
        if self._gateway_config_task is not None:
            self._gateway_config_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._gateway_config_task
            self._gateway_config_task = None
        for task in (self._run_task, self._server_task, self._http_server_task, self._power_laser_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if was_started:
            self.plc_input = PlcInput()
            self._abort_active_command(CommandResult.SYSTEM_NOT_READY)
            if self._safe_stop_task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await self._safe_stop_task
            await self._perform_safe_stop()
        await self.devices.stop_all()
        await self.orchestrator.stop()
        if self._bypass_stop_tasks:
            await asyncio.gather(*self._bypass_stop_tasks, return_exceptions=True)
        await self.discovery.stop()
        await self.network_manager.cancel()
        with contextlib.suppress(Exception):
            await self.process_manager.shutdown()
        await self._server.stop()
        if self._http_server is not None:
            await self._http_server.stop()
        self.plc_memory.close()
        await self.alarm_history.close()
        if self._log_handler is not None:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler.close()
            self._log_handler = None

    async def wait_closed(self) -> None:
        await self._stop_event.wait()

    async def _configure_gateway_on_start(self) -> None:
        client = self.xgt_gateway
        for attempt in range(10):
            if (
                not self._plc_enabled() or self._stop_event.is_set()
                or self._gateway_update_in_progress or client is not self.xgt_gateway
            ):
                return
            try:
                config = await client.get_config()
                if client is not self.xgt_gateway or self._gateway_update_in_progress:
                    return
                self._apply_gateway_config(config)
                return
            except Exception as exc:
                self._gateway_config_error = str(exc) or type(exc).__name__
                if isinstance(exc, ValueError):
                    self._gateway_configured = False
                    self.plc_memory.close()
                if attempt == 9:
                    LOGGER.warning("PLC Gateway configuration read failed: %s", exc)
                    self.alarms.set_fault_word(
                        DeviceSummaryBit.PLC_COMMUNICATION,
                        1 << 0,
                        self.settings.runtime.fault_latch_enabled,
                    )
                else:
                    await asyncio.sleep(1)
        self._gateway_refresh_ms = now_ms() + 2000

    def _apply_gateway_config(self, config: dict[str, Any]) -> None:
        read = config.get("read") or {}
        write = config.get("write") or {}
        shm = config.get("shared_memory") or {}
        for name, area in (("read", read), ("write", write)):
            if not area.get("enabled") or area.get("byte_count") != BYTE_COUNT:
                raise ValueError(f"Gateway {name} area must be enabled with {BYTE_COUNT} bytes")
            if not isinstance(area.get("address"), str) or not area["address"].strip():
                raise ValueError(f"Gateway {name} address is missing")
        if not isinstance(shm.get("name"), str) or not shm["name"].strip():
            raise ValueError("Gateway shared memory name is missing")
        current = {"read": dict(read), "write": dict(write), "shared_memory": dict(shm)}
        if current != self._gateway_config:
            self.plc_memory.close()
            self.plc_memory = PlcSharedMemoryClient(shm["name"])
            self._reset_plc_write_tracking()
            self._last_plc_heartbeat = None
            self._last_plc_heartbeat_change_ms = now_ms()
            self.plc_input = PlcInput()
            self._gateway_config = current
        self._gateway_config_error = ""
        self._gateway_configured = True
        self._gateway_refresh_ms = now_ms() + 2000

    def _plc_area_value(self, area: str, key: str) -> Any:
        values = self._gateway_config.get(area, {})
        if key in values:
            return values[key]
        settings = self.settings.xgt.plc_area
        return getattr(settings, f"{area}_address") if key == "address" else settings.interval_ms

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            started = now_ms()
            try:
                await self._cycle()
                if self._last_cycle_error:
                    LOGGER.info("Core cycle recovered: %s", self._last_cycle_error)
                self._last_cycle_error = ""
            except Exception as exc:
                error = str(exc) or type(exc).__name__
                if error != self._last_cycle_error:
                    LOGGER.exception("core cycle failed")
                self._last_cycle_error = error
                self.alarms.set_fault_word(
                    DeviceSummaryBit.CONTROLLER,
                    1 << 0,
                    self.settings.runtime.fault_latch_enabled,
                )
            self._report_diagnostics()
            self.alarm_history.capture(self.alarms, self.settings.runtime.fault_latch_enabled)
            elapsed = now_ms() - started
            self._cycle_count += 1
            self._last_cycle_ms = now_ms()
            self._last_cycle_duration_ms = elapsed
            delay_ms = max(1, self.settings.core.cycle_interval_ms - elapsed)
            await asyncio.sleep(delay_ms / 1000)

    def _report_diagnostics(self) -> None:
        errors = {"plc.config": self._gateway_config_error,
                  "plc.read": self._plc_read_error, "plc.write": self._plc_write_error}
        for key, service in self.devices.services.items():
            state = service.state
            fault = state.status.get("fault_word") or state.status.get("fault")
            errors[key] = (state.last_error or (f"device fault: {fault}" if fault else "")) if state.enabled and not state.bypassed else ""
        summary = self.alarms.fault_summary(False)
        errors["alarms"] = f"active fault summary={summary}, code={self.alarms.primary_fault_code(False)}" if summary else ""
        for source, error in errors.items():
            previous = self._diagnostic_states.get(source, "")
            if error != previous:
                logger = logging.getLogger(f"core.{source}")
                if error:
                    logger.error("%s", error)
                elif previous:
                    logger.info("Recovered: %s", previous)
                self._diagnostic_states[source] = error

    async def _cycle(self) -> None:
        if (
            self._plc_enabled() and now_ms() >= self._gateway_refresh_ms
            and not self._gateway_update_in_progress
            and (self._gateway_config_task is None or self._gateway_config_task.done())
        ):
            self._gateway_config_task = asyncio.create_task(self._configure_gateway_on_start())
            self._gateway_refresh_ms = now_ms() + 2000
        if self.settings.runtime.orchestration_enabled:
            await self.orchestrator.run_cycle(self)
            return
        now = now_ms()
        self._read_plc_input(now)
        self._update_plc_heartbeat_alarm(now)
        self._sync_device_snapshots()
        self._update_power_save()
        if self.power_save.sleeping:
            self.orchestrator.tick(self.plc_input, power_saving=True)
        await self._apply_safe_stop_if_needed()
        await self._handle_plc_command()
        self._build_output(now)
        self._write_plc_output()

    def _laser_permitted(self) -> bool:
        return bool(self.plc_input.run_enable and self.plc_input.laser_enable
                    and not self.plc_input.force_stop and not self.power_save.sleeping
                    and not self._controller_restart_pending()
                    and not self.alarms.fault_summary(self.settings.runtime.fault_latch_enabled)
                    and self.laser_service.state.enabled and not self._device_bypassed("laser"))

    def _laser_manual_allowed(self) -> bool:
        return bool(not self.plc_input.force_stop and not self._controller_restart_pending()
                    and not self.alarms.fault_summary(self.settings.runtime.fault_latch_enabled)
                    and self.laser_service.state.enabled and not self._device_bypassed("laser"))

    def _laser_manual_active(self) -> bool:
        state = self.ptm_service.state
        return bool(state.online and state.status.get("raw", {}).get("laser", {}).get("manual_override")
                    and now_ms() - state.last_ok_ms <= 1000)

    def _set_laser_command_fault(self, error: Exception) -> None:
        # Transport loss belongs to the PTM alarm; only GPIO errors are laser faults.
        if "GPIO" in str(error).upper():
            self.alarms.set_fault_word(DeviceSummaryBit.LASER, 1 << 2,
                                       self.settings.runtime.fault_latch_enabled)

    def _update_power_save(self) -> None:
        plc = self.plc_input
        if plc.wake_up and not self._wake_up_previous:
            self.power_save.wake("plc.wake_up")
        self._wake_up_previous = plc.wake_up
        if (plc.command_code in {CommandCode.TARGET_APPLY_MOVE, CommandCode.NUTRUNNER_DATA_REQUEST}
                and (not self._command_seen or plc.command_seq != self.command.last_seq)
                and self.command.status != CommandStatus.BUSY):
            self.power_save.observe_request(plc.command_code, plc.command_seq,
                                            plc.req_recipe_id, plc.req_point_id)
        self.power_save.timeout_s = self.settings.runtime.power_save_timeout_s
        entered = self.power_save.tick(busy=bool(
            self.command.status == CommandStatus.BUSY or self._pending_request
            or self.ptm_service.state.status.get("moving")))
        if entered:
            LOGGER.info("PLC work requests idle: entering Vision/laser power saving")
            self._active_recipe_id = self._active_point_id = 0
        permitted = self._laser_permitted()
        laser = self.laser_service.state
        need_off = (entered or self._laser_permit_previous or
                    self.ptm_service.state.status.get("laser_on") or laser.status.get("on"))
        changed = self._power_sleeping_previous != self.power_save.sleeping
        raw_laser = self.ptm_service.state.status.get("raw", {}).get("laser", {})
        actual_sleep = raw_laser.get("power_saving")
        mismatch = isinstance(actual_sleep, bool) and actual_sleep != self.power_save.sleeping
        actual_manual_allowed = raw_laser.get("manual_allowed")
        mismatch |= isinstance(actual_manual_allowed, bool) and actual_manual_allowed != self._laser_manual_allowed()
        if ((changed or mismatch or (not permitted and need_off and not (
                self._laser_manual_active() and self._laser_manual_allowed()))) and laser.enabled and not laser.bypassed
                and now_ms() >= self._power_laser_retry_ms
                and (self._power_laser_task is None or self._power_laser_task.done())):
            self._power_laser_retry_ms = now_ms() + 1000
            self._power_laser_task = asyncio.create_task(self._power_laser_off())
            self._power_sleeping_previous = self.power_save.sleeping
        self._laser_permit_previous = permitted

    async def _power_laser_off(self) -> None:
        try:
            async with self._laser_control_lock:
                if self.settings.laser.mode.lower() in {"ptm", "gpio"}:
                    await self.laser_service.command("laser.power_save", {
                        "enabled": self.power_save.sleeping, "automatic_off": True,
                        "manual_allowed": self._laser_manual_allowed()})
                else:
                    await self.laser_service.command(self.settings.laser.off_command)
            self._report_safe_stop_result("laser", "")
        except Exception as exc:
            self._report_safe_stop_result("laser", str(exc))
            self._set_laser_command_fault(exc)

    def _read_plc_input(self, timestamp_ms: int) -> None:
        if not self._plc_enabled():
            self.plc_memory.close()
            self._plc_read_error = ""
            self.plc_input = PlcInput()
            self.alarms.clear_device(DeviceSummaryBit.PLC_COMMUNICATION)
            return
        if not self._gateway_configured:
            self._plc_read_error = ""
            self.plc_input = PlcInput()
            return
        try:
            if not self.plc_memory.connected:
                self.plc_memory.connect()
            header = self.plc_memory.header()
            if not header.status_flags & FLAG_PLC_CONNECTED:
                raise SharedMemoryError("Gateway reports PLC disconnected")
            if not header.status_flags & FLAG_LAST_READ_OK or not header.last_read_time_ns:
                raise ValueError("Gateway has no successful live PLC read")
            payload = self.plc_memory.read_plc_data()
            if len(payload) != BYTE_COUNT:
                raise ValueError(
                    f"PLC input length {len(payload)} != {BYTE_COUNT}"
                )
            self.plc_input = PlcInput.from_bytes(payload)
            self._monitor_plc_cache.update(
                name=self.plc_memory.connected_name or self.plc_memory.name,
                header=header.to_dict() if hasattr(header, "to_dict") else dict(vars(header)),
                read_words=list(self.plc_input.raw_words),
                read_sample_ms=timestamp_ms,
            )
            self._plc_read_error = ""
            self.alarms.set_fault_word(
                DeviceSummaryBit.PLC_COMMUNICATION,
                self.alarms.fault_word(
                    DeviceSummaryBit.PLC_COMMUNICATION, latch_enabled=False
                ) & (1 << 4),
                self.settings.runtime.fault_latch_enabled,
            )
            if self.plc_input.heartbeat != self._last_plc_heartbeat:
                self._last_plc_heartbeat = self.plc_input.heartbeat
                self._last_plc_heartbeat_change_ms = timestamp_ms
        except SharedMemoryError as exc:
            self._plc_read_error = str(exc) or type(exc).__name__
            LOGGER.debug("PLC shared memory read failed: %s", exc)
            self.plc_memory.close()
            self.plc_input = PlcInput()
            self.alarms.set_fault_word(
                DeviceSummaryBit.PLC_COMMUNICATION,
                self.alarms.fault_word(
                    DeviceSummaryBit.PLC_COMMUNICATION, latch_enabled=False
                ) | (1 << 0),
                self.settings.runtime.fault_latch_enabled,
            )
        except Exception as exc:
            LOGGER.debug("PLC read failed: %s", exc)
            self._plc_read_error = str(exc) or type(exc).__name__
            self.plc_input = PlcInput()
            self.alarms.set_fault_word(
                DeviceSummaryBit.PLC_COMMUNICATION,
                self.alarms.fault_word(
                    DeviceSummaryBit.PLC_COMMUNICATION, latch_enabled=False
                ) | (1 << 3),
                self.settings.runtime.fault_latch_enabled,
            )

    def _update_plc_heartbeat_alarm(self, timestamp_ms: int) -> None:
        if not self._plc_enabled():
            self.alarms.clear_device(DeviceSummaryBit.PLC_COMMUNICATION)
            return
        if not self._gateway_configured:
            return
        if self._last_plc_heartbeat is None:
            self.alarms.set_warning_word(DeviceSummaryBit.PLC_COMMUNICATION, 0)
            return
        elapsed = timestamp_ms - self._last_plc_heartbeat_change_ms
        warning = 1 << 3 if elapsed >= self.settings.runtime.plc_heartbeat_warn_ms else 0
        heartbeat_fault = (
            1 << 1 if elapsed >= self.settings.runtime.plc_heartbeat_fault_ms else 0
        )
        current_fault = self.alarms.fault_word(
            DeviceSummaryBit.PLC_COMMUNICATION,
            latch_enabled=False,
        )
        disconnected_or_read_fault = current_fault & ~(1 << 1)
        self.alarms.set_warning_word(DeviceSummaryBit.PLC_COMMUNICATION, warning)
        self.alarms.set_fault_word(
            DeviceSummaryBit.PLC_COMMUNICATION,
            disconnected_or_read_fault | heartbeat_fault,
            self.settings.runtime.fault_latch_enabled,
        )
        if heartbeat_fault:
            self.plc_input = PlcInput()

    def _sync_device_snapshots(self) -> None:
        self._sync_bypass_state()
        self._apply_ptm_alarms()
        self._apply_laser_alarms()

        if self._device_bypassed("vision"):
            self._vision_result_missing_since_ms = None
            source = self.vision_service.snapshot_value
            self.vision_snapshot = VisionSnapshot(
                online=source.online, camera_ip=source.camera_ip,
                tracking_bypass=source.tracking_bypass,
                tracking_bypass_reason=source.tracking_bypass_reason,
            )
            return
        if not self.vision_service.state.enabled:
            self.vision_snapshot = VisionSnapshot(online=False)
            self._vision_result_missing_since_ms = None
            self._clear_vision_alarms()
            return

        vision_state = self.vision_service.state
        self.vision_snapshot = self.vision_service.snapshot_value
        stale = bool(vision_state.last_ok_ms and now_ms() - vision_state.last_ok_ms >
                     self.settings.runtime.vision_status_max_age_ms)
        transport_failed = bool(vision_state.last_error or (not vision_state.online and not vision_state.last_ok_ms))
        if (vision_state.ok_count or vision_state.error_count) and (transport_failed or stale):
            self.vision_snapshot = VisionSnapshot(online=False)
            self._vision_result_missing_since_ms = None
            self.alarms.set_fault_word(
                DeviceSummaryBit.VISION_COMMON,
                1 << 5,
                self.settings.runtime.fault_latch_enabled,
            )
            return
        self._apply_vision_alarms(self.vision_snapshot)

    def _apply_ptm_alarms(self) -> None:
        if self._device_bypassed("ptm"):
            return
        if not self.ptm_service.state.enabled:
            self.alarms.clear_device(DeviceSummaryBit.PAN_TILT_MOTOR)
            self.alarms.clear_device(DeviceSummaryBit.TILT_MOTOR)
            return

        state = self.ptm_service.state
        status = state.status
        warning = _safe_int(status.get("warning_word"))
        fault = _safe_int(status.get("fault_word"))
        if status.get("fault"):
            fault |= 1 << 1
        if (state.ok_count or state.error_count) and (not state.online or status.get("connected") is False):
            fault |= 1 << 0
        self.alarms.set_warning_word(DeviceSummaryBit.PAN_TILT_MOTOR, warning)
        self.alarms.set_fault_word(
            DeviceSummaryBit.PAN_TILT_MOTOR,
            fault,
            self.settings.runtime.fault_latch_enabled,
        )

    def _apply_laser_alarms(self) -> None:
        if self._device_bypassed("ptm"):
            return
        if not self.laser_service.state.enabled:
            self.alarms.clear_device(DeviceSummaryBit.LASER)
            return

        state = self.laser_service.state
        status = state.status
        warning = _safe_int(status.get("warning_word"))
        fault = _safe_int(status.get("fault_word"))
        if status.get("fault"):
            fault |= 1 << 2  # GPIO initialization/write/readback error, not communication.
        fault &= ~1
        if self.alarms.last_fault_code == 5001:
            self.alarms.last_fault_code = 0
        self.alarms.set_warning_word(DeviceSummaryBit.LASER, warning)
        self.alarms.set_fault_word(
            DeviceSummaryBit.LASER,
            fault,
            self.settings.runtime.fault_latch_enabled,
        )

    def _clear_vision_alarms(self) -> None:
        for device in (
            DeviceSummaryBit.VISION_COMMON,
            DeviceSummaryBit.TRACKER,
            DeviceSummaryBit.CAMERA_1,
            DeviceSummaryBit.CAMERA_2,
            DeviceSummaryBit.CAMERA_3,
            DeviceSummaryBit.CAMERA_4,
        ):
            self.alarms.clear_device(device)

    def _apply_vision_alarms(self, snapshot: VisionSnapshot) -> None:
        self.alarms.set_warning_word(
            DeviceSummaryBit.VISION_COMMON,
            snapshot.vision_warning_word | (1 << 2 if snapshot.degraded else 0),
        )
        required_missing = bool(snapshot.camera_required_mask & ~(snapshot.camera_online_mask & snapshot.camera_valid_mask))
        vision_fault = snapshot.vision_fault_word
        tracking_requested = self.plc_input.tracking_enable or snapshot.tracking_active
        tracker_present = snapshot.tracker_present is True or (snapshot.tracker_present is None and snapshot.tracker_valid)
        result_expected = tracking_requested and tracker_present and not required_missing
        timestamp_ms = now_ms()
        result_timeout_ms = self.settings.runtime.vision_status_max_age_ms
        if not result_expected:
            self._vision_result_missing_since_ms = None
            vision_fault &= ~(1 << 5)
        elif snapshot.result_ready is False:
            if getattr(self, "_vision_result_missing_since_ms", None) is None:
                self._vision_result_missing_since_ms = timestamp_ms
            if timestamp_ms - self._vision_result_missing_since_ms > result_timeout_ms:
                vision_fault |= 1 << 5
        else:
            self._vision_result_missing_since_ms = None
            if snapshot.result_ready is True:
                vision_fault &= ~(1 << 5)
        if result_expected and snapshot.result_age_seconds is not None:
            if snapshot.result_age_seconds * 1000 > result_timeout_ms:
                vision_fault |= 1 << 5
        if required_missing:
            vision_fault |= 1 << 0
        if snapshot.fault:
            vision_fault |= 1 << 2
        self.alarms.set_fault_word(
            DeviceSummaryBit.VISION_COMMON,
            vision_fault,
            self.settings.runtime.fault_latch_enabled,
        )

        tracker_fault = snapshot.tracker_fault_word & ~1
        tracker_warning = snapshot.tracker_warning_word
        if (snapshot.tracker_fault_word & 1 or
                tracking_requested and (snapshot.tracker_present is False or
                                        snapshot.tracker_present is None and not snapshot.tracker_valid)):
            tracker_warning |= 1 << 4
        self.alarms.set_warning_word(
            DeviceSummaryBit.TRACKER,
            tracker_warning,
        )
        self.alarms.set_fault_word(
            DeviceSummaryBit.TRACKER,
            tracker_fault,
            self.settings.runtime.fault_latch_enabled,
        )

        self.alarms.set_warning_word(
            DeviceSummaryBit.CAMERA_1,
            snapshot.camera_warning_words.get(1, 0),
        )
        camera_fault = snapshot.camera_fault_words.get(1, 0)
        if snapshot.camera_required_mask & 1 and not snapshot.camera_online_mask & 1:
            camera_fault |= 1 << 0
        self.alarms.set_fault_word(
            DeviceSummaryBit.CAMERA_1,
            camera_fault,
            self.settings.runtime.fault_latch_enabled,
        )
        for device in (
            DeviceSummaryBit.CAMERA_2,
            DeviceSummaryBit.CAMERA_3,
            DeviceSummaryBit.CAMERA_4,
        ):
            self.alarms.clear_device(device)

    async def _apply_safe_stop_if_needed(self) -> None:
        safe_stop_requested = (
            self.plc_input.force_stop
            or not self.plc_input.run_enable
            or bool(self.alarms.fault_summary(self.settings.runtime.fault_latch_enabled))
            or self._controller_restart_pending()
        )
        if not safe_stop_requested:
            self._safe_stop_applied = False
            self._safe_stop_retry_ms = 0
            return
        self._active_recipe_id = 0
        self._active_point_id = 0
        if (
            self.command.status in {CommandStatus.RECEIVED, CommandStatus.BUSY}
            and self.command.last_code not in {CommandCode.ERROR_RESET, CommandCode.CONTROLLER_RESTART}
            and (self.command.last_code != CommandCode.NUTRUNNER_DATA_REQUEST
                 or self.plc_input.force_stop or not self.plc_input.run_enable)
        ):
            result = (
                CommandResult.FORCE_STOP_ACTIVE if self.plc_input.force_stop
                else CommandResult.SYSTEM_NOT_READY
            )
            self._abort_active_command(result)
        if self._safe_stop_task is not None:
            if not self._safe_stop_task.done():
                return
            self._safe_stop_applied = self._safe_stop_task.result()
            self._safe_stop_task = None
        ptm_status = self.ptm_service.state.status
        laser_status = self.laser_service.state.status
        preserve_manual = self._laser_manual_allowed()
        still_active = bool(
            ptm_status.get("moving") or ((ptm_status.get("laser_on") or laser_status.get("on"))
                and not (preserve_manual and self._laser_manual_active()))
        )
        if self._safe_stop_applied and not still_active:
            return
        if now_ms() < self._safe_stop_retry_ms:
            return
        self._safe_stop_retry_ms = now_ms() + 1000
        self._safe_stop_task = asyncio.create_task(
            self._perform_safe_stop(preserve_manual=True) if preserve_manual else self._perform_safe_stop())

    def _controller_restart_pending(self) -> bool:
        return self._controller_restart_request is not None or (
            self.command.last_code == CommandCode.CONTROLLER_RESTART
            and self.command.status in {CommandStatus.RECEIVED, CommandStatus.BUSY}
        )

    async def _request_controller_restart(self, plc_command_seq: int | None = None) -> dict[str, Any]:
        if not self._managed_worker:
            raise RuntimeError("Main Core restart requires the management server")
        if plc_command_seq is None and self.command.last_code == CommandCode.CONTROLLER_RESTART and self.command.status in {
            CommandStatus.RECEIVED, CommandStatus.BUSY,
        }:
            plc_command_seq = self.command.last_seq
        if self._controller_restart_request is not None:
            return self._controller_restart_request
        request = {"requested_ms": now_ms(), "plc_command_seq": plc_command_seq, "ready": False}
        self._controller_restart_request = request
        try:
            await self._apply_safe_stop_if_needed()
            safe = await asyncio.shield(self._safe_stop_task) if self._safe_stop_task is not None else self._safe_stop_applied
            if not safe:
                raise RuntimeError("Safe stop could not be confirmed; Main Core restart cancelled")
            self.orchestrator.cancel_tracking()
            request["ready"] = True
            return request
        except BaseException:
            if self._controller_restart_request is request:
                self._controller_restart_request = None
            raise

    def _abort_active_command(self, result: CommandResult) -> None:
        if self._command_task is not None and not self._command_task.done():
            self._command_task.cancel()
        self._command_task = None
        self._command_request_params = {}
        self._pending_request = None
        self._active_motion = None
        self._active_recipe_id = 0
        self._active_point_id = 0
        if self.command.status in {CommandStatus.RECEIVED, CommandStatus.BUSY}:
            self.command.status = CommandStatus.ERROR
            self.command.result = result

    async def _perform_safe_stop(self, *, preserve_manual: bool = False) -> bool:
        success = True
        if self.ptm_service.state.enabled and not self.ptm_service.state.bypassed:
            try:
                if preserve_manual and self._laser_manual_allowed():
                    await self.ptm_service.command("motion.stop", {"preserve_laser_manual": True})
                else:
                    await self.ptm_service.command("motion.stop")
                self._report_safe_stop_result("motor", "")
            except Exception as exc:
                if not self.ptm_service.state.bypassed:
                    success = False
                    self._report_safe_stop_result("motor", str(exc))
                    self.alarms.set_fault_word(
                        DeviceSummaryBit.PAN_TILT_MOTOR,
                        1 << 0,
                        self.settings.runtime.fault_latch_enabled,
                    )
        if not self.laser_service.state.bypassed and (
            self.laser_service.state.enabled
            or self.ptm_service.state.status.get("laser_on")
            or (
                self.ptm_service.state.enabled
                and self.ptm_service.state.status.get("laser_connected") is True
            )
        ):
            try:
                if self.laser_service.state.enabled:
                    if self.settings.laser.mode.lower() in {"ptm", "gpio"}:
                        if preserve_manual and self._laser_manual_allowed():
                            await self.laser_service.command("laser.power_save", {
                                "enabled": self.power_save.sleeping, "automatic_off": True, "manual_allowed": True})
                        else:
                            try:
                                await self.laser_service.command("laser.arm", {"enabled": False})
                            finally:
                                await self.laser_service.command(self.settings.laser.off_command)
                    else:
                        await self.laser_service.command(self.settings.laser.off_command)
                else:
                    await self.ptm_service.command("laser.off")
                self._report_safe_stop_result("laser", "")
            except Exception as exc:
                if not self.laser_service.state.bypassed:
                    success = False
                    self._report_safe_stop_result("laser", str(exc))
                    self._set_laser_command_fault(exc)
        return success

    def _report_safe_stop_result(self, device: str, error: str) -> None:
        previous = self._safe_stop_errors.get(device, "")
        if error == previous:
            return
        if error:
            operation = "motor stop" if device == "motor" else "laser off"
            LOGGER.error("%s failed during safe stop: %s", operation, error)
        elif previous:
            LOGGER.info("%s safe stop recovered: %s", device, previous)
        self._safe_stop_errors[device] = error

    async def _handle_plc_command(self) -> None:
        self._sync_bypass_state()
        self._progress_motion_command(now_ms())
        restart_expired = bool(self._controller_restart_request and
                               now_ms() - self._controller_restart_request["requested_ms"] >
                               self.settings.runtime.command_timeout_s * 1000)
        if restart_expired or (self.command.last_code == CommandCode.CONTROLLER_RESTART
                and self.command.status == CommandStatus.BUSY
                and now_ms() - self.command.busy_since_ms > self.settings.runtime.command_timeout_s * 1000):
            self._controller_restart_request = None
            if self.command.last_code == CommandCode.CONTROLLER_RESTART and self.command.status == CommandStatus.BUSY:
                self._abort_active_command(CommandResult.INTERNAL_ERROR)
                self.command.message = "Main Core restart request timed out"
            LOGGER.warning("Main Core restart request timed out")
        if self._controller_restart_request is not None:
            return
        if self._pending_request is not None:
            code, seq, expected, params = self._pending_request
            self._pending_request = None
            self.command.status = CommandStatus.BUSY
            self._command_request_params = params
            self._command_task = asyncio.create_task(
                self._dispatch_plc_command(code, seq, expected, params)
            )
            return
        code = self.plc_input.command_code
        seq = self.plc_input.command_seq
        if code == 0 or (self._command_seen and seq == self.command.last_seq):
            return
        if self.command.status == CommandStatus.BUSY:
            return

        self.command.last_code = code
        self.command.last_seq = seq
        self._command_request_params = {}
        self._command_seen = True
        self.command.status = CommandStatus.RECEIVED
        self.command.result = CommandResult.OK
        self.command.message = ""
        self.command.busy_since_ms = now_ms()
        if code == CommandCode.NUTRUNNER_DATA_REQUEST:
            self.nutrunner_result = None

        if code not in {
            CommandCode.TARGET_APPLY_MOVE, CommandCode.HOME, CommandCode.ERROR_RESET,
            CommandCode.CONTROLLER_RESTART, CommandCode.NUTRUNNER_DATA_REQUEST,
        }:
            self.command.status = CommandStatus.REJECTED
            self.command.result = CommandResult.INVALID_COMMAND
            return
        if code not in {CommandCode.ERROR_RESET, CommandCode.CONTROLLER_RESTART}:
            if self.plc_input.force_stop:
                self.command.status = CommandStatus.REJECTED
                self.command.result = CommandResult.FORCE_STOP_ACTIVE
                return
            if not self.plc_input.run_enable:
                self.command.status = CommandStatus.REJECTED
                self.command.result = CommandResult.SYSTEM_NOT_READY
                return
            if code == CommandCode.NUTRUNNER_DATA_REQUEST:
                if self._device_bypassed("vision"):
                    if self.alarms.fault_summary(self.settings.runtime.fault_latch_enabled):
                        self.command.status = CommandStatus.REJECTED
                        self.command.result = CommandResult.FAULT_ACTIVE
                        return
                    self._complete_bypassed_command("vision")
                    return
                self._prepare_nutrunner_request(code, seq)
                return
            if self._device_bypassed("ptm"):
                if self.alarms.fault_summary(self.settings.runtime.fault_latch_enabled):
                    self.command.status = CommandStatus.REJECTED
                    self.command.result = CommandResult.FAULT_ACTIVE
                    return
                self._complete_bypassed_command("ptm")
                return
            if self.alarms.fault_summary(self.settings.runtime.fault_latch_enabled):
                self.command.status = CommandStatus.REJECTED
                self.command.result = CommandResult.FAULT_ACTIVE
                return
            if ((self._safe_stop_task is not None and not self._safe_stop_task.done())
                    or any(not task.done() for task in self._bypass_stop_tasks)):
                self.command.status = CommandStatus.REJECTED
                self.command.result = CommandResult.SYSTEM_NOT_READY
                return
            if (
                not self.ptm_service.state.online
                or not self.ptm_service.state.status.get("connected")
                or not self.ptm_service.state.status.get("ready", True)
            ):
                self.command.status = CommandStatus.REJECTED
                self.command.result = CommandResult.SYSTEM_NOT_READY
                return
            if (
                code == CommandCode.TARGET_APPLY_MOVE
                and not self.ptm_service.state.status.get("homed")
            ):
                self.command.status = CommandStatus.REJECTED
                self.command.result = CommandResult.HOMING_REQUIRED
                return

        entry = self.settings.command_map.get(str(code))
        expected = {
            CommandCode.TARGET_APPLY_MOVE: "point.goto",
            CommandCode.HOME: "home.start",
            CommandCode.ERROR_RESET: "alarm.reset",
            CommandCode.CONTROLLER_RESTART: "controller.restart_request",
        }[CommandCode(code)]
        if (
            entry is None or entry.get("command") != expected
            or entry.get("target") != ("core" if code in {CommandCode.ERROR_RESET, CommandCode.CONTROLLER_RESTART} else "motor")
        ):
            self.command.status = CommandStatus.REJECTED
            self.command.result = CommandResult.INVALID_COMMAND
            return
        params = self._resolve_params(entry.get("params") or {})
        if code == CommandCode.TARGET_APPLY_MOVE and (
            not params.get("recipe_id") or not params.get("point_id")
        ):
            self.command.status = CommandStatus.REJECTED
            self.command.result = (
                CommandResult.RECIPE_NOT_FOUND if not params.get("recipe_id")
                else CommandResult.POINT_NOT_FOUND
            )
            return
        self._pending_request = (code, seq, expected, params)
        if code in {CommandCode.TARGET_APPLY_MOVE, CommandCode.HOME}:
            self._active_recipe_id = 0
            self._active_point_id = 0

    def _prepare_nutrunner_request(self, code: int, seq: int) -> None:
        recipe_id, point_id = self.plc_input.req_recipe_id, self.plc_input.req_point_id
        if not 1 <= recipe_id <= 65535:
            self.command.status = CommandStatus.REJECTED
            self.command.result = CommandResult.RECIPE_NOT_FOUND
            return
        if not 1 <= point_id <= 65535:
            self.command.status = CommandStatus.REJECTED
            self.command.result = CommandResult.POINT_NOT_FOUND
            return
        entry = self.settings.command_map.get(str(code))
        if entry is None or entry.get("target") != "vision" or entry.get("command") != "vision.result":
            self.command.status = CommandStatus.REJECTED
            self.command.result = CommandResult.INVALID_COMMAND
            return
        if not self.vision_service.state.enabled or not self.vision_service.state.online:
            self.command.status = CommandStatus.REJECTED
            self.command.result = CommandResult.TRACKING_UNAVAILABLE
            return
        self._pending_request = (code, seq, "vision.result", {"recipe_id": recipe_id, "point_id": point_id})

    async def _dispatch_plc_command(
        self, code: int, seq: int, command: str, params: dict[str, Any]
    ) -> None:
        preparing_laser = False
        try:
            if code in {CommandCode.ERROR_RESET, CommandCode.NUTRUNNER_DATA_REQUEST} and self._laser_manual_active():
                try:
                    await self.laser_service.command(self.settings.laser.off_command)
                except Exception as exc:
                    self._report_safe_stop_result("laser", str(exc))
                    self._set_laser_command_fault(exc)
            if code == CommandCode.ERROR_RESET:
                self.alarm_history.capture(self.alarms, self.settings.runtime.fault_latch_enabled)
                self.alarms.reset_latched_faults()
                self.alarm_history.capture(self.alarms, self.settings.runtime.fault_latch_enabled, reset=True)
                self.command.status = CommandStatus.COMPLETE
                return
            if code == CommandCode.CONTROLLER_RESTART:
                await self._request_controller_restart(seq)
                self.command.message = "Main Core restart requested; awaiting management server"
                return
            if code == CommandCode.NUTRUNNER_DATA_REQUEST:
                response = await self.vision_service.request_nutrunner_data(
                    int(params["recipe_id"]), int(params["point_id"]), seq,
                )
                if seq != self.command.last_seq or self.command.status != CommandStatus.BUSY:
                    return
                if self._device_bypassed("vision"):
                    self._complete_bypassed_command("vision")
                    return
                if not response["supported"] or not response["data_valid"]:
                    self.nutrunner_result = response
                    self.command.status = CommandStatus.REJECTED
                    self.command.result = CommandResult.TRACKING_UNAVAILABLE
                    self.command.message = "Vision tightening data is not available"
                    return
                self.nutrunner_result = response
                self.command.status = CommandStatus.COMPLETE
                self.command.result = CommandResult.OK
                return
            if code == CommandCode.TARGET_APPLY_MOVE and self.laser_service.state.enabled:
                async with self._laser_control_lock:
                    preparing_laser = True
                    await self.laser_service.command(self.settings.laser.off_command)
                    permitted = self._laser_permitted()
                    move_params = {**params, "laser_after_move": permitted}
                    if self.settings.laser.mode.lower() in {"ptm", "gpio"}:
                        await self.laser_service.command("laser.power_save", {"enabled": self.power_save.sleeping})
                        arm = await self.laser_service.command("laser.arm", {"enabled": permitted})
                        if isinstance(arm, dict) and "permit_token" in arm:
                            move_params["laser_permit_token"] = arm["permit_token"]
                    preparing_laser = False
                    response = await self.ptm_service.command(command, move_params)
            else:
                response = await self.ptm_service.command(command, {
                    **params, **({"laser_after_move": False} if code == CommandCode.TARGET_APPLY_MOVE else {})})
            if seq != self.command.last_seq or self.command.status != CommandStatus.BUSY:
                return
            motion_id = response.get("motion_id") if isinstance(response, dict) else None
            if not motion_id:
                raise ValueError(
                    "PTM response has no motion_id; movement completion cannot be verified"
                )
            self._active_motion = {
                "id": motion_id,
                "code": code,
                "seq": seq,
                "params": params,
                "accepted_ms": now_ms(),
            }
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if seq == self.command.last_seq and self.command.status == CommandStatus.BUSY:
                LOGGER.warning("PLC command %s/%s failed: %s", code, seq, exc)
                self.command.status = CommandStatus.ERROR
                self.command.message = str(exc)
                if code == CommandCode.CONTROLLER_RESTART:
                    self._controller_restart_request = None
                    self.command.result = CommandResult.INTERNAL_ERROR
                    return
                if code == CommandCode.NUTRUNNER_DATA_REQUEST:
                    self.nutrunner_result = None
                    vision_error = getattr(exc, "code", None)
                    if vision_error in {"RECIPE_NOT_FOUND", "POINT_NOT_FOUND"}:
                        self.command.status = CommandStatus.REJECTED
                        self.command.result = (
                            CommandResult.RECIPE_NOT_FOUND if vision_error == "RECIPE_NOT_FOUND"
                            else CommandResult.POINT_NOT_FOUND
                        )
                    else:
                        self.command.result = CommandResult.TRACKING_UNAVAILABLE
                    return
                if "unknown recipe" in self.command.message.lower():
                    self.command.result = CommandResult.RECIPE_NOT_FOUND
                elif "unknown point" in self.command.message.lower():
                    self.command.result = CommandResult.POINT_NOT_FOUND
                else:
                    self.command.result = CommandResult.INTERNAL_ERROR
                    if preparing_laser:
                        self._set_laser_command_fault(exc)
                    else:
                        self.alarms.set_fault_word(DeviceSummaryBit.PAN_TILT_MOTOR, 1,
                                                   self.settings.runtime.fault_latch_enabled)

    def _progress_motion_command(self, timestamp_ms: int) -> None:
        motion = self._active_motion
        if motion is None or self.command.status != CommandStatus.BUSY:
            return
        state = self.ptm_service.state
        status = state.status
        raw_motion = status.get("raw", {}).get("motion", {})
        fresh = state.online and state.last_ok_ms >= motion["accepted_ms"]
        if fresh and raw_motion.get("completed_id") == motion["id"]:
            if motion["code"] == CommandCode.HOME and not status.get("homed"):
                self.command.status = CommandStatus.ERROR
                self.command.result = CommandResult.HOMING_REQUIRED
                self._set_motion_fault(2)
            else:
                self.command.status = CommandStatus.COMPLETE
                self.command.result = CommandResult.OK
                if motion["code"] == CommandCode.TARGET_APPLY_MOVE:
                    self._active_recipe_id = int(motion["params"]["recipe_id"])
                    self._active_point_id = int(motion["params"]["point_id"])
            self._active_motion = None
            return
        if fresh and raw_motion.get("failed_id") == motion["id"]:
            self.command.status = CommandStatus.ERROR
            self.command.result = (
                CommandResult.MOTION_TIMEOUT
                if raw_motion.get("last_error") == "MOTION_TIMEOUT"
                else CommandResult.INTERNAL_ERROR
            )
            self.command.message = str(raw_motion.get("last_error") or "motion failed")
            fault_bit = (
                3 if self.command.result == CommandResult.MOTION_TIMEOUT
                else 2 if motion["code"] == CommandCode.HOME else 6
            )
            if str(raw_motion.get("last_error", "")).startswith("LASER_"):
                self.alarms.set_fault_word(DeviceSummaryBit.LASER, 1 << 2,
                                           self.settings.runtime.fault_latch_enabled)
            else:
                self._set_motion_fault(fault_bit)
            self._active_motion = None
            return
        ptm_timeout = (
            status.get("raw", {}).get("motion", {})
            .get("completion", {}).get("timeout_s", 0)
        )
        timeout_ms = int(
            max(self.settings.runtime.command_timeout_s, float(ptm_timeout) + 5) * 1000
        )
        if timestamp_ms - self.command.busy_since_ms >= timeout_ms:
            self.command.status = CommandStatus.ERROR
            self.command.result = CommandResult.MOTION_TIMEOUT
            self.command.message = "PTM movement completion timed out"
            self._active_motion = None
            self._set_motion_fault(2 if motion["code"] == CommandCode.HOME else 3)
            self._safe_stop_applied = False

    def _set_motion_fault(self, bit_index: int) -> None:
        self.alarms.set_fault_word(
            DeviceSummaryBit.PAN_TILT_MOTOR, 1 << bit_index,
            self.settings.runtime.fault_latch_enabled,
        )

    def _resolve_params(self, params: dict[str, Any]) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        for key, value in params.items():
            if value == "$req_recipe_id":
                resolved[key] = self.plc_input.req_recipe_id
            elif value == "$req_point_id":
                resolved[key] = self.plc_input.req_point_id
            else:
                resolved[key] = value
        return resolved

    def _build_output(self, timestamp_ms: int) -> None:
        bypass = self._bypass_status()
        ptm_bypassed = bypass["ptm"]["active"]
        vision_bypassed = bypass["vision"]["active"]
        fault_active = self.alarms.fault_summary(self.settings.runtime.fault_latch_enabled) != 0
        restart_pending = self._controller_restart_pending()
        process_permitted = self.plc_input.run_enable and not self.plc_input.force_stop and not fault_active and not restart_pending
        tracking_tolerance = self.plc_input.applied_tracking_tolerance(
            self.settings.runtime.tracking_tolerance_default_mm_x100
        )
        ptm_status = self.ptm_service.state.status if self.ptm_service.state.online and not bypass["ptm"]["active"] else {}
        laser_status = self.laser_service.state.status if self.laser_service.state.online and not bypass["laser"]["active"] else {}
        laser_tolerance = self.plc_input.applied_laser_tolerance(
            self.settings.runtime.laser_tolerance_default_deg_x100
        )
        position_error_x100 = _scale_or_zero(self.vision_snapshot.position_error_mm)
        pan_error_x100 = _scale_or_zero(self.vision_snapshot.pan_error_deg)
        tilt_error_x100 = _scale_or_zero(self.vision_snapshot.tilt_error_deg)

        position_ok = self.plc_input.tracking_enable and not restart_pending and (
            process_permitted if vision_bypassed else (
                self.vision_snapshot.tracker_valid
                and self.vision_snapshot.tracker_present is not False
                and self.vision_snapshot.result_ready is not False
                and self.vision_snapshot.position_error_mm is not None
                and math.isfinite(self.vision_snapshot.position_error_mm)
                and position_error_x100 <= tracking_tolerance
            )
        )
        if position_ok:
            if self._position_ok_since_ms is None:
                self._position_ok_since_ms = timestamp_ms
        else:
            self._position_ok_since_ms = None
        position_stable = (vision_bypassed and position_ok) or (
            self._position_ok_since_ms is not None
            and timestamp_ms - self._position_ok_since_ms
            >= self.settings.runtime.position_stable_ms
        )
        laser_target_ready = (
            (bypass["laser"]["active"] or self.laser_service.state.enabled)
            and (vision_bypassed or self.vision_service.state.enabled)
            and position_stable
            and self._active_recipe_id > 0
            and self._active_point_id > 0
            and process_permitted
            and (vision_bypassed or ptm_bypassed or (
                self.vision_snapshot.pan_error_deg is not None
                and self.vision_snapshot.tilt_error_deg is not None
                and math.isfinite(self.vision_snapshot.pan_error_deg)
                and math.isfinite(self.vision_snapshot.tilt_error_deg)
                and abs(self.vision_snapshot.pan_error_deg) <= laser_tolerance / 100
                and abs(self.vision_snapshot.tilt_error_deg) <= laser_tolerance / 100
            ))
            and (ptm_bypassed or (
                self.ptm_service.state.online
                and bool(ptm_status.get("connected", True))
                and bool(ptm_status.get("ready", True))
                and bool(ptm_status.get("in_position", True))
                and not ptm_status.get("moving", False)
            ))
        )

        motor_ready = ptm_bypassed or (
            self.ptm_service.state.enabled
            and self.ptm_service.state.online
            and bool(ptm_status.get("connected"))
            and bool(ptm_status.get("ready", True))
        )
        system_ready = (
            process_permitted and motor_ready
            and (self._safe_stop_task is None or self._safe_stop_task.done())
            and not any(not task.done() for task in self._bypass_stop_tasks)
        )
        tracking_active = (
            self.plc_input.tracking_enable and process_permitted if vision_bypassed
            else self.vision_service.state.enabled and not restart_pending and self.vision_snapshot.tracking_active
        )
        if fault_active:
            controller_state = ControllerState.FAULT
        elif self.plc_input.force_stop or not self.plc_input.run_enable or restart_pending:
            controller_state = ControllerState.STOPPED
        elif self.command.status == CommandStatus.BUSY and self.command.last_code == CommandCode.HOME:
            controller_state = ControllerState.HOMING
        elif self.command.status == CommandStatus.BUSY and self.command.last_code == CommandCode.TARGET_APPLY_MOVE:
            controller_state = ControllerState.TARGET_MOVING
        elif self.plc_input.tracking_enable and tracking_active:
            controller_state = ControllerState.TRACKING
        elif system_ready:
            controller_state = ControllerState.READY
        else:
            controller_state = ControllerState.STANDBY

        tracking_state = TrackingState.OFF
        if self.plc_input.tracking_enable and not restart_pending and (vision_bypassed or self.vision_service.state.enabled):
            if fault_active:
                tracking_state = TrackingState.FAULT
            elif vision_bypassed:
                tracking_state = TrackingState.TRACKING if tracking_active else TrackingState.OFF
            elif self.vision_snapshot.tracking_active and self.vision_snapshot.tracker_valid:
                tracking_state = TrackingState.TRACKING
            elif self.vision_snapshot.degraded:
                tracking_state = TrackingState.DEGRADED
            else:
                tracking_state = TrackingState.SEARCHING

        self.output = PlcOutput(
            flags=ProcessFlags(
                controller_online=True,
                system_ready=system_ready,
                tracking_active=tracking_active,
                homing=controller_state == ControllerState.HOMING or bool(ptm_status.get("homing")),
                motion_moving=bool(ptm_status.get("moving") and ptm_status.get("connected", True)),
                laser_on=bool(laser_status.get("on", ptm_status.get("laser_on", False))),
                homed=ptm_bypassed or bool(ptm_status.get("homed")),
                stop_active=self.plc_input.force_stop or not self.plc_input.run_enable or restart_pending,
                force_stop_active=self.plc_input.force_stop,
                camera_ready=vision_bypassed or self.vision_service.state.enabled and _mask_ready(
                    self.vision_snapshot.camera_required_mask,
                    self.vision_snapshot.camera_valid_mask,
                ),
                laser_target_ready=laser_target_ready,
                position_ok=position_ok,
                position_stable=position_stable,
            ),
            controller_state=controller_state,
            tracking_state=tracking_state,
            position_error_mm_x100=position_error_x100,
            pan_error_deg_x100=pan_error_x100,
            tilt_error_deg_x100=tilt_error_x100,
            heartbeat_return=self.plc_input.heartbeat,
            active_tracking_tolerance_mm_x100=tracking_tolerance,
            active_laser_tolerance_deg_x100=laser_tolerance,
            active_recipe_id=self._active_recipe_id,
            active_point_id=self._active_point_id,
            ptm_bypass=bypass["ptm"]["active"],
            vision_bypass=bypass["vision"]["active"],
            laser_bypass=bypass["laser"]["active"],
            command=self.command,
            cameras=CameraMasks(
                required=self.vision_snapshot.camera_required_mask,
                online=self.vision_snapshot.camera_online_mask,
                valid=self.vision_snapshot.camera_valid_mask,
            ),
            alarms=self.alarms,
            latch_faults=self.settings.runtime.fault_latch_enabled,
        )

    def _reset_plc_write_tracking(self) -> None:
        self._plc_write_sequence: int | None = None
        self._plc_write_ack_sequence: int | None = None
        self._plc_write_last_ack_ms = 0
        self._plc_write_ack_seen = False
        self._plc_write_last_reconnect_ms = 0
        self._plc_write_last_commit_ms = 0
        self._plc_write_commit_count = 0
        self._plc_write_error = ""
        self._plc_write_payload = b""

    def _write_plc_output(self) -> None:
        if not self._plc_enabled() or not self._gateway_configured:
            return
        try:
            if not self.plc_memory.connected:
                return
            payload = self.output.to_bytes()
            header = self.plc_memory.header()
            timestamp = now_ms()
            if header.write_length != BYTE_COUNT:
                raise SharedMemoryError(
                    f"PLC output length {header.write_length} != {BYTE_COUNT}"
                )
            if self._plc_write_ack_sequence is None:
                self._plc_write_ack_sequence = header.write_ack_sequence
                self._plc_write_last_ack_ms = timestamp
            elif header.last_write_time_ns and header.status_flags & FLAG_LAST_WRITE_OK and (
                header.write_ack_sequence != self._plc_write_ack_sequence
                or header.write_ack_sequence == self._plc_write_sequence
            ):
                self._plc_write_ack_sequence = header.write_ack_sequence
                self._plc_write_last_ack_ms = timestamp
                self._plc_write_error = ""
                self._plc_write_ack_seen = bool(header.last_write_time_ns)
            self._plc_write_sequence = self.plc_memory.write_plc_data(payload)
            self._plc_write_payload = payload
            self._monitor_plc_cache.update(write_words=words_from_bytes(payload), write_sample_ms=timestamp)
            self._plc_write_last_commit_ms = timestamp
            self._plc_write_commit_count += 1
            timeout_ms = max(
                self.settings.runtime.plc_heartbeat_fault_ms,
                int(self._plc_area_value("write", "interval_ms")) * 6,
            )
            if timestamp - self._plc_write_last_ack_ms >= timeout_ms:
                self._plc_write_error = f"PLC write acknowledgement stopped for {timeout_ms} ms"
                if timestamp - self._plc_write_last_reconnect_ms >= 1000:
                    self._plc_write_last_reconnect_ms = timestamp
                    # A restarted Gateway may own a new segment with the same name.
                    self.plc_memory.reconnect()
            current_fault = self.alarms.fault_word(
                DeviceSummaryBit.PLC_COMMUNICATION, latch_enabled=False
            )
            self.alarms.set_fault_word(
                DeviceSummaryBit.PLC_COMMUNICATION,
                (current_fault & ~(1 << 4)) | (1 << 4 if self._plc_write_error else 0),
                self.settings.runtime.fault_latch_enabled,
            )
        except Exception as exc:
            self._plc_write_error = str(exc) or type(exc).__name__
            LOGGER.debug("PLC shared memory write failed: %s", exc)
            self.alarms.set_fault_word(
                DeviceSummaryBit.PLC_COMMUNICATION,
                self.alarms.fault_word(
                    DeviceSummaryBit.PLC_COMMUNICATION,
                    latch_enabled=False,
                )
                | (1 << 4),
                self.settings.runtime.fault_latch_enabled,
            )

    async def handle_tcp_command(
        self,
        command: str,
        params: dict[str, Any],
    ) -> JsonTcpResponse:
        try:
            command = _normalize_command(command)
            if command in {"device.command", "motion.stop", "laser.on", "laser.off"}:
                self._sync_bypass_state()
            if command in {"runtime.snapshot", "monitor.snapshot"}:
                return JsonTcpResponse(True, self.monitor_snapshot())
            if command == "monitor.capabilities":
                return JsonTcpResponse(True, self.monitor_capabilities())
            if command == "monitor.config.apply":
                patch = params.get("patch")
                expected = params.get("expected")
                if not isinstance(patch, dict) or not isinstance(expected, dict):
                    return JsonTcpResponse(False, error={"code": "BAD_REQUEST", "message": "patch and expected objects are required"})
                async with self._config_request_lock:
                    age = now_ms() - self._last_cycle_ms if self._last_cycle_ms else None
                    if age is None or age > 2000 or self._run_task is None or self._run_task.done():
                        return JsonTcpResponse(False, error={"code": "STATE_UNAVAILABLE", "message": "A fresh running Main Core loop is required"})
                    if self.plc_input.run_enable or bool(self.ptm_service.state.status.get("moving")) or (
                        self._safe_stop_task is not None and not self._safe_stop_task.done()
                    ):
                        return JsonTcpResponse(False, error={"code": "RUNNING", "message": "Stop PLC operation and motion before applying configuration"})
                    latest = load_settings(self.settings_path)
                    changed = _settings_patch_paths(patch)
                    actual = latest.to_dict()
                    missing = [key for key in changed if key not in expected]
                    if missing:
                        return JsonTcpResponse(False, error={"code": "BAD_REQUEST", "message": "expected must include every changed setting path", "paths": missing})
                    conflicts = []
                    for key in changed:
                        value: Any = actual
                        for part in key.split("."):
                            value = value.get(part) if isinstance(value, dict) else None
                        same_number = (type(value) in (int, float) and type(expected[key]) in (int, float))
                        if value != expected[key] or (type(value) is not type(expected[key]) and not same_number):
                            conflicts.append(key)
                    if conflicts:
                        return JsonTcpResponse(False, error={"code": "CONFLICT", "message": "Settings changed since they were read", "paths": conflicts})
                    new_settings = apply_settings_patch(latest, patch)
                    validate_discovery(new_settings.discovery)
                    self._validate_operator_settings(new_settings)
                    ensure_data_directories(new_settings)
                    old_settings = self.settings
                    try:
                        await self._replace_settings(new_settings)
                        save_settings(new_settings, self.settings_path)
                    except Exception:
                        await self._replace_settings(old_settings)
                        raise
                return JsonTcpResponse(True, self.settings.to_dict())
            if command == "alarms.active":
                return JsonTcpResponse(True, self.alarm_history.active())
            if command in {"alarms.history", "alarms.export"}:
                return JsonTcpResponse(True, await self.alarm_history.query(params))
            if command == "controller.restart_request":
                request = await self._request_controller_restart()
                return JsonTcpResponse(True, {"accepted": True, **request})
            if command == "controller.restart_failed":
                request = self._controller_restart_request
                if request is not None and params.get("requested_ms") == request["requested_ms"]:
                    self._controller_restart_request = None
                    if self.command.last_code == CommandCode.CONTROLLER_RESTART and self.command.status == CommandStatus.BUSY:
                        self.command.status = CommandStatus.ERROR
                        self.command.result = CommandResult.INTERNAL_ERROR
                        self.command.message = str(params.get("message") or "Main Core restart failed")
                return JsonTcpResponse(True, {"accepted": True})
            if command == "worker.shutdown" and self._managed_worker:
                asyncio.get_running_loop().call_later(0.1, self._stop_event.set)
                return JsonTcpResponse(True, {"accepted": True})
            if command == "runtime.prepare_stop" and self._managed_worker:
                process_name = params.get("process_name")
                for key, device in (("ptm", "ptm"), ("vision", "vision")):
                    if (process_name == self.settings.processes[key]["process_name"]
                            and self._device_bypassed(device)):
                        return JsonTcpResponse(True, {"accepted": True, "bypassed": True})
                self._abort_active_command(CommandResult.SYSTEM_NOT_READY)
                safe = await self._perform_safe_stop()
                if not safe:
                    LOGGER.error("Safe stop could not be confirmed before dependent process lifecycle")
                if params.get("process_name") == self.settings.processes["plc_gateway"]["process_name"]:
                    self._suspended_processes.add("plc_gateway")
                    self.plc_memory.close()
                    self._gateway_configured = False
                return JsonTcpResponse(True, {"accepted": True, "safe_stop_confirmed": safe})
            if command == "runtime.resume_process" and self._managed_worker:
                self._suspended_processes.discard("plc_gateway")
                self._gateway_refresh_ms = 0
                return JsonTcpResponse(True, {"accepted": True})
            if command == "ping":
                return JsonTcpResponse(
                    True,
                    {
                        "message": "pong",
                        "request_id": params.get("request_id"),
                        "sequence": params.get("sequence"),
                        "timestamp_ms": now_ms(),
                    },
                )
            if command == "get_status":
                processes = await self.process_manager.status_all()
                status = self.status_snapshot(processes)
                xgt_web, ptm_web, vision_web = await asyncio.gather(
                    self._xgt_web_status(),
                    self._ptm_web_status(),
                    self._vision_web_status(),
                )
                status["http_communication"] = {
                    "plc": xgt_web,
                    "ptm": ptm_web,
                    "vision": vision_web,
                }
                return JsonTcpResponse(True, status)
            if command == "get_devices":
                return JsonTcpResponse(True, self.devices.snapshot())
            if command == "orchestration.status":
                return JsonTcpResponse(True, {"enabled": self.settings.runtime.orchestration_enabled,
                                               **self.orchestrator.snapshot()})
            if command == "get_processes":
                return JsonTcpResponse(True, await self.process_manager.status_all())
            if command == "get_config":
                return JsonTcpResponse(True, self.settings.to_dict())
            if command == "discovery.scan":
                return JsonTcpResponse(True, await self.discovery.scan())
            if command == "discovery.status":
                return JsonTcpResponse(True, {"enabled": self.settings.discovery.enabled,
                                               "listening": self.discovery.transport is not None,
                                               "port": self.settings.discovery.port})
            if command == "network.status":
                return JsonTcpResponse(True, await self.network_manager.status())
            if command == "network.apply":
                return JsonTcpResponse(True, await self.network_manager.apply(params))
            if command == "network.confirm":
                result = await self.network_manager.confirm()
                try:
                    await self.discovery.stop()
                    await self.discovery.start()
                except Exception as exc:
                    LOGGER.warning("Discovery restart after network change failed: %s", exc)
                    result["discovery_error"] = str(exc)
                return JsonTcpResponse(True, result)
            if command == "network.cancel":
                await self.network_manager.cancel()
                return JsonTcpResponse(True, {"cancelled": True})
            if command == "update_config":
                async with self._config_request_lock:
                    patch = params.get("patch")
                    new_settings = apply_settings_patch(self.settings, patch)
                    validate_discovery(new_settings.discovery)
                    self._validate_operator_settings(new_settings)
                    ensure_data_directories(new_settings)
                    old_settings = self.settings
                    bypass_only = self._only_bypass_changed(new_settings)
                    if bypass_only:
                        save_settings(new_settings, self.settings_path)
                    try:
                        await self._replace_settings(new_settings)
                        if not bypass_only:
                            save_settings(new_settings, self.settings_path)
                    except Exception:
                        await self._replace_settings(old_settings)
                        if bypass_only:
                            save_settings(old_settings, self.settings_path)
                        raise
                return JsonTcpResponse(True, self.settings.to_dict())
            if command == "reload_config":
                async with self._config_request_lock:
                    new_settings = load_settings(self.settings_path)
                    ensure_data_directories(new_settings)
                    await self._replace_settings(new_settings)
                return JsonTcpResponse(True, self.settings.to_dict())
            if command == "xgt.configure":
                async with self._settings_lock:
                    if not self._plc_enabled():
                        return JsonTcpResponse(False, error={"code": "PROCESS_DISABLED", "message": "PLC Communication is disabled"})
                    self._gateway_update_in_progress = True
                    try:
                        if self._gateway_config_task is not None:
                            self._gateway_config_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await self._gateway_config_task
                            self._gateway_config_task = None
                        self._gateway_configured = False
                        self.plc_memory.close()
                        self._gateway_refresh_ms = now_ms() + 2000
                        result = await self.xgt_gateway.configure_gateway()
                        self._apply_gateway_config(await self.xgt_gateway.get_config())
                    finally:
                        self._gateway_update_in_progress = False
                return JsonTcpResponse(True, result)
            if command == "xgt.status":
                if not self._plc_enabled():
                    return JsonTcpResponse(False, error={"code": "PROCESS_DISABLED", "message": "PLC Communication is disabled"})
                result = await self.xgt_gateway.status()
                return JsonTcpResponse(True, result)
            if command == "xgt.web_status":
                return JsonTcpResponse(True, await self._xgt_web_status())
            if command == "ptm.web_status":
                return JsonTcpResponse(True, await self._ptm_web_status())
            if command == "vision.web_status":
                return JsonTcpResponse(True, await self._vision_web_status())
            if command == "xgt.shared_memory":
                return JsonTcpResponse(True, self._xgt_shared_memory_snapshot())
            if command in {"broadcast", "broadcast.ping", "process.broadcast"}:
                return JsonTcpResponse(True, await self.process_manager.broadcast_ping())
            if command == "device.command":
                device_id = str(params.get("device") or params.get("device_id") or "")
                device_command = str(params.get("command") or "")
                device_params = params.get("params") or {}
                if not device_id or not device_command or not isinstance(device_params, dict):
                    return JsonTcpResponse(
                        False,
                        error={
                            "code": "BAD_DEVICE_COMMAND",
                            "message": "device, command, and object params are required",
                        },
                    )
                service = self.devices.get(device_id)
                if self._device_bypassed(device_id):
                    return JsonTcpResponse(True, {"accepted": False, "bypassed": True})
                if service is not None and not service.state.enabled:
                    return JsonTcpResponse(False, error={"code": "DEVICE_DISABLED", "message": f"device is disabled: {device_id}"})
                manual_on = device_command == "laser.on" and device_params.get("manual") is True
                if manual_on and not self._laser_manual_allowed():
                    return JsonTcpResponse(False, error={"code": "LASER_INTERLOCK", "message": "Safety interlock blocks manual laser output"})
                if self.power_save.sleeping and not manual_on and (device_command in {"laser.on", "laser.pulse"}
                        or (device_command == "laser.arm" and device_params.get("enabled"))):
                    return JsonTcpResponse(False, error={"code": "POWER_SAVING", "message": "Wake Core before enabling laser output"})
                result = await self.devices.command(device_id, device_command, device_params)
                return JsonTcpResponse(True, result)
            if command.startswith("process."):
                action = command.split(".", 1)[1]
                process_name = str(
                    params.get("process_name")
                    or params.get("name")
                    or params.get("process")
                    or ""
                ).strip()
                if not process_name:
                    return JsonTcpResponse(
                        False,
                        error={
                            "code": "BAD_PROCESS_COMMAND",
                            "message": "process_name is required",
                        },
                    )
                plc_process_name = str(
                    self.settings.processes.get("plc_gateway", {}).get(
                        "process_name",
                        "",
                    )
                ).strip()
                if (
                    process_name == plc_process_name
                    and action in {"start", "stop", "restart"}
                ):
                    # Release the previous named mapping before the gateway is
                    # stopped or recreated.  On Windows an open client handle
                    # keeps a stale mapping alive after the owner exits.
                    self.plc_memory.close()
                    self._reset_plc_write_tracking()
                result = await self.process_manager.command(action, process_name)
                return JsonTcpResponse(True, result)
            if command in {"ptm.status", "motor.status"}:
                return JsonTcpResponse(True, self.ptm_service.snapshot())
            if command == "vision.status":
                return JsonTcpResponse(True, self.vision_service.snapshot())
            if command == "power_save.wake":
                self.power_save.wake("api")
                return JsonTcpResponse(True, self.power_save.snapshot())
            if command == "laser.status":
                return JsonTcpResponse(True, self.laser_service.snapshot())
            if command == "motion.stop":
                if self._device_bypassed("ptm"):
                    return JsonTcpResponse(True, {"accepted": False, "bypassed": True})
                if not self.ptm_service.state.enabled:
                    return JsonTcpResponse(False, error={"code": "DEVICE_DISABLED", "message": "PTM is disabled"})
                await self.ptm_service.command("motion.stop")
                return JsonTcpResponse(True, {"accepted": True})
            if command == "laser.on":
                manual_on = params.get("manual") is True
                if manual_on and not self._laser_manual_allowed():
                    return JsonTcpResponse(False, error={"code": "LASER_INTERLOCK", "message": "Safety interlock blocks manual laser output"})
                if self.power_save.sleeping and not manual_on:
                    return JsonTcpResponse(False, error={"code": "POWER_SAVING", "message": "Wake Core before enabling laser output"})
                if self._device_bypassed("laser"):
                    return JsonTcpResponse(True, {"accepted": False, "bypassed": True})
                if not self.laser_service.state.enabled:
                    return JsonTcpResponse(False, error={"code": "DEVICE_DISABLED", "message": "Laser is disabled"})
                result = await self.laser_service.command(self.settings.laser.on_command, params)
                return JsonTcpResponse(True, result or {"accepted": True})
            if command == "laser.off":
                if self._device_bypassed("laser"):
                    return JsonTcpResponse(True, {"accepted": False, "bypassed": True})
                if not self.laser_service.state.enabled:
                    return JsonTcpResponse(False, error={"code": "DEVICE_DISABLED", "message": "Laser is disabled"})
                result = await self.laser_service.command(self.settings.laser.off_command)
                return JsonTcpResponse(True, {"accepted": True})
            if command == "alarm.reset":
                self.alarm_history.capture(self.alarms, self.settings.runtime.fault_latch_enabled)
                self.alarms.reset_latched_faults()
                self.alarm_history.capture(self.alarms, self.settings.runtime.fault_latch_enabled, reset=True)
                return JsonTcpResponse(True, {"accepted": True})
            return JsonTcpResponse(
                False,
                error={"code": "UNKNOWN_COMMAND", "message": command},
            )
        except Exception as exc:
            LOGGER.error("Command %s failed: %s", command, exc)
            return JsonTcpResponse(
                False,
                error={"code": "INTERNAL_ERROR", "message": str(exc)},
            )

    def _plc_write_snapshot(self, header: SharedMemoryHeader | None) -> dict[str, Any]:
        timestamp = now_ms()
        timeout_ms = max(
            self.settings.runtime.plc_heartbeat_fault_ms,
            int(self._plc_area_value("write", "interval_ms")) * 6,
        )
        if not self._plc_enabled():
            delivery_state = "disabled"
        elif not self._gateway_configured:
            delivery_state = "waiting_gateway"
        elif header is None:
            delivery_state = "disconnected"
        elif self._plc_write_error:
            delivery_state = "error"
        elif self._plc_write_sequence is None:
            delivery_state = "waiting_commit"
        elif (
            header.write_ack_sequence == self._plc_write_sequence
            and header.status_flags & FLAG_LAST_WRITE_OK
            and header.last_write_time_ns
        ):
            delivery_state = "confirmed"
        elif self._plc_write_ack_seen and header.status_flags & FLAG_LAST_WRITE_OK:
            delivery_state = "transmitting"
        else:
            delivery_state = "pending"
        return {
            "state": delivery_state,
            "base_address": self._plc_area_value("write", "address"),
            "commit_count": self._plc_write_commit_count,
            "committed_sequence": self._plc_write_sequence,
            "acknowledged_sequence": header.write_ack_sequence if header else None,
            "pending": bool(header and self._plc_write_sequence is not None
                            and self._plc_write_sequence != header.write_ack_sequence),
            "last_commit_age_ms": timestamp - self._plc_write_last_commit_ms
            if self._plc_write_last_commit_ms else None,
            "last_ack_age_ms": timestamp - self._plc_write_last_ack_ms
            if self._plc_write_ack_seen else None,
            "ack_timeout_ms": timeout_ms,
            "last_error": self._plc_write_error or self._gateway_config_error,
            "last_committed_words_preview": words_from_bytes(self._plc_write_payload)[:80]
            if self._plc_write_payload else [],
        }

    def status_snapshot(
        self,
        processes: dict[str, Any] | None = None,
        *, include_shared_memory: bool = True,
    ) -> dict[str, Any]:
        memory_header = None
        if include_shared_memory and self._plc_enabled():
            with contextlib.suppress(Exception):
                memory_header = self.plc_memory.header()
                header = memory_header.to_dict()
        plc_words = self.plc_input.raw_words[:20]
        output_words = self.output.to_words()[:80]
        self._last_status = {
            "core": {
                "running": not self._stop_event.is_set(),
                "tcp": {
                    "host": self._server.host,
                    "port": self._server.port,
                },
                "resources": self.system_resources.snapshot(
                    self.settings.system.memory_warn_percent,
                    self.settings.system.disk_warn_percent,
                ),
            },
            "plc": {
                "enabled": self._plc_enabled(),
                "gateway_configured": self._gateway_configured,
                "gateway_config_error": self._gateway_config_error,
                "shared_memory_connected": self.plc_memory.connected,
                "shared_memory_name": self.plc_memory.connected_name
                or self._gateway_config.get("shared_memory", {}).get("name")
                or self.settings.xgt.shared_memory.name,
                "input_words_preview": plc_words,
                "output_words_preview": output_words,
                "heartbeat": self.plc_input.heartbeat,
                "heartbeat_age_ms": now_ms() - self._last_plc_heartbeat_change_ms,
                "write": self._plc_write_snapshot(memory_header),
            },
            "runtime": {
                "power_save": self.power_save.snapshot(),
                "sampled_ms": now_ms(),
                "loop_running": self._run_task is not None and not self._run_task.done(),
                "cycle_count": self._cycle_count,
                "cycle_interval_ms": self.settings.core.cycle_interval_ms,
                "last_cycle_duration_ms": self._last_cycle_duration_ms,
                "last_cycle_age_ms": now_ms() - self._last_cycle_ms if self._last_cycle_ms else None,
                "last_cycle_error": self._last_cycle_error,
                "controller_state": self.output.controller_state.name,
                "tracking_state": self.output.tracking_state.name,
                "run_enable": self.plc_input.run_enable,
                "force_stop": self.plc_input.force_stop,
                "tracking_enable": self.plc_input.tracking_enable,
                "system_ready": self.output.flags.system_ready,
                "safe_stop_pending": self._safe_stop_task is not None and not self._safe_stop_task.done(),
                "active_recipe_id": self._active_recipe_id,
                "active_point_id": self._active_point_id,
                "restart_request": self._controller_restart_request,
            },
            "command": self.command.to_dict(),
            "bypass": self._bypass_status(),
            "devices": self.devices.snapshot(),
            "processes": processes if processes is not None else self.process_manager.snapshot(),
            "ptm": {
                "enabled": self.ptm_service.state.enabled,
                "online": self.ptm_service.state.online,
                "serial_connected": self.ptm_service.state.status.get("connected"),
                "moving": bool(self.ptm_service.state.status.get("moving")),
                "pan_deg": self.ptm_service.state.status.get("pan_deg"),
                "tilt_deg": self.ptm_service.state.status.get("tilt_deg"),
            },
            "laser": {
                "enabled": self.laser_service.state.enabled,
                "mode": self.settings.laser.mode,
                "online": self.laser_service.state.online,
                "on": bool(self.laser_service.state.status.get("on")),
            },
            "vision": {
                "enabled": self.vision_service.state.enabled,
                "online": self.vision_snapshot.online,
                "tracking_active": self.vision_snapshot.tracking_active,
                "tracker_valid": self.vision_snapshot.tracker_valid,
                "tracker_present": self.vision_snapshot.tracker_present,
                "result_ready": self.vision_snapshot.result_ready,
                "result_age_seconds": self.vision_snapshot.result_age_seconds,
                "position_error_mm": self.vision_snapshot.position_error_mm,
                "camera_ip": self.vision_snapshot.camera_ip,
                "tracking_bypass": self.vision_snapshot.tracking_bypass,
                "tracking_bypass_reason": self.vision_snapshot.tracking_bypass_reason,
                "nutrunner_result": self.nutrunner_result,
            },
            "alarms": {
                "warning_summary": self.alarms.warning_summary(),
                "fault_summary": self.alarms.fault_summary(
                    self.settings.runtime.fault_latch_enabled
                ),
                "active_fault_summary": self.alarms.fault_summary(False),
                "active_fault_words": {
                    "plc_communication": self.alarms.fault_word(
                        DeviceSummaryBit.PLC_COMMUNICATION, False
                    ),
                    "ptm_pan": self.alarms.fault_word(DeviceSummaryBit.PAN_MOTOR, False),
                    "ptm_tilt": self.alarms.fault_word(DeviceSummaryBit.TILT_MOTOR, False),
                },
                "primary_fault_code": self.alarms.primary_fault_code(
                    self.settings.runtime.fault_latch_enabled
                ),
                "primary_warning_code": self.alarms.primary_warning_code(),
                "last_fault_code": self.alarms.visible_last_fault_code(),
                "alarm_sequence": self.alarms.alarm_sequence,
            },
        }
        if "header" in locals():
            self._last_status["plc"]["shared_memory_header"] = header
        return self._last_status

    def monitor_capabilities(self) -> dict[str, Any]:
        endpoints = {}
        for key, module_key in (("ptm", "ptm"), ("vision", "vision"), ("plc", "plc_gateway")):
            module = self.settings.processes.get(module_key, {})
            process = self.process_manager.snapshot().get(str(module.get("process_name", "")), {})
            runtime = process.get("runtime_endpoint") or {}
            endpoints[key] = {
                "enabled": bool(module.get("enabled", True)),
                "tcp": {"host": runtime.get("host", module.get("health_host")),
                        "port": runtime.get("port", module.get("health_port"))},
                "http": {"host": module.get("http_host"), "port": module.get("http_port")},
            }
        return {"schema_version": 1, "commands": ["monitor.snapshot", "monitor.capabilities", "monitor.config.apply", "alarms.active",
                                                         "alarms.history", "alarms.export", "power_save.wake"],
                "power_save": {"timeout_s": self.settings.runtime.power_save_timeout_s,
                               "plc_wake_bit": "D+00.4", "edge": "rising"},
                "alarm_retention_days": 30, "map_version": MAP_VERSION, "endpoints": endpoints,
                "allow_remote_process_control": self.settings.core.allow_remote_process_control,
                "unsupported": ["camera_2_plc_alarms", "vision_frame_contract", "vision_calibration_contract"]}

    def monitor_snapshot(self) -> dict[str, Any]:
        status = self.status_snapshot(include_shared_memory=False)
        sampled = now_ms()
        cache = self._monitor_plc_cache
        header = cache.get("header", {})
        connected = bool(self._plc_enabled() and self.plc_memory.connected and not self._plc_read_error)
        memory = {"connected": connected, "disabled": not self._plc_enabled(),
                  "name": cache.get("name", self.plc_memory.name), "byte_order": "little", "refresh_ms": 500,
                  "header": dict(header), "error": self._plc_read_error or self._gateway_config_error,
                  "read": {"base_address": self._plc_area_value("read", "address"),
                           "offset": header.get("read_offset"), "word_count": len(cache.get("read_words", [])),
                           "words": list(cache.get("read_words", [])),
                           "age_ms": sampled - cache["read_sample_ms"] if cache.get("read_sample_ms") else None},
                  "write": {"base_address": self._plc_area_value("write", "address"),
                            "offset": header.get("write_offset"), "word_count": len(cache.get("write_words", [])),
                            "words": list(cache.get("write_words", [])),
                            "age_ms": sampled - cache["write_sample_ms"] if cache.get("write_sample_ms") else None}}
        source_ns = header.get("last_read_time_ns")
        memory["read"]["source_age_ms"] = max(0, (time.time_ns() - source_ns) // 1000000) if source_ns else None
        status["plc_memory"] = memory
        status["alarm_history"] = self.alarm_history.active()
        status["monitor"] = {"schema_version": 1, "captured_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                             "state_age_ms": sampled - self._last_cycle_ms if self._last_cycle_ms else None,
                             "endpoints": self.monitor_capabilities()["endpoints"]}
        for device in status.get("devices", {}).values():
            device["age_ms"] = sampled - device["last_ok_ms"] if device.get("last_ok_ms") else None
        return status

    async def _xgt_web_status(self) -> dict[str, Any]:
        web = self.settings.xgt.web
        host = client_connect_host(web.host)
        url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        result: dict[str, Any] = {
            "enabled": self._plc_enabled() and web.enabled,
            "url": f"http://{url_host}:{web.port}/",
            "online": False,
            "error": None,
        }
        if not result["enabled"]:
            result["error"] = "XGT Web UI is disabled"
            return result
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, web.port),
                timeout=min(web.timeout_s, 0.75),
            )
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            result["online"] = True
        except Exception as exc:
            result["error"] = str(exc) or "connection failed"
        return result

    async def _ptm_web_status(self) -> dict[str, Any]:
        process = self.settings.processes.get("ptm", {})
        host = client_connect_host(self.settings.motor.web_host)
        port = self.settings.motor.web_port
        url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        result: dict[str, Any] = {
            "enabled": bool(
                self.ptm_service.state.enabled
                and self.settings.motor.web_enabled
            ),
            "url": f"http://{url_host}:{port}/",
            "online": False,
            "error": None,
        }
        if not result["enabled"]:
            result["error"] = "PTM Web UI is disabled"
            return result
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=min(self.settings.motor.timeout_s, 0.75),
            )
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            result["online"] = True
        except Exception as exc:
            result["error"] = str(exc) or "connection failed"
        return result

    async def _vision_web_status(self) -> dict[str, Any]:
        web = self.settings.vision
        host = client_connect_host(web.web_host)
        enabled = bool(self.vision_service.state.enabled and web.web_enabled and web.web_port)
        url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        result: dict[str, Any] = {
            "enabled": enabled,
            "url": f"http://{url_host}:{web.web_port}/" if web.web_port else "",
            "online": False,
            "error": None,
        }
        if not enabled:
            result["error"] = "Vision Web UI is not configured or enabled"
            return result
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, web.web_port),
                timeout=min(web.timeout_s, 0.75),
            )
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            result["online"] = True
        except Exception as exc:
            result["error"] = str(exc) or "connection failed"
        return result

    def _xgt_shared_memory_snapshot(self) -> dict[str, Any]:
        if not self._plc_enabled():
            return {"connected": False, "disabled": True, "refresh_ms": 500}
        try:
            if not self.plc_memory.connected:
                self.plc_memory.connect()
            header = self.plc_memory.header()
            read_bytes = self.plc_memory.read_plc_data()
            write_bytes = self.plc_memory.read_write_data()
            read_count = header.read_length // 2
            write_count = header.write_length // 2
            return {
                "connected": True,
                "name": self.plc_memory.connected_name or self.plc_memory.name,
                "byte_order": "little",
                "refresh_ms": 500,
                "header": header.to_dict(),
                "read": {
                    "base_address": self._plc_area_value("read", "address"),
                    "offset": header.read_offset,
                    "word_count": read_count,
                    "words": words_from_bytes(read_bytes, read_count),
                },
                "write": {
                    "base_address": self._plc_area_value("write", "address"),
                    "offset": header.write_offset,
                    "word_count": write_count,
                    "words": words_from_bytes(write_bytes, write_count),
                },
            }
        except Exception:
            self.plc_memory.close()
            raise


def _scale_or_zero(value: float | None) -> int:
    if value is None or not math.isfinite(value):
        return 0
    return max(0, min(65535, int(round(abs(value) * 100))))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _mask_ready(required: int, valid: int) -> bool:
    if required == 0:
        return valid != 0
    return (required & valid) == required


def _normalize_command(command: str) -> str:
    aliases = {
        "GET_STATUS": "get_status",
        "GET_CONFIG": "get_config",
        "SET_CONFIG": "update_config",
        "SAVE_CONFIG": "get_config",
        "APPLY_CONFIG": "reload_config",
        "GET_PROCESS_STATUS": "get_processes",
        "START_PROCESS": "process.start",
        "STOP_PROCESS": "process.stop",
        "RESTART_PROCESS": "process.restart",
        "RESTART_CONTROLLER": "controller.restart_request",
    }
    return aliases.get(command, command)


def _settings_patch_paths(patch: dict[str, Any], prefix: str = "") -> list[str]:
    paths = []
    for key, value in patch.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and value:
            paths.extend(_settings_patch_paths(value, path))
        else:
            paths.append(path)
    return paths


async def run_core(settings_path: Path = DEFAULT_CONFIG_PATH) -> None:
    app = CoreApplication(settings_path)
    await app.start()
    try:
        await app.wait_closed()
    finally:
        await app.stop()
