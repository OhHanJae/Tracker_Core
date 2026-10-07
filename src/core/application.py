from __future__ import annotations

import asyncio
import contextlib
import logging
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

        self.alarms = AlarmBook()
        self.command = CommandRuntime()
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
        self._last_status: dict[str, Any] = {}

    def _rebuild_clients(self) -> None:
        self.xgt_gateway = XgtGatewayClient(self.settings.xgt)
        self.plc_memory = PlcSharedMemoryClient(self.settings.xgt.shared_memory.name)
        self.motor_api = MotorApiClient(self.settings.motor)
        self.vision_api = VisionTcpClient(self.settings.vision)
        self.ptm_service = PtmService(self.settings.motor, self.motor_api)
        self.laser_service = LaserService(
            self.settings.laser,
            self.ptm_service,
            self.motor_api,
        )
        self.vision_service = VisionDeviceService(self.settings.vision, self.vision_api)
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

    def _process_communication_endpoints(self) -> dict[str, dict[str, Any]]:
        endpoints: dict[str, dict[str, Any]] = {}

        def add(config_key: str, host: str, port: int, timeout_s: float, command: str) -> None:
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
        if self._started:
            # Saving configuration must not block while starting every missing
            # process. Process lifecycle is controlled by the dedicated UI
            # buttons; reconcile here only applies retire/disable changes.
            await self.process_manager.reconcile(start_missing=False)
            await self.devices.start_all()
            if restart_discovery:
                await self.discovery.start()

    async def start(self) -> None:
        if self.settings.xgt.shared_memory.configure_gateway_on_start:
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
        self._started = False
        self._stop_event.set()
        for task in (self._run_task, self._server_task, self._http_server_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
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
        try:
            if not self.plc_memory.connected:
                self.plc_memory.connect()
            payload = self.plc_memory.read_plc_data()
            self.plc_input = PlcInput.from_bytes(payload)
            self.alarms.set_fault_word(
                DeviceSummaryBit.PLC_COMMUNICATION,
                0,
                self.settings.runtime.fault_latch_enabled,
            )
            if self.plc_input.heartbeat != self._last_plc_heartbeat:
                self._last_plc_heartbeat = self.plc_input.heartbeat
                self._last_plc_heartbeat_change_ms = timestamp_ms
        except SharedMemoryError as exc:
            LOGGER.debug("PLC shared memory read failed: %s", exc)
            self.alarms.set_fault_word(
                DeviceSummaryBit.PLC_COMMUNICATION,
                1 << 0,
                self.settings.runtime.fault_latch_enabled,
            )
        except Exception as exc:
            LOGGER.debug("PLC read failed: %s", exc)
            self.alarms.set_fault_word(
                DeviceSummaryBit.PLC_COMMUNICATION,
                1 << 3,
                self.settings.runtime.fault_latch_enabled,
            )

    def _update_plc_heartbeat_alarm(self, timestamp_ms: int) -> None:
        elapsed = timestamp_ms - self._last_plc_heartbeat_change_ms
        warning = 1 << 3 if elapsed >= self.settings.runtime.plc_heartbeat_warn_ms else 0
        heartbeat_fault = (
            1 << 1 if elapsed >= self.settings.runtime.plc_heartbeat_fault_ms else 0
        )
        current_fault = self.alarms.fault_word(
            DeviceSummaryBit.PLC_COMMUNICATION,
            latch_enabled=False,
        )
        disconnected_or_read_fault = current_fault & ~((1 << 1) | (1 << 3))
        self.alarms.set_warning_word(DeviceSummaryBit.PLC_COMMUNICATION, warning)
        self.alarms.set_fault_word(
            DeviceSummaryBit.PLC_COMMUNICATION,
            disconnected_or_read_fault | heartbeat_fault,
            self.settings.runtime.fault_latch_enabled,
        )

    def _sync_device_snapshots(self) -> None:
        self._apply_ptm_alarms()
        self._apply_laser_alarms()

        if not self.settings.vision.enabled:
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
        if not self.settings.motor.enabled:
            self.alarms.set_warning_word(DeviceSummaryBit.PAN_MOTOR, 0)
            self.alarms.set_warning_word(DeviceSummaryBit.TILT_MOTOR, 0)
            self.alarms.set_fault_word(
                DeviceSummaryBit.PAN_MOTOR,
                0,
                self.settings.runtime.fault_latch_enabled,
            )
            self.alarms.set_fault_word(
                DeviceSummaryBit.TILT_MOTOR,
                0,
                self.settings.runtime.fault_latch_enabled,
            )
            return

        state = self.ptm_service.state
        status = state.status
        warning = _safe_int(status.get("warning_word"))
        fault = _safe_int(status.get("fault_word"))
        if status.get("fault"):
            fault |= 1 << 1
        if state.poll_count and not state.online:
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
        if not self.settings.laser.enabled:
            self.alarms.set_warning_word(DeviceSummaryBit.LASER, 0)
            self.alarms.set_fault_word(
                DeviceSummaryBit.LASER,
                0,
                self.settings.runtime.fault_latch_enabled,
            )
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
            self.alarms.set_warning_word(device, 0)
            self.alarms.set_fault_word(
                device,
                0,
                self.settings.runtime.fault_latch_enabled,
            )

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

        camera_devices = (
            DeviceSummaryBit.CAMERA_1,
            DeviceSummaryBit.CAMERA_2,
            DeviceSummaryBit.CAMERA_3,
            DeviceSummaryBit.CAMERA_4,
        )
        for index, device in enumerate(camera_devices, start=1):
            self.alarms.set_warning_word(
                device,
                snapshot.camera_warning_words.get(index, 0),
            )
            self.alarms.set_fault_word(
                device,
                snapshot.camera_fault_words.get(index, 0),
                self.settings.runtime.fault_latch_enabled,
            )

    async def _apply_safe_stop_if_needed(self) -> None:
        safe_stop_requested = self.plc_input.force_stop or not self.plc_input.run_enable
        if not safe_stop_requested:
            self._safe_stop_applied = False
            return
        if self._safe_stop_applied:
            return
        self._safe_stop_applied = True
        if self.settings.motor.enabled:
            try:
                await self.ptm_service.command("motion.stop")
            except Exception as exc:
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
        if self.settings.laser.enabled:
            try:
                await self.laser_service.command(self.settings.laser.off_command)
            except Exception as exc:
                LOGGER.debug("laser off failed during safe stop: %s", exc)
                self.alarms.set_fault_word(
                    DeviceSummaryBit.LASER,
                    1 << 0,
                    self.settings.runtime.fault_latch_enabled,
                )

    async def _handle_plc_command(self) -> None:
        code = self.plc_input.command_code
        seq = self.plc_input.command_seq
        if code == 0 or seq == self.command.last_seq:
            return
        if self.command.status == CommandStatus.BUSY:
            self.command.status = CommandStatus.REJECTED
            self.command.result = CommandResult.COMMAND_BUSY
            return

        self.command.last_code = code
        self.command.last_seq = seq
        self.command.status = CommandStatus.RECEIVED
        self.command.result = CommandResult.OK
        self.command.message = ""
        self.command.busy_since_ms = now_ms()

        try:
            await self._execute_command(code)
            if self.command.status not in {CommandStatus.REJECTED, CommandStatus.ERROR}:
                self.command.status = CommandStatus.COMPLETE
                self.command.result = CommandResult.OK
        except Exception as exc:
            LOGGER.debug("command %s/%s failed: %s", code, seq, exc)
            self.command.status = CommandStatus.ERROR
            self.command.result = CommandResult.INTERNAL_ERROR
            self.command.message = str(exc)

    async def _execute_command(self, code: int) -> None:
        entry = self.settings.command_map.get(str(code))
        if entry is None:
            self.command.status = CommandStatus.REJECTED
            self.command.result = CommandResult.INVALID_COMMAND
            return
        target = entry.get("target")
        command = entry.get("command")
        params = self._resolve_params(entry.get("params") or {})

        self.command.status = CommandStatus.BUSY
        if target == "core":
            await self._execute_core_command(command, params)
            return
        if target == "motor":
            await self.ptm_service.command(str(command), params)
            return
        if target == "laser":
            await self.laser_service.command(str(command), params)
            return
        if target == "vision":
            await self.vision_service.command(str(command), params)
            return
        self.command.status = CommandStatus.REJECTED
        self.command.result = CommandResult.INVALID_COMMAND

    async def _execute_core_command(self, command: str, _params: dict[str, Any]) -> None:
        if command == "alarm.reset":
            self.alarms.reset_latched_faults()
            return
        if command == "config.reload":
            settings = load_settings(self.settings_path)
            ensure_data_directories(settings)
            await self._replace_settings(settings)
            return
        if command == "controller.restart_request":
            self.command.status = CommandStatus.REJECTED
            self.command.result = CommandResult.INVALID_COMMAND
            self.command.message = "restart must be handled by service manager"
            return
        self.command.status = CommandStatus.REJECTED
        self.command.result = CommandResult.INVALID_COMMAND

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

        fault_active = self.alarms.fault_summary(
            self.settings.runtime.fault_latch_enabled
        ) != 0
        system_ready = self.plc_input.run_enable and not fault_active
        if fault_active:
            controller_state = ControllerState.FAULT
        elif self.plc_input.force_stop or not self.plc_input.run_enable:
            controller_state = ControllerState.STOPPED
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
            elif self.vision_snapshot.tracker_valid:
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
                motion_moving=False,
                laser_on=False,
                stop_active=self.plc_input.force_stop or not self.plc_input.run_enable,
                force_stop_active=self.plc_input.force_stop,
                camera_ready=_mask_ready(
                    self.vision_snapshot.camera_required_mask,
                    self.vision_snapshot.camera_valid_mask,
                ),
                laser_target_ready=position_stable,
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
            active_recipe_id=self.plc_input.req_recipe_id,
            active_point_id=self.plc_input.req_point_id,
            command=self.command,
            cameras=CameraMasks(
                required=self.vision_snapshot.camera_required_mask,
                online=self.vision_snapshot.camera_online_mask,
                valid=self.vision_snapshot.camera_valid_mask,
            ),
            alarms=self.alarms,
            latch_faults=self.settings.runtime.fault_latch_enabled,
        )

    def _write_plc_output(self) -> None:
        try:
            if not self.plc_memory.connected:
                return
            payload = self.output.to_bytes()
            header = self.plc_memory.header()
            if len(payload) != header.write_length:
                payload = payload[: header.write_length].ljust(header.write_length, b"\x00")
            self.plc_memory.write_plc_data(payload)
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
                ensure_data_directories(new_settings)
                save_settings(new_settings, self.settings_path)
                await self._replace_settings(new_settings)
                return JsonTcpResponse(True, self.settings.to_dict())
            if command == "reload_config":
                new_settings = load_settings(self.settings_path)
                ensure_data_directories(new_settings)
                await self._replace_settings(new_settings)
                return JsonTcpResponse(True, self.settings.to_dict())
            if command == "xgt.configure":
                result = await self.xgt_gateway.configure_gateway()
                return JsonTcpResponse(True, result)
            if command == "xgt.status":
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
                await self.ptm_service.command("motion.stop")
                return JsonTcpResponse(True, {"accepted": True})
            if command == "laser.on":
                result = await self.laser_service.command(self.settings.laser.on_command)
                return JsonTcpResponse(True, result or {"accepted": True})
            if command == "laser.off":
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
                "resources": self.system_resources.snapshot(),
            },
            "plc": {
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
                "enabled": self.settings.motor.enabled,
                "online": self.ptm_service.state.online,
                "moving": bool(self.ptm_service.state.status.get("moving")),
                "pan_deg": self.ptm_service.state.status.get("pan_deg"),
                "tilt_deg": self.ptm_service.state.status.get("tilt_deg"),
            },
            "laser": {
                "enabled": self.settings.laser.enabled,
                "mode": self.settings.laser.mode,
                "online": self.laser_service.state.online,
                "on": bool(self.laser_service.state.status.get("on")),
            },
            "vision": {
                "enabled": self.settings.vision.enabled,
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
            "enabled": web.enabled,
            "url": f"http://{url_host}:{web.port}/",
            "online": False,
            "error": None,
        }
        if not web.enabled:
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
                process.get("enabled", self.settings.motor.enabled)
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
        enabled = bool(web.enabled and web.web_enabled and web.web_port)
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
