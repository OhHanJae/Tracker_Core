from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.common.settings import XgtSettings
from src.communication.json_tcp import JsonTcpError, send_json_request


@dataclass
class XgtGatewayClient:
    settings: XgtSettings

    async def ping(self) -> dict[str, Any]:
        response = await send_json_request(
            self.settings.control.host,
            self.settings.control.port,
            "ping",
            {},
            self.settings.control.timeout_s,
        )
        if not response.ok:
            raise JsonTcpError(str(response.error))
        return response.result or {}

    async def status(self) -> dict[str, Any]:
        response = await send_json_request(
            self.settings.control.host,
            self.settings.control.port,
            "get_status",
            {},
            self.settings.control.timeout_s,
        )
        if not response.ok:
            raise JsonTcpError(str(response.error))
        return response.result or {}

    async def get_config(self) -> dict[str, Any]:
        response = await send_json_request(
            self.settings.control.host,
            self.settings.control.port,
            "get_config",
            {},
            self.settings.control.timeout_s,
        )
        if not response.ok:
            raise JsonTcpError(str(response.error))
        if not isinstance(response.result, dict):
            raise JsonTcpError("Gateway returned an invalid configuration")
        return response.result

    async def configure_gateway(self) -> dict[str, Any]:
        shm = self.settings.shared_memory
        area = self.settings.plc_area
        patch = {
            "read": {
                "enabled": True,
                "address": area.read_address,
                "byte_count": shm.read_words * 2,
                "interval_ms": area.interval_ms,
            },
            "write": {
                "enabled": True,
                "address": area.write_address,
                "byte_count": shm.write_words * 2,
                "interval_ms": area.interval_ms,
            },
            "shared_memory": {
                "name": shm.name,
                "size": shm.total_size,
                "read_offset": shm.read_offset,
                "write_offset": shm.write_offset,
            },
        }
        response = await send_json_request(
            self.settings.control.host,
            self.settings.control.port,
            "update_config",
            {"patch": patch},
            self.settings.control.timeout_s,
        )
        if not response.ok:
            raise JsonTcpError(str(response.error))
        return response.result or {}
