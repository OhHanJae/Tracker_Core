from __future__ import annotations

import asyncio
import contextlib
import logging
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
from src.common.system_resources import SystemResourceMonitor
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
    PlcSharedMemoryClient,
    SharedMemoryError,
)
from src.communication.xgt_gateway_client import XgtGatewayClient
from src.devices import DeviceRegistry, LaserService, PtmService, VisionDeviceService
from src.processes import ProcessManager
from src.pt_motor.motor_control import MotorApiClient
from src.vision.vision_client import VisionSnapshot, VisionTcpClient


LOGGER = logging.getLogger("tracker_core")


class CoreApplication:
    def __init__(self, settings_path: Path = DEFAULT_CONFIG_PATH) -> None:
        self.settings_path = settings_path
        self.settings = load_settings(settings_path)
        ensure_data_directories(self.settings)
        save_settings(self.settings, settings_path)
        self._log_handler: RotatingFileHandler | None = None

        self.alarms = AlarmBook()
        self.command = CommandRuntime()
        self._command_seen = False
        self._admin_seen = False
        self._admin_ack_seq = 0
        self._admin_status = CommandStatus.IDLE
        self._admin_result = CommandResult.OK
        self.plc_input = PlcInput()
        self.vision_snapshot = VisionSnapshot()
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
        self._server_task: asyncio.Task[None] | None = None
        self._http_server_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._started = False
        self.system_resources = SystemResourceMonitor(ROOT_DIR)
        self.discovery = DiscoveryService(self.settings.discovery, self.settings.core.port,
                                          self.settings.core.http_port or self.settings.core.port)
        self.network_manager = NetworkManager("eth0")

        self._rebuild_clients()
        self._last_plc_heartbeat = 0
        self._last_plc_heartbeat_change_ms = now_ms()
        self._position_ok_since_ms: int | None = None
        self._safe_stop_applied = False
        self._safe_stop_task: asyncio.Task[bool] | None = None
        self._safe_stop_retry_ms = 0
        self._command_task: asyncio.Task[None] | None = None
        self._pending_request: tuple[int, int, str, dict[str, Any]] | None = None
        self._active_motion: dict[str, Any] | None = None
        self._active_recipe_id = 0
        self._active_point_id = 0
        self._last_status: dict[str, Any] = {}

    @staticmethod
    def _validate_operator_settings(settings: AppSettings) -> None:
        if settings.logging.level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
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
        if settings.file_enabled:
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
        self.plc_memory = PlcSharedMemoryClient(self.settings.xgt.shared_memory.name)
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
        if hasattr(self, "process_manager"):
            self.process_manager.update_modules(self.settings.processes)
        else:
            self.process_manager = ProcessManager(self.settings.processes)
        self.process_manager.update_communication_endpoints(
            self._process_communication_endpoints()
        )

    def _process_enabled(self, key: str) -> bool:
        return self.settings.processes.get(key, {}).get("enabled", True) is not False

    def _plc_enabled(self) -> bool:
        return self._process_enabled("plc_gateway")

    def _motor_enabled(self) -> bool:
        return self.settings.motor.enabled and self._process_enabled("ptm")

    def _laser_enabled(self) -> bool:
        return (
            self.settings.laser.enabled
            and self._process_enabled("laser")
            and (self.settings.laser.mode.lower() != "ptm" or self._motor_enabled())
        )

    def _vision_enabled(self) -> bool:
        return self.settings.vision.enabled and self._process_enabled("vision")

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
        add(
            "laser",
            self.settings.laser.host,
            self.settings.laser.port,
            self.settings.laser.timeout_s,
            "ping",
        )
        return endpoints

    async def _replace_settings(self, settings: AppSettings) -> None:
        validate_discovery(settings.discovery)
        self._validate_operator_settings(settings)
        plc_was_enabled = self._plc_enabled()
        disabling_plc = plc_was_enabled and settings.processes.get(
            "plc_gateway", {}
        ).get("enabled", True) is False
        disabling_motor = self.ptm_service.state.enabled and not (
            settings.motor.enabled
            and settings.processes.get("ptm", {}).get("enabled", True)
        )
        disabling_laser = self.laser_service.state.enabled and not (
            settings.laser.enabled
            and settings.processes.get("laser", {}).get("enabled", True)
            and (
                settings.laser.mode.lower() != "ptm"
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
        self._abort_active_command(CommandResult.SYSTEM_NOT_READY)
        if self._safe_stop_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._safe_stop_task
            self._safe_stop_task = None
        if had_motion or disabling_plc or disabling_motor or disabling_laser:
            self._safe_stop_applied = await self._perform_safe_stop()
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
        if self._started:
            self._configure_logging()
        if restart_discovery:
            self.discovery = DiscoveryService(settings.discovery, settings.core.port,
                                              settings.core.http_port or settings.core.port)
        self._server.static_root = resolve_app_path(self.settings.paths.web_dir)
        self._server.allow_remote_process_control = settings.core.allow_remote_process_control
        if self._http_server is not None:
            self._http_server.static_root = self._server.static_root
            self._http_server.allow_remote_process_control = (
                settings.core.allow_remote_process_control
            )
        self._rebuild_clients()
        if plc_was_enabled != self._plc_enabled():
            self._last_plc_heartbeat = 0
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
            if restart_discovery:
                await self.discovery.start()

    async def start(self) -> None:
        self._validate_operator_settings(self.settings)
        self._configure_logging()
        if self._plc_enabled() and self.settings.xgt.shared_memory.configure_gateway_on_start:
            with contextlib.suppress(Exception):
                await self.xgt_gateway.configure_gateway()

        await self._server.start()
        self._server_task = asyncio.create_task(self._server.serve_forever())
        if self._http_server is not None:
            await self._http_server.start()
            self._http_server_task = asyncio.create_task(
                self._http_server.serve_forever()
            )
        await self.process_manager.reconcile()
        self.process_manager.start_watchdog()
        await self.devices.start_all()
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
        for task in (self._run_task, self._server_task, self._http_server_task):
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
        await self.discovery.stop()
        await self.network_manager.cancel()
        with contextlib.suppress(Exception):
            await self.process_manager.shutdown()
        await self._server.stop()
        if self._http_server is not None:
            await self._http_server.stop()
        self.plc_memory.close()
        if self._log_handler is not None:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler.close()
            self._log_handler = None

    async def wait_closed(self) -> None:
        await self._stop_event.wait()

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            started = now_ms()
            try:
                await self._cycle()
            except Exception:
                LOGGER.exception("core cycle failed")
                self.alarms.set_fault_word(
                    DeviceSummaryBit.CONTROLLER,
                    1 << 0,
                    self.settings.runtime.fault_latch_enabled,
                )
            elapsed = now_ms() - started
            delay_ms = max(1, self.settings.core.cycle_interval_ms - elapsed)
            await asyncio.sleep(delay_ms / 1000)

    async def _cycle(self) -> None:
        if self.settings.runtime.orchestration_enabled:
            await self.orchestrator.run_cycle(self)
            return
        now = now_ms()
        self._read_plc_input(now)
        self._update_plc_heartbeat_alarm(now)
        self._sync_device_snapshots()
        await self._apply_safe_stop_if_needed()
        await self._handle_plc_command()
        self._build_output(now)
        self._write_plc_output()

    def _read_plc_input(self, timestamp_ms: int) -> None:
        if not self._plc_enabled():
            self.plc_memory.close()
            self.plc_input = PlcInput()
            self.alarms.clear_device(DeviceSummaryBit.PLC_COMMUNICATION)
            return
        try:
            if not self.plc_memory.connected:
                self.plc_memory.connect()
            payload = self.plc_memory.read_plc_data()
            if len(payload) != BYTE_COUNT:
                raise ValueError(
                    f"PLC input length {len(payload)} != {BYTE_COUNT}"
                )
            self.plc_input = PlcInput.from_bytes(payload)
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
            LOGGER.debug("PLC shared memory read failed: %s", exc)
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
        self._apply_ptm_alarms()
        self._apply_laser_alarms()

        if not self.vision_service.state.enabled:
            self.vision_snapshot = VisionSnapshot(online=False)
            self._clear_vision_alarms()
            return

        vision_state = self.vision_service.state
        self.vision_snapshot = self.vision_service.snapshot_value
        stale = bool(vision_state.last_ok_ms and now_ms() - vision_state.last_ok_ms >
                     self.settings.runtime.vision_status_max_age_ms)
        if vision_state.poll_count and (not vision_state.online or stale):
            self.vision_snapshot = VisionSnapshot(online=False)
            self.alarms.set_fault_word(
                DeviceSummaryBit.VISION_COMMON,
                1 << 2,
                self.settings.runtime.fault_latch_enabled,
            )
            return
        self._apply_vision_alarms(self.vision_snapshot)

    def _apply_ptm_alarms(self) -> None:
        if not self.ptm_service.state.enabled:
            self.alarms.clear_device(DeviceSummaryBit.PAN_MOTOR)
            self.alarms.clear_device(DeviceSummaryBit.TILT_MOTOR)
            return

        state = self.ptm_service.state
        status = state.status
        warning = _safe_int(status.get("warning_word"))
        fault = _safe_int(status.get("fault_word"))
        if status.get("fault"):
            fault |= 1 << 1
        if state.poll_count and (not state.online or status.get("connected") is False):
            fault |= 1 << 0
        self.alarms.set_warning_word(DeviceSummaryBit.PAN_MOTOR, warning)
        self.alarms.set_warning_word(DeviceSummaryBit.TILT_MOTOR, warning)
        self.alarms.set_fault_word(
            DeviceSummaryBit.PAN_MOTOR,
            fault,
            self.settings.runtime.fault_latch_enabled,
        )
        self.alarms.set_fault_word(
            DeviceSummaryBit.TILT_MOTOR,
            fault,
            self.settings.runtime.fault_latch_enabled,
        )

    def _apply_laser_alarms(self) -> None:
        if not self.laser_service.state.enabled:
            self.alarms.clear_device(DeviceSummaryBit.LASER)
            return

        state = self.laser_service.state
        status = state.status
        warning = _safe_int(status.get("warning_word"))
        fault = _safe_int(status.get("fault_word"))
        if status.get("fault"):
            fault |= 1 << 1
        if state.poll_count and not state.online:
            fault |= 1 << 0
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
        required_missing = bool(snapshot.camera_required_mask & ~snapshot.camera_valid_mask)
        vision_fault = snapshot.vision_fault_word
        if required_missing:
            vision_fault |= 1 << 0
        if snapshot.fault:
            vision_fault |= 1 << 2
        self.alarms.set_fault_word(
            DeviceSummaryBit.VISION_COMMON,
            vision_fault,
            self.settings.runtime.fault_latch_enabled,
        )

        tracker_fault = snapshot.tracker_fault_word
        if self.plc_input.tracking_enable and not snapshot.tracker_valid:
            tracker_fault |= 1 << 0
        self.alarms.set_warning_word(
            DeviceSummaryBit.TRACKER,
            snapshot.tracker_warning_word,
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
        self.alarms.set_fault_word(
            DeviceSummaryBit.CAMERA_1,
            snapshot.camera_fault_words.get(1, 0),
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
        )
        if not safe_stop_requested:
            self._safe_stop_applied = False
            self._safe_stop_retry_ms = 0
            return
        self._active_recipe_id = 0
        self._active_point_id = 0
        if (
            self.command.status in {CommandStatus.RECEIVED, CommandStatus.BUSY}
            and self.command.last_code != CommandCode.ERROR_RESET
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
        still_active = bool(
            ptm_status.get("moving") or ptm_status.get("laser_on")
            or laser_status.get("on")
        )
        if self._safe_stop_applied and not still_active:
            return
        if now_ms() < self._safe_stop_retry_ms:
            return
        self._safe_stop_retry_ms = now_ms() + 1000
        self._safe_stop_task = asyncio.create_task(self._perform_safe_stop())

    def _abort_active_command(self, result: CommandResult) -> None:
        if self._command_task is not None and not self._command_task.done():
            self._command_task.cancel()
        self._command_task = None
        self._pending_request = None
        self._active_motion = None
        self._active_recipe_id = 0
        self._active_point_id = 0
        if self.command.status in {CommandStatus.RECEIVED, CommandStatus.BUSY}:
            self.command.status = CommandStatus.ERROR
            self.command.result = result

    async def _perform_safe_stop(self) -> bool:
        success = True
        if self.ptm_service.state.enabled:
            try:
                await self.ptm_service.command("motion.stop")
            except Exception as exc:
                success = False
                LOGGER.debug("motor stop failed during safe stop: %s", exc)
                self.alarms.set_fault_word(
                    DeviceSummaryBit.PAN_MOTOR,
                    1 << 0,
                    self.settings.runtime.fault_latch_enabled,
                )
                self.alarms.set_fault_word(
                    DeviceSummaryBit.TILT_MOTOR,
                    1 << 0,
                    self.settings.runtime.fault_latch_enabled,
                )
        if (
            self.laser_service.state.enabled
            or self.ptm_service.state.status.get("laser_on")
            or (
                self.ptm_service.state.enabled
                and self.ptm_service.state.status.get("laser_connected") is True
            )
        ):
            try:
                if self.laser_service.state.enabled:
                    await self.laser_service.command(self.settings.laser.off_command)
                else:
                    await self.ptm_service.command("laser.off")
            except Exception as exc:
                success = False
                LOGGER.debug("laser off failed during safe stop: %s", exc)
                self.alarms.set_fault_word(
                    DeviceSummaryBit.LASER,
                    1 << 0,
                    self.settings.runtime.fault_latch_enabled,
                )
        return success

    async def _handle_plc_command(self) -> None:
        if self.plc_input.admin_command and (
            not self._admin_seen or self.plc_input.admin_seq != self._admin_ack_seq
        ):
            self._admin_seen = True
            self._admin_ack_seq = self.plc_input.admin_seq
            self._admin_status = CommandStatus.REJECTED
            self._admin_result = CommandResult.INVALID_COMMAND
        self._progress_motion_command(now_ms())
        if self._pending_request is not None:
            code, seq, expected, params = self._pending_request
            self._pending_request = None
            self.command.status = CommandStatus.BUSY
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
        self._command_seen = True
        self.command.status = CommandStatus.RECEIVED
        self.command.result = CommandResult.OK
        self.command.message = ""
        self.command.busy_since_ms = now_ms()

        if code not in {
            CommandCode.TARGET_APPLY_MOVE, CommandCode.HOME, CommandCode.ERROR_RESET
        }:
            self.command.status = CommandStatus.REJECTED
            self.command.result = CommandResult.INVALID_COMMAND
            return
        if code != CommandCode.ERROR_RESET:
            if self.plc_input.force_stop:
                self.command.status = CommandStatus.REJECTED
                self.command.result = CommandResult.FORCE_STOP_ACTIVE
                return
            if not self.plc_input.run_enable:
                self.command.status = CommandStatus.REJECTED
                self.command.result = CommandResult.SYSTEM_NOT_READY
                return
            if self.alarms.fault_summary(self.settings.runtime.fault_latch_enabled):
                self.command.status = CommandStatus.REJECTED
                self.command.result = CommandResult.FAULT_ACTIVE
                return
            if self._safe_stop_task is not None and not self._safe_stop_task.done():
                self.command.status = CommandStatus.REJECTED
                self.command.result = CommandResult.SYSTEM_NOT_READY
                return
            if (
                not self.ptm_service.state.online
                or not self.ptm_service.state.status.get("connected")
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
        }[CommandCode(code)]
        if (
            entry is None or entry.get("command") != expected
            or entry.get("target") != ("core" if code == CommandCode.ERROR_RESET else "motor")
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

    async def _dispatch_plc_command(
        self, code: int, seq: int, command: str, params: dict[str, Any]
    ) -> None:
        try:
            if code == CommandCode.ERROR_RESET:
                self.alarms.reset_latched_faults()
                self.command.status = CommandStatus.COMPLETE
                return
            response = await self.ptm_service.command(command, params)
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
                if "unknown recipe" in self.command.message.lower():
                    self.command.result = CommandResult.RECIPE_NOT_FOUND
                elif "unknown point" in self.command.message.lower():
                    self.command.result = CommandResult.POINT_NOT_FOUND
                else:
                    self.command.result = CommandResult.INTERNAL_ERROR
                    self.alarms.set_fault_word(
                        DeviceSummaryBit.PAN_MOTOR, 1 << 0,
                        self.settings.runtime.fault_latch_enabled,
                    )

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
        for device in (DeviceSummaryBit.PAN_MOTOR, DeviceSummaryBit.TILT_MOTOR):
            self.alarms.set_fault_word(
                device, 1 << bit_index,
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
        tracking_tolerance = self.plc_input.applied_tracking_tolerance(
            self.settings.runtime.tracking_tolerance_default_mm_x100
        )
        ptm_status = self.ptm_service.state.status if self.ptm_service.state.online else {}
        laser_status = self.laser_service.state.status if self.laser_service.state.online else {}
        laser_tolerance = self.plc_input.applied_laser_tolerance(
            self.settings.runtime.laser_tolerance_default_deg_x100
        )
        position_error_x100 = _scale_or_zero(self.vision_snapshot.position_error_mm)
        pan_error_x100 = _scale_or_zero(self.vision_snapshot.pan_error_deg)
        tilt_error_x100 = _scale_or_zero(self.vision_snapshot.tilt_error_deg)

        position_ok = (
            self.plc_input.tracking_enable
            and self.vision_snapshot.tracker_valid
            and self.vision_snapshot.position_error_mm is not None
            and position_error_x100 <= tracking_tolerance
        )
        if position_ok:
            if self._position_ok_since_ms is None:
                self._position_ok_since_ms = timestamp_ms
        else:
            self._position_ok_since_ms = None
        position_stable = (
            self._position_ok_since_ms is not None
            and timestamp_ms - self._position_ok_since_ms
            >= self.settings.runtime.position_stable_ms
        )
        laser_target_ready = (
            self.laser_service.state.enabled
            and self.vision_service.state.enabled
            and position_stable
            and self._active_recipe_id > 0
            and self._active_point_id > 0
            and self.plc_input.run_enable
            and not self.plc_input.force_stop
            and self.vision_snapshot.pan_error_deg is not None
            and self.vision_snapshot.tilt_error_deg is not None
            and pan_error_x100 <= laser_tolerance
            and tilt_error_x100 <= laser_tolerance
            and self.ptm_service.state.online
            and bool(ptm_status.get("connected", True))
            and not ptm_status.get("moving", False)
            and not self.alarms.fault_summary(self.settings.runtime.fault_latch_enabled)
        )

        fault_active = self.alarms.fault_summary(
            self.settings.runtime.fault_latch_enabled
        ) != 0
        motor_ready = (
            self.ptm_service.state.enabled
            and self.ptm_service.state.online
            and bool(ptm_status.get("connected"))
        )
        system_ready = (
            self.plc_input.run_enable and not self.plc_input.force_stop
            and not fault_active and motor_ready
            and (self._safe_stop_task is None or self._safe_stop_task.done())
        )
        if fault_active:
            controller_state = ControllerState.FAULT
        elif self.plc_input.force_stop or not self.plc_input.run_enable:
            controller_state = ControllerState.STOPPED
        elif self.command.status == CommandStatus.BUSY and self.command.last_code == CommandCode.HOME:
            controller_state = ControllerState.HOMING
        elif self.command.status == CommandStatus.BUSY and self.command.last_code == CommandCode.TARGET_APPLY_MOVE:
            controller_state = ControllerState.TARGET_MOVING
        elif self.plc_input.tracking_enable and self.vision_snapshot.tracking_active:
            controller_state = ControllerState.TRACKING
        elif system_ready:
            controller_state = ControllerState.READY
        else:
            controller_state = ControllerState.STANDBY

        tracking_state = TrackingState.OFF
        if self.plc_input.tracking_enable:
            if fault_active:
                tracking_state = TrackingState.FAULT
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
                tracking_active=self.vision_snapshot.tracking_active,
                homing=controller_state == ControllerState.HOMING,
                motion_moving=bool(ptm_status.get("moving") and ptm_status.get("connected", True)),
                laser_on=bool(laser_status.get("on", ptm_status.get("laser_on", False))),
                homed=bool(ptm_status.get("homed")),
                stop_active=self.plc_input.force_stop or not self.plc_input.run_enable,
                force_stop_active=self.plc_input.force_stop,
                camera_ready=self.vision_service.state.enabled and _mask_ready(
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
            command=self.command,
            cameras=CameraMasks(
                required=self.vision_snapshot.camera_required_mask,
                online=self.vision_snapshot.camera_online_mask,
                valid=self.vision_snapshot.camera_valid_mask,
            ),
            alarms=self.alarms,
            admin_status=int(self._admin_status),
            admin_result=int(self._admin_result),
            admin_ack_seq=self._admin_ack_seq,
            latch_faults=self.settings.runtime.fault_latch_enabled,
        )

    def _write_plc_output(self) -> None:
        if not self._plc_enabled():
            return
        try:
            if not self.plc_memory.connected:
                return
            payload = self.output.to_bytes()
            header = self.plc_memory.header()
            if header.write_length != BYTE_COUNT:
                raise SharedMemoryError(
                    f"PLC output length {header.write_length} != {BYTE_COUNT}"
                )
            self.plc_memory.write_plc_data(payload)
            current_fault = self.alarms.fault_word(
                DeviceSummaryBit.PLC_COMMUNICATION, latch_enabled=False
            )
            if current_fault & (1 << 4):
                self.alarms.set_fault_word(
                    DeviceSummaryBit.PLC_COMMUNICATION,
                    current_fault & ~(1 << 4),
                    self.settings.runtime.fault_latch_enabled,
                )
        except Exception as exc:
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
                patch = params.get("patch")
                new_settings = apply_settings_patch(self.settings, patch)
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
            if command == "reload_config":
                new_settings = load_settings(self.settings_path)
                ensure_data_directories(new_settings)
                await self._replace_settings(new_settings)
                return JsonTcpResponse(True, self.settings.to_dict())
            if command == "xgt.configure":
                if not self._plc_enabled():
                    return JsonTcpResponse(False, error={"code": "PROCESS_DISABLED", "message": "PLC Communication is disabled"})
                result = await self.xgt_gateway.configure_gateway()
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
                if service is not None and not service.state.enabled:
                    return JsonTcpResponse(False, error={"code": "DEVICE_DISABLED", "message": f"device is disabled: {device_id}"})
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
                result = await self.process_manager.command(action, process_name)
                return JsonTcpResponse(True, result)
            if command in {"ptm.status", "motor.status"}:
                return JsonTcpResponse(True, self.ptm_service.snapshot())
            if command == "vision.status":
                return JsonTcpResponse(True, self.vision_service.snapshot())
            if command == "laser.status":
                return JsonTcpResponse(True, self.laser_service.snapshot())
            if command == "motion.stop":
                if not self.ptm_service.state.enabled:
                    return JsonTcpResponse(False, error={"code": "DEVICE_DISABLED", "message": "PTM is disabled"})
                await self.ptm_service.command("motion.stop")
                return JsonTcpResponse(True, {"accepted": True})
            if command == "laser.on":
                if not self.laser_service.state.enabled:
                    return JsonTcpResponse(False, error={"code": "DEVICE_DISABLED", "message": "Laser is disabled"})
                result = await self.laser_service.command(self.settings.laser.on_command)
                return JsonTcpResponse(True, result or {"accepted": True})
            if command == "laser.off":
                if not self.laser_service.state.enabled:
                    return JsonTcpResponse(False, error={"code": "DEVICE_DISABLED", "message": "Laser is disabled"})
                result = await self.laser_service.command(self.settings.laser.off_command)
                return JsonTcpResponse(True, {"accepted": True})
            if command == "alarm.reset":
                self.alarms.reset_latched_faults()
                return JsonTcpResponse(True, {"accepted": True})
            return JsonTcpResponse(
                False,
                error={"code": "UNKNOWN_COMMAND", "message": command},
            )
        except Exception as exc:
            return JsonTcpResponse(
                False,
                error={"code": "INTERNAL_ERROR", "message": str(exc)},
            )

    def status_snapshot(
        self,
        processes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._plc_enabled():
            with contextlib.suppress(Exception):
                header = self.plc_memory.header().to_dict()
        plc_words = self.plc_input.raw_words[:20]
        output_words = self.output.to_words()[:80]
        self._last_status = {
            "core": {
                "running": not self._stop_event.is_set(),
                "tcp": {
                    "host": self.settings.core.host,
                    "port": self.settings.core.port,
                },
                "resources": self.system_resources.snapshot(
                    self.settings.system.memory_warn_percent,
                    self.settings.system.disk_warn_percent,
                ),
            },
            "plc": {
                "enabled": self._plc_enabled(),
                "shared_memory_connected": self.plc_memory.connected,
                "shared_memory_name": self.plc_memory.connected_name
                or self.settings.xgt.shared_memory.name,
                "input_words_preview": plc_words,
                "output_words_preview": output_words,
                "heartbeat": self.plc_input.heartbeat,
            },
            "command": self.command.to_dict(),
            "devices": self.devices.snapshot(),
            "processes": processes if processes is not None else self.process_manager.snapshot(),
            "ptm": {
                "enabled": self.ptm_service.state.enabled,
                "online": self.ptm_service.state.online,
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
                "position_error_mm": self.vision_snapshot.position_error_mm,
            },
            "alarms": {
                "warning_summary": self.alarms.warning_summary(),
                "fault_summary": self.alarms.fault_summary(
                    self.settings.runtime.fault_latch_enabled
                ),
                "primary_fault_code": self.alarms.primary_fault_code(
                    self.settings.runtime.fault_latch_enabled
                ),
                "primary_warning_code": self.alarms.primary_warning_code(),
                "last_fault_code": self.alarms.last_fault_code,
                "alarm_sequence": self.alarms.alarm_sequence,
            },
        }
        if "header" in locals():
            self._last_status["plc"]["shared_memory_header"] = header
        return self._last_status

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
                    "base_address": self.settings.xgt.plc_area.read_address,
                    "offset": header.read_offset,
                    "word_count": read_count,
                    "words": words_from_bytes(read_bytes, read_count),
                },
                "write": {
                    "base_address": self.settings.xgt.plc_area.write_address,
                    "offset": header.write_offset,
                    "word_count": write_count,
                    "words": words_from_bytes(write_bytes, write_count),
                },
            }
        except Exception:
            self.plc_memory.close()
            raise


def _scale_or_zero(value: float | None) -> int:
    if value is None:
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


async def run_core(settings_path: Path = DEFAULT_CONFIG_PATH) -> None:
    app = CoreApplication(settings_path)
    await app.start()
    try:
        await app.wait_closed()
    finally:
        await app.stop()
