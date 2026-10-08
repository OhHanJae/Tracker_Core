from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any

from src.common.status import now_ms


@dataclass
class DeviceState:
    device_id: str
    name: str
    enabled: bool
    protocol: str
    endpoint: str
    poll_interval_ms: int = 200
    running: bool = False
    online: bool = False
    last_poll_ms: int = 0
    last_ok_ms: int = 0
    response_ms: int | None = None
    poll_count: int = 0
    ok_count: int = 0
    error_count: int = 0
    last_error: str = ""
    status: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.device_id,
            "name": self.name,
            "enabled": self.enabled,
            "protocol": self.protocol,
            "endpoint": self.endpoint,
            "poll_interval_ms": self.poll_interval_ms,
            "running": self.running,
            "online": self.online,
            "last_poll_ms": self.last_poll_ms,
            "last_ok_ms": self.last_ok_ms,
            "response_ms": self.response_ms,
            "poll_count": self.poll_count,
            "ok_count": self.ok_count,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "status": self.status,
        }


class DeviceService:
    def __init__(
        self,
        device_id: str,
        name: str,
        enabled: bool,
        protocol: str,
        endpoint: str,
        poll_interval_ms: int = 200,
    ) -> None:
        self.device_id = device_id
        self.state = DeviceState(
            device_id=device_id,
            name=name,
            enabled=enabled,
            protocol=protocol,
            endpoint=endpoint,
            poll_interval_ms=max(50, int(poll_interval_ms)),
        )
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        self._stop_event.clear()
        self.state.running = self.state.enabled
        if not self.state.enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._run_loop(), name=f"{self.device_id}-poll")

    async def stop(self) -> None:
        self._stop_event.set()
        self.state.running = False
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            await self.poll_once()
            await asyncio.sleep(self.state.poll_interval_ms / 1000)

    async def poll_once(self) -> None:
        if not self.state.enabled:
            self.state.online = False
            self.state.running = False
            return

        started = now_ms()
        self.state.last_poll_ms = started
        self.state.poll_count += 1
        try:
            status = await self.read_status()
            response_ms = max(0, now_ms() - started)
            if not isinstance(status, dict):
                status = {"value": status}
            self.state.status = status
            self.state.online = bool(status.get("online", True))
            self.state.response_ms = response_ms
            self.state.last_ok_ms = now_ms()
            self.state.last_error = ""
            self.state.ok_count += 1
        except Exception as exc:
            self.state.online = False
            self.state.response_ms = max(0, now_ms() - started)
            self.state.last_error = str(exc)
            self.state.error_count += 1

    async def read_status(self) -> dict[str, Any]:
        raise NotImplementedError

    async def command(self, command: str, params: dict[str, Any] | None = None) -> Any:
        raise NotImplementedError

    def snapshot(self) -> dict[str, Any]:
        return self.state.to_dict()


class DeviceRegistry:
    def __init__(self, services: list[DeviceService]) -> None:
        self.services = {service.device_id: service for service in services}

    async def start_all(self) -> None:
        for service in self.services.values():
            await service.start()

    async def stop_all(self) -> None:
        await asyncio.gather(
            *(service.stop() for service in self.services.values()),
            return_exceptions=True,
        )

    def get(self, device_id: str) -> DeviceService | None:
        return self.services.get(device_id)

    def snapshot(self) -> dict[str, Any]:
        return {
            device_id: service.snapshot()
            for device_id, service in self.services.items()
        }

    async def command(
        self,
        device_id: str,
        command: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        service = self.get(device_id)
        if service is None:
            raise ValueError(f"unknown device: {device_id}")
        if not service.state.enabled:
            raise ValueError(f"device is disabled: {device_id}")
        return await service.command(command, params or {})
