from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.common.settings import MotorSettings
from src.communication.json_tcp import JsonTcpError, send_json_request


@dataclass
class MotorApiClient:
    settings: MotorSettings

    async def request(self, command: str, params: dict[str, Any] | None = None) -> Any:
        if not self.settings.enabled:
            raise JsonTcpError("motor API is disabled")
        response = await send_json_request(
            self.settings.host,
            self.settings.port,
            command,
            params or {},
            self.settings.timeout_s,
        )
        if not response.ok:
            raise JsonTcpError(str(response.error))
        return response.result

    async def status(self) -> dict[str, Any]:
        result = await self.request(self.settings.status_command)
        return result if isinstance(result, dict) else {}

    async def stop(self) -> None:
        await self.request("motion.stop")

    async def laser_off(self) -> None:
        await self.request("laser.off")

    async def point_goto(self, recipe_id: int, point_id: int) -> Any:
        return await self.request(
            "point.goto",
            {
                "recipe_id": int(recipe_id),
                "point_id": int(point_id),
            },
        )
