"""Optional PLC-to-Vision tracking transitions for the Core cycle."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.communication.plc_memory_mapping import PlcInput
from src.devices.vision_service import VisionDeviceService
from src.common.status import now_ms


LOGGER = logging.getLogger("tracker_core.orchestration")


class CoreOrchestrator:
    def __init__(self, vision: VisionDeviceService) -> None:
        self.vision = vision
        self._requested_tracking = False
        self._task: asyncio.Task[None] | None = None
        self.last_error = ""
        self.last_command = ""
        self._last_attempt_ms = 0

    async def run_cycle(self, core: Any) -> None:
        timestamp_ms = now_ms()
        core._read_plc_input(timestamp_ms)
        core._update_plc_heartbeat_alarm(timestamp_ms)
        core._sync_device_snapshots()
        await core._apply_safe_stop_if_needed()
        self.tick(
            core.plc_input,
            bool(core.alarms.fault_summary(core.settings.runtime.fault_latch_enabled)),
        )
        await core._handle_plc_command()
        core._build_output(timestamp_ms)
        core._write_plc_output()

    def tick(self, plc: PlcInput, fault_active: bool = False) -> None:
        desired = bool(
            plc.run_enable and plc.tracking_enable
            and not plc.force_stop and not fault_active
        )
        retry = bool(self.last_error) and now_ms() - self._last_attempt_ms >= 1000
        if desired == self._requested_tracking and not retry:
            return
        if self._task is not None and not self._task.done():
            return
        self._requested_tracking = desired
        if not self.vision.state.enabled:
            return
        previous = self._task
        command = "tracking.start" if desired else "tracking.stop"
        self._last_attempt_ms = now_ms()

        async def dispatch() -> None:
            if previous:
                try:
                    await previous
                except Exception:
                    pass
            try:
                await self.vision.command(command)
                self.last_command = command
                self.last_error = ""
            except Exception as exc:
                self.last_error = str(exc)
                LOGGER.warning("Vision %s failed: %s", command, exc)

        self._task = asyncio.create_task(dispatch())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    def snapshot(self) -> dict[str, Any]:
        return {"requested_tracking": self._requested_tracking,
                "last_command": self.last_command, "last_error": self.last_error,
                "busy": self._task is not None and not self._task.done()}
