from __future__ import annotations

from typing import Any

from src.common.settings import LaserSettings
from src.communication.json_tcp import JsonTcpError, send_json_request
from src.devices.base import DeviceService
from src.devices.ptm_service import PtmService
from src.pt_motor.motor_control import MotorApiClient


class LaserService(DeviceService):
    def __init__(
        self,
        settings: LaserSettings,
        ptm_service: PtmService,
        motor_client: MotorApiClient,
    ) -> None:
        self.settings = settings
        self.ptm_service = ptm_service
        self.motor_client = motor_client
        super().__init__(
            device_id="laser",
            name="Laser",
            enabled=settings.enabled,
            protocol=_protocol(settings.mode),
            endpoint=_endpoint(settings),
            poll_interval_ms=200,
        )

    async def read_status(self) -> dict[str, Any]:
        mode = self.settings.mode.lower()
        if mode == "ptm":
            ptm = self.ptm_service.state
            if not ptm.online:
                raise JsonTcpError(ptm.last_error or "PTM status is offline")
            status = normalize_laser_status(ptm.status, mode="ptm")
            if ptm.status.get("laser_connected") is not None:
                status["online"] = bool(ptm.status["laser_connected"])
            return status
        if mode == "tcp":
            response = await send_json_request(
                self.settings.host,
                self.settings.port,
                self.settings.status_command,
                {},
                self.settings.timeout_s,
            )
            if not response.ok:
                raise JsonTcpError(str(response.error))
            data = response.result if isinstance(response.result, dict) else {}
            return normalize_laser_status(data, mode="tcp")
        if mode == "gpio":
            return {
                "online": False,
                "mode": "gpio",
                "configured": False,
                "message": "GPIO laser mode is reserved but not implemented",
            }
        raise JsonTcpError(f"unsupported laser mode: {self.settings.mode}")

    async def command(self, command: str, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        mode = self.settings.mode.lower()
        if command in {"laser.status", self.settings.status_command}:
            return self.snapshot()
        if mode == "ptm":
            return await self.motor_client.request(command, params)
        if mode == "tcp":
            response = await send_json_request(
                self.settings.host,
                self.settings.port,
                command,
                params,
                self.settings.timeout_s,
            )
            if not response.ok:
                raise JsonTcpError(str(response.error))
            return response.result
        if mode == "gpio":
            raise JsonTcpError("GPIO laser mode is not implemented yet")
        raise JsonTcpError(f"unsupported laser mode: {self.settings.mode}")


def normalize_laser_status(data: dict[str, Any], mode: str) -> dict[str, Any]:
    laser = _dict(data.get("laser"))
    state_text = str(
        _first_value(data, laser, keys=("state", "status", "emission_state")) or ""
    ).lower()
    on = _bool_first(False, data, laser, "laser_on", "on", "emission", "enabled")
    if state_text in {"on", "emitting", "active"}:
        on = True
    if state_text in {"off", "idle", "disabled"}:
        on = False
    return {
        "online": _bool_first(True, data, laser, "online", "connected", "ready"),
        "mode": mode,
        "on": on,
        "power_percent": _first_number(data, laser, "power_percent", "power"),
        "temperature_c": _first_number(data, laser, "temperature_c", "temperature"),
        "warning_word": _first_int(0, data, laser, "warning_word", "warnings"),
        "fault_word": _first_int(0, data, laser, "fault_word", "faults"),
        "fault": _bool_first(False, data, laser, "fault", "error"),
        "raw": data,
    }


def _protocol(mode: str) -> str:
    mode = mode.lower()
    if mode == "ptm":
        return "PTM TCP JSON"
    if mode == "gpio":
        return "GPIO"
    return "TCP JSON"


def _endpoint(settings: LaserSettings) -> str:
    mode = settings.mode.lower()
    if mode == "ptm":
        return "ptm://attached"
    if mode == "gpio":
        return "gpio://local"
    return f"{settings.host}:{settings.port}"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_value(*sources: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            value = source.get(key)
            if value is not None:
                return value
    return None


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
