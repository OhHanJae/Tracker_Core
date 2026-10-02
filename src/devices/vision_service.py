from __future__ import annotations

from typing import Any

from src.common.settings import VisionSettings
from src.communication.json_tcp import JsonTcpError, send_json_request
from src.devices.base import DeviceService
from src.vision.vision_client import VisionSnapshot, VisionTcpClient


class VisionDeviceService(DeviceService):
    def __init__(self, settings: VisionSettings, client: VisionTcpClient) -> None:
        super().__init__(
            device_id="vision",
            name="Vision",
            enabled=settings.enabled,
            protocol="TCP JSON",
            endpoint=f"{settings.host}:{settings.port}",
            poll_interval_ms=200,
        )
        self.settings = settings
        self.client = client
        self.snapshot_value = VisionSnapshot(online=False)

    async def read_status(self) -> dict[str, Any]:
        self.snapshot_value = await self.client.snapshot()
        return self.snapshot_value.to_dict()

    async def command(self, command: str, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        if command in {"vision.status", self.settings.status_command}:
            return self.snapshot()
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
