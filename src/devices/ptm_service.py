from __future__ import annotations

import asyncio
from typing import Any

from src.common.settings import MotorSettings
from src.communication.json_tcp import JsonTcpError
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
        attempts = self.settings.status_retries + 1
        for attempt in range(attempts):
            if self.state.bypassed:
                return self.state.status
            try:
                data = await asyncio.wait_for(self.client.status(), timeout=self.settings.timeout_s)
                break
            except Exception as exc:
                self.state.poll_interval_ms = 200
                if attempt == attempts - 1:
                    raise JsonTcpError(
                        f"PTM status failed after {attempts} attempts: {str(exc) or type(exc).__name__}"
                    ) from exc
                await asyncio.sleep(self.state.poll_interval_ms / 1000)
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
            for key in ("recipe_id", "point_id"):
                if type(params.get(key)) is not int or not 1 <= params[key] <= 65535:
                    raise ValueError(f"{key} must be an integer in 1..65535")
            if "laser_after_move" in params and type(params["laser_after_move"]) is not bool:
                raise ValueError("laser_after_move must be a boolean")
            options = {key: params[key] for key in ("laser_after_move", "laser_permit_token", "require_homed", "expected_recipe_revision") if key in params}
            response = await self.client.point_goto(
                int(params.get("recipe_id", 0)),
                int(params.get("point_id", 0)),
                **options,
            )
        elif command == "motion.stop":
            if params:
                response = await self.client.request(command, params)
                if isinstance(response, dict) and response.get("accepted") is False:
                    return response
            else:
                await self.client.stop()
                response = {"accepted": True}
            self._poll_motion_id = None
            self.state.poll_interval_ms = 200
            return response
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

    status = {
        "online": _bool_first(True, data, motor, "online", "ready")
        and _positive_axis_status(True, {}, {}, pan, tilt, "online"),
        "connected": _bool_first(True, serial, data, motor, "connected")
        and _positive_axis_status(True, {}, {}, pan, tilt, "connected"),
        "moving": _bool_any(motion_state in {"tracking", "jog"}, data, motor, pan, tilt, "moving", "motion_moving", "busy"),
        "homing": _bool_any(motion_state == "homing", data, motor, pan, tilt, "homing", "home_busy"),
        "homed": _positive_axis_status(False, data, motor, pan, tilt, "homed", "home_complete"),
        "pan_deg": _first_number(data, motor, pan, position, "pan_deg", "pan", "position_deg"),
        "tilt_deg": _first_number(data, motor, tilt, position, "tilt_deg", "tilt", "position_deg"),
        "pan_speed_deg_s": _first_number(data, motor, pan, "pan_speed_deg_s", "speed"),
        "tilt_speed_deg_s": _first_number(data, motor, tilt, "tilt_speed_deg_s", "speed"),
        "laser_on": _bool_first(False, data, motor, laser, "laser_on", "on", "emission"),
        "laser_connected": _bool_first(False, laser, "connected") if "connected" in laser else None,
        "laser_bypass": _bool_first(False, laser, "bypass"),
        "warning_word": _or_words(data, motor, pan, tilt, "warning_word", "warnings"),
        "fault_word": _or_words(data, motor, pan, tilt, "fault_word", "faults"),
        "fault": _bool_any(False, data, motor, pan, tilt, "fault", "error"),
        "raw": data,
    }
    for name, keys in (("ready", ("ready",)), ("in_position", ("in_position", "inposition", "position_ok"))):
        if any(key in source for source in (data, motor, pan, tilt) for key in keys):
            status[name] = _positive_axis_status(False, data, motor, pan, tilt, *keys)
    return status


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


def _positive_axis_status(
    default: bool, data: dict[str, Any], motor: dict[str, Any],
    pan: dict[str, Any], tilt: dict[str, Any], *keys: str,
) -> bool:
    aggregate = [
        _bool_first(default, source, key)
        for source in (data, motor) for key in keys if key in source
    ]
    fallback = all(aggregate) if aggregate else default
    axes = [
        all(_bool_first(default, axis, key) for key in keys if key in axis)
        if any(key in axis for key in keys) else fallback
        for axis in (pan, tilt)
    ]
    return all(aggregate) and all(axes)


def _bool_any(default: bool, *sources_and_keys: Any) -> bool:
    sources, keys = _split_sources_and_keys(sources_and_keys)
    return default or any(
        _bool_first(False, source, key)
        for source in sources for key in keys if key in source
    )


def _or_words(*sources_and_keys: Any) -> int:
    sources, keys = _split_sources_and_keys(sources_and_keys)
    word = 0
    for source in sources:
        word |= _first_int(0, source, *keys)
    return word & 0xFFFF


def _split_sources_and_keys(values: tuple[Any, ...]) -> tuple[list[dict[str, Any]], list[str]]:
    sources: list[dict[str, Any]] = []
    keys: list[str] = []
    for value in values:
        if isinstance(value, dict) and not keys:
            sources.append(value)
        else:
            keys.append(str(value))
    return sources, keys
