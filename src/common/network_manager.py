"""Apply RDK IPv4 settings through NetworkManager with rollback."""

from __future__ import annotations

import asyncio
import ipaddress
import sys
import time
from typing import Any


class NetworkManager:
    def __init__(self, interface: str = "eth0") -> None:
        self.interface = interface
        self._checkpoint: asyncio.subprocess.Process | None = None
        self._pending: dict[str, Any] | None = None
        self._deadline = 0.0

    async def _run(self, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "nmcli", *args, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise
        if process.returncode:
            raise RuntimeError((stderr or stdout).decode(errors="replace").strip())
        return stdout.decode(errors="replace").strip()

    async def status(self) -> dict[str, Any]:
        if sys.platform != "linux":
            return {"supported": False, "interface": self.interface, "pending": False}
        try:
            connection = await self._run("-g", "GENERAL.CONNECTION", "device", "show", self.interface)
            ip = await self._run("-g", "IP4.ADDRESS", "device", "show", self.interface)
            gateway = await self._run("-g", "IP4.GATEWAY", "device", "show", self.interface)
            dns = await self._run("-g", "IP4.DNS", "device", "show", self.interface)
            method = await self._run("-g", "ipv4.method", "connection", "show", connection)
            mtu = await self._run("-g", "802-3-ethernet.mtu", "connection", "show", connection)
            mac = await self._run("-g", "GENERAL.HWADDR", "device", "show", self.interface)
            return {"supported": True, "interface": self.interface, "connection": connection,
                    "method": method, "address": ip.splitlines()[0] if ip else "",
                    "gateway": gateway, "dns": dns.splitlines(), "mtu": mtu, "mac": mac,
                    "pending": self._checkpoint is not None and self._checkpoint.returncode is None}
        except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
            return {"supported": False, "interface": self.interface, "error": str(exc),
                    "pending": self._checkpoint is not None and self._checkpoint.returncode is None}

    async def apply(self, params: dict[str, Any]) -> dict[str, Any]:
        if sys.platform != "linux":
            raise RuntimeError("Network changes require Linux and NetworkManager")
        if self._checkpoint and self._checkpoint.returncode is None:
            raise RuntimeError("A network change is already pending")
        method = str(params.get("method", "manual"))
        if method not in {"manual", "auto"}:
            raise ValueError("method must be manual or auto")
        address = str(params.get("address", "")).strip()
        gateway = str(params.get("gateway", "")).strip()
        dns = str(params.get("dns", "")).strip()
        if method == "manual":
            if "/" not in address:
                raise ValueError("Static IPv4 address requires a prefix, for example 192.168.0.210/24")
            interface = ipaddress.IPv4Interface(address)
            address = str(interface)
            if gateway:
                if ipaddress.IPv4Address(gateway) not in interface.network:
                    raise ValueError("Gateway must be within the configured IPv4 subnet")
        if dns:
            for entry in dns.replace(",", " ").split():
                ipaddress.IPv4Address(entry)
        current = await self.status()
        if not current.get("supported") or not current.get("connection"):
            raise RuntimeError(current.get("error") or "NetworkManager connection unavailable")
        properties = ["ipv4.method", method, "ipv4.addresses", address if method == "manual" else "",
                      "ipv4.gateway", gateway if method == "manual" else "",
                      "ipv4.dns", dns.replace(",", " ")]
        mtu = params.get("mtu")
        if mtu not in {None, ""}:
            mtu = int(mtu)
            if mtu != 0 and not 576 <= mtu <= 9000:
                raise ValueError("MTU must be 0 or between 576 and 9000")
            properties.extend(["802-3-ethernet.mtu", str(mtu)])
        self._checkpoint = await asyncio.create_subprocess_exec(
            "nmcli", "device", "checkpoint", "--timeout", "60", self.interface,
            "--", "nmcli", "device", "modify", self.interface, *properties,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._pending = {"connection": current["connection"], "properties": properties}
        self._deadline = time.monotonic() + 60
        await asyncio.sleep(1)
        if self._checkpoint.returncode is not None:
            _out, err = await self._checkpoint.communicate()
            self._checkpoint = None
            self._pending = None
            self._deadline = 0.0
            raise RuntimeError(err.decode(errors="replace").strip() or "Network checkpoint failed")
        return {"pending": True, "timeout_s": 60, "interface": self.interface,
                "address": address if method == "manual" else "DHCP"}

    async def confirm(self) -> dict[str, Any]:
        process = self._checkpoint
        pending = self._pending
        if not process or process.returncode is not None or not pending:
            raise RuntimeError("No active network checkpoint")
        if time.monotonic() > self._deadline - 10:
            raise RuntimeError("Network checkpoint is expiring; wait for rollback and apply again")
        confirmed = False
        try:
            await self._run("connection", "modify", pending["connection"], *pending["properties"])
            assert process.stdin is not None
            process.stdin.write(b"Yes\n")
            await process.stdin.drain()
            confirmed = True
            stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
            if process.returncode:
                raise RuntimeError((stderr or stdout).decode(errors="replace").strip())
            return {"confirmed": True, "interface": self.interface}
        except Exception:
            if not confirmed and process.returncode is None:
                assert process.stdin is not None
                process.stdin.write(b"No\n")
                await process.stdin.drain()
                await process.communicate()
            raise
        finally:
            self._checkpoint = None
            self._pending = None
            self._deadline = 0.0

    async def cancel(self) -> None:
        if self._checkpoint and self._checkpoint.returncode is None:
            assert self._checkpoint.stdin is not None
            self._checkpoint.stdin.write(b"No\n")
            await self._checkpoint.stdin.drain()
            await asyncio.wait_for(self._checkpoint.communicate(), 10)
        self._checkpoint = None
        self._pending = None
        self._deadline = 0.0
