"""UDP discovery for Core instances on the local network."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import os
import socket
import uuid
from typing import Any

from src.common.settings import DiscoverySettings


PROTOCOL = "tracker-core-discovery"
VERSION = 1


def interface_ipv4(name: str) -> str:
    if os.name == "posix":
        import fcntl
        import struct

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            try:
                packed = fcntl.ioctl(
                    sock.fileno(), 0x8915, struct.pack("256s", name.encode("ascii")[:15])
                )
                return socket.inet_ntoa(packed[20:24])
            except OSError as exc:
                raise ValueError(f"Discovery interface {name} has no IPv4 address") from exc
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def validate_discovery(settings: DiscoverySettings) -> None:
    if settings.method not in {"udp_broadcast", "manual"}:
        raise ValueError("Discovery method must be udp_broadcast or manual")
    if not 1 <= int(settings.port) <= 65535:
        raise ValueError("Discovery port must be between 1 and 65535")
    if not 100 <= int(settings.timeout_ms) <= 10000:
        raise ValueError("Discovery timeout_ms must be between 100 and 10000")
    if not 1 <= int(settings.retries) <= 5:
        raise ValueError("Discovery retries must be between 1 and 5")
    ipaddress.IPv4Address(settings.broadcast_address)
    if not settings.interface or len(settings.interface) > 15:
        raise ValueError("Discovery interface must name a network interface")
    if len(settings.name.strip()) > 80 or not settings.name.strip():
        raise ValueError("Discovery name must contain 1-80 characters")
    if len(settings.controller_id) > 80:
        raise ValueError("Controller ID must be at most 80 characters")


class _Responder(asyncio.DatagramProtocol):
    def __init__(self, service: "DiscoveryService") -> None:
        self.service = service

    def datagram_received(self, data: bytes, address: tuple[str, int]) -> None:
        try:
            message = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return
        if not isinstance(message, dict) or message.get("protocol") != PROTOCOL:
            return
        if message.get("version") != VERSION or message.get("type") != "discover":
            return
        if self.service.transport:
            self.service.transport.sendto(self.service.packet("offer"), address)


class _Scanner(asyncio.DatagramProtocol):
    def __init__(self, queue: asyncio.Queue) -> None:
        self.queue = queue

    def datagram_received(self, data: bytes, address: tuple[str, int]) -> None:
        if not self.queue.full():
            self.queue.put_nowait((data, address))


class DiscoveryService:
    def __init__(self, settings: DiscoverySettings, core_port: int, web_port: int) -> None:
        validate_discovery(settings)
        self.settings = settings
        self.core_port = core_port
        self.web_port = web_port
        self.transport: asyncio.DatagramTransport | None = None
        self._announce_task: asyncio.Task[None] | None = None
        self._announce_socket: socket.socket | None = None
        self._announce_transport: asyncio.DatagramTransport | None = None

    def packet(self, kind: str) -> bytes:
        ip = interface_ipv4(self.settings.interface)
        device_id = self.settings.controller_id or str(uuid.uuid5(
            uuid.NAMESPACE_DNS, f"tracker-core:{socket.gethostname()}:{self.settings.interface}"
        ))
        return json.dumps({
            "protocol": PROTOCOL,
            "version": VERSION,
            "type": kind,
            "id": device_id,
            "name": self.settings.name,
            "ip": ip,
            "core_port": self.core_port,
            "web_port": self.web_port,
        }, separators=(",", ":")).encode("utf-8")

    async def start(self) -> None:
        if not self.settings.enabled or self.settings.method != "udp_broadcast":
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("0.0.0.0", int(self.settings.port)))
            sock.setblocking(False)
            transport, _protocol = await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: _Responder(self), sock=sock
            )
        except Exception:
            sock.close()
            raise
        self.transport = transport
        try:
            self._announce_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._announce_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            source = interface_ipv4(self.settings.interface) if os.name == "posix" else "0.0.0.0"
            self._announce_socket.bind((source, 0))
            self._announce_socket.setblocking(False)
            self._announce_transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                asyncio.DatagramProtocol, sock=self._announce_socket
            )
            self._announce_socket = None
        except Exception:
            await self.stop()
            raise
        self._announce_task = asyncio.create_task(self._announce_loop())

    async def stop(self) -> None:
        if self._announce_task:
            self._announce_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._announce_task
            self._announce_task = None
        if self.transport:
            self.transport.close()
            self.transport = None
        if self._announce_socket:
            self._announce_socket.close()
            self._announce_socket = None
        if self._announce_transport:
            self._announce_transport.close()
            self._announce_transport = None
        await asyncio.sleep(0)

    async def _announce_loop(self) -> None:
        while True:
            try:
                if self._announce_transport:
                    self._announce_transport.sendto(
                        self.packet("announce"),
                        (self.settings.broadcast_address, int(self.settings.port)))
            except OSError:
                pass
            await asyncio.sleep(5)

    async def scan(self) -> list[dict[str, Any]]:
        if self.settings.method != "udp_broadcast":
            return []
        loop = asyncio.get_running_loop()
        found: dict[str, dict[str, Any]] = {}
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((interface_ipv4(self.settings.interface) if os.name == "posix" else "0.0.0.0", 0))
            sock.setblocking(False)
            queue: asyncio.Queue = asyncio.Queue(maxsize=128)
            transport, _ = await loop.create_datagram_endpoint(lambda: _Scanner(queue), sock=sock)
            request = json.dumps({
                "protocol": PROTOCOL, "version": VERSION, "type": "discover"
            }).encode("utf-8")
            try:
                for _ in range(int(self.settings.retries)):
                    transport.sendto(request, (
                        self.settings.broadcast_address, int(self.settings.port)
                    ))
                    deadline = loop.time() + int(self.settings.timeout_ms) / 1000
                    while loop.time() < deadline:
                        try:
                            data, peer = await asyncio.wait_for(queue.get(), deadline - loop.time())
                        except asyncio.TimeoutError:
                            break
                        try:
                            offer = json.loads(data.decode("utf-8"))
                        except (UnicodeDecodeError, ValueError):
                            continue
                        if not isinstance(offer, dict) or offer.get("protocol") != PROTOCOL:
                            continue
                        if offer.get("version") != VERSION or offer.get("type") != "offer":
                            continue
                        offer["ip"] = peer[0]
                        found[str(offer.get("id") or peer[0])] = offer
            finally:
                transport.close()
        return list(found.values())
