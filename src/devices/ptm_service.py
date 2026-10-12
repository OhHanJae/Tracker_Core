from __future__ import annotations

from typing import Any

from src.common.settings import MotorSettings
from src.devices.base import DeviceService
from src.pt_motor.motor_control import MotorApiClient


class PtmService(DeviceService):
    def __init__(self, settings: MotorSettings, client: MotorApiClient) -> None:
        super().__init__(
            device_id="ptm",
            name="PTM",
            enabled=settings.enabled,
            protocol="TCP JSON",
            endpoint=f"{settings.host}:{settings.port}",
            poll_interval_ms=200,
        )
        self.settings = settings
        self.client = client
        self._poll_motion_id: str | None = None

    async def read_status(self) -> dict[str, Any]:
        try:
            data = await self.client.status()
        except Exception:
            self.state.poll_interval_ms = 200
            raise
        motion = _dict(data.get("motion"))
        if self._poll_motion_id is not None and self._poll_motion_id in (
            motion.get("completed_id"), motion.get("failed_id")
        ):
            self._poll_motion_id = None
        self.state.poll_interval_ms = (
            50 if self._poll_motion_id is not None
            or str(motion.get("state") or "").lower() == "tracking" else 200
        )
        return normalize_ptm_status(data)

    async def command(self, command: str, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        if command in {"ptm.status", "motor.status", self.settings.status_command}:
            return self.snapshot()
        if command == "point.goto":
            response = await self.client.point_goto(
                int(params.get("recipe_id", 0)),
                int(params.get("point_id", 0)),
            )
        elif command == "motion.stop":
            await self.client.stop()
            self._poll_motion_id = None
            self.state.poll_interval_ms = 200
            return {"accepted": True}
        else:
            response = await self.client.request(command, params)
        if command in {"point.goto", "home.start"} and isinstance(response, dict):
            motion_id = response.get("motion_id")
            if motion_id:
                self._poll_motion_id = motion_id
                self.state.poll_interval_ms = 50
        return response


def normalize_ptm_status(data: dict[str, Any]) -> dict[str, Any]:
    motor = _dict(data.get("motor"))
    serial = _dict(data.get("serial"))
    motion = _dict(data.get("motion"))
    position = _dict(data.get("position"))
    pan = _dict(data.get("pan"))
    tilt = _dict(data.get("tilt"))
    laser = _dict(data.get("laser"))
    motion_state = str(motion.get("state") or "").lower()

    return {
        "online": _bool_first(True, data, motor, "online", "ready"),
        "connected": _bool_first(True, serial, data, motor, "connected"),
        "moving": _bool_first(motion_state in {"tracking", "jog"}, data, motor, "moving", "motion_moving", "busy"),
        "homed": _bool_first(False, data, motor, "homed", "home_complete"),
        "pan_deg": _first_number(data, motor, pan, position, "pan_deg", "pan", "position_deg"),
        "tilt_deg": _first_number(data, motor, tilt, position, "tilt_deg", "tilt", "position_deg"),
        "pan_speed_deg_s": _first_number(data, motor, pan, "pan_speed_deg_s", "speed"),
        "tilt_speed_deg_s": _first_number(data, motor, tilt, "tilt_speed_deg_s", "speed"),
        "laser_on": _bool_first(False, data, motor, laser, "laser_on", "on", "emission"),
        "laser_connected": _bool_first(False, laser, "connected") if "connected" in laser else None,
        "laser_bypass": _bool_first(False, laser, "bypass"),
        "warning_word": _first_int(0, data, motor, "warning_word", "warnings"),
        "fault_word": _first_int(0, data, motor, "fault_word", "faults"),
        "fault": _bool_first(False, data, motor, "fault", "error"),
        "raw": data,
    }


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_number(*sources_and_keys: Any) -> float | None:
    sources, keys = _split_sources_and_keys(sources_and_keys)
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _first_int(default: int, *sources_and_keys: Any) -> int:
    sources, keys = _split_sources_and_keys(sources_and_keys)
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return default


def _bool_first(default: bool, *sources_and_keys: Any) -> bool:
    sources, keys = _split_sources_and_keys(sources_and_keys)
    for source in sources:
        for key in keys:
            if key not in source:
                continue
            value = source.get(key)
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "on", "yes", "ready"}
            return bool(value)
    return default


def _split_sources_and_keys(values: tuple[Any, ...]) -> tuple[list[dict[str, Any]], list[str]]:
    sources: list[dict[str, Any]] = []
    keys: list[str] = []
    for value in values:
        if isinstance(value, dict) and not keys:
            sources.append(value)
        else:
            keys.append(str(value))
    return sources, keys
