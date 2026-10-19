from __future__ import annotations

import ctypes
import os
import platform
import shutil
import time
from pathlib import Path
from typing import Any


class SystemResourceMonitor:
    def __init__(self, disk_path: Path) -> None:
        self.disk_path = disk_path.resolve()
        self._last_cpu: tuple[int, int] | None = None
        self._last_ethernet: tuple[float, dict[str, dict[str, Any]]] | None = None
        self._ethernet_sample: dict[str, Any] | None = None

    def snapshot(self, memory_warn_percent: int = 90, disk_warn_percent: int = 90) -> dict[str, Any]:
        result = {
            "cpu": self._metric(self._cpu),
            "memory": self._metric(self._memory),
            "disk": self._metric(self._disk),
            "ethernet": self._metric(self._ethernet),
            "uptime": self._metric(self._uptime),
            "platform": self._metric(self._platform),
            "collected_ms": int(time.time() * 1000),
        }
        for key, threshold in (("memory", memory_warn_percent), ("disk", disk_warn_percent)):
            metric = result[key]
            metric["warning_threshold_percent"] = threshold
            metric["warning"] = bool(metric.get("available") and metric.get("value") is not None
                                     and metric["value"] >= threshold)
        return result

    @staticmethod
    def _metric(reader: Any) -> dict[str, Any]:
        try:
            return {"available": True, **reader()}
        except Exception as exc:
            return {"available": False, "value": None, "error": str(exc)}

    def _cpu(self) -> dict[str, Any]:
        idle, total = self._cpu_times()
        previous, self._last_cpu = self._last_cpu, (idle, total)
        if previous is None or total <= previous[1]:
            return {"value": None, "unit": "%", "message": "collecting sample"}
        idle_delta = idle - previous[0]
        total_delta = total - previous[1]
        value = max(0.0, min(100.0, 100.0 * (1.0 - idle_delta / total_delta)))
        return {"value": round(value, 1), "unit": "%", "logical_cores": os.cpu_count()}

    @staticmethod
    def _cpu_times() -> tuple[int, int]:
        if os.name == "nt":
            class FileTime(ctypes.Structure):
                _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

            idle = FileTime()
            kernel = FileTime()
            user = FileTime()
            if not ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
            ):
                raise OSError(ctypes.get_last_error(), "GetSystemTimes failed")

            def value(item: FileTime) -> int:
                return (int(item.high) << 32) | int(item.low)

            return value(idle), value(kernel) + value(user)

        fields = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()
        if not fields or fields[0] != "cpu":
            raise RuntimeError("/proc/stat cpu row is unavailable")
        values = [int(value) for value in fields[1:]]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return idle, sum(values)

    @staticmethod
    def _memory() -> dict[str, Any]:
        if os.name == "nt":
            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_uint32),
                    ("load", ctypes.c_uint32),
                    ("total", ctypes.c_uint64),
                    ("available", ctypes.c_uint64),
                    ("total_page", ctypes.c_uint64),
                    ("available_page", ctypes.c_uint64),
                    ("total_virtual", ctypes.c_uint64),
                    ("available_virtual", ctypes.c_uint64),
                    ("available_extended", ctypes.c_uint64),
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                raise OSError(ctypes.get_last_error(), "GlobalMemoryStatusEx failed")
            total, available = int(status.total), int(status.available)
        else:
            values: dict[str, int] = {}
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                key, _, raw = line.partition(":")
                if raw:
                    values[key] = int(raw.strip().split()[0]) * 1024
            total = values["MemTotal"]
            available = values["MemAvailable"]
        used = total - available
        return {
            "value": round(used * 100.0 / total, 1),
            "unit": "%",
            "used_bytes": used,
            "total_bytes": total,
        }

    def _disk(self) -> dict[str, Any]:
        usage = shutil.disk_usage(self.disk_path)
        return {
            "value": round(usage.used * 100.0 / usage.total, 1),
            "unit": "%",
            "used_bytes": usage.used,
            "total_bytes": usage.total,
            "path": str(self.disk_path.anchor or self.disk_path),
        }

    def _ethernet(self) -> dict[str, Any]:
        timestamp = time.monotonic()
        previous = self._last_ethernet
        if previous is not None and timestamp - previous[0] < 1.0 and self._ethernet_sample is not None:
            return self._ethernet_sample
        counters = self._ethernet_counters()
        interfaces = []
        for key, current in counters.items():
            item = {**current, "receive_bps": None, "transmit_bps": None, "utilization_percent": None}
            old = previous[1].get(key) if previous is not None else None
            if (old is not None and current["received_bytes"] >= old["received_bytes"]
                    and current["transmitted_bytes"] >= old["transmitted_bytes"]):
                elapsed = timestamp - previous[0]
                item["receive_bps"] = (current["received_bytes"] - old["received_bytes"]) * 8 / elapsed
                item["transmit_bps"] = (current["transmitted_bytes"] - old["transmitted_bytes"]) * 8 / elapsed
                if current["receive_link_bps"] and current["transmit_link_bps"]:
                    # Full-duplex links can receive and transmit at their rated speed simultaneously.
                    item["utilization_percent"] = round(min(100.0, 100 * max(
                        item["receive_bps"] / current["receive_link_bps"],
                        item["transmit_bps"] / current["transmit_link_bps"],
                    )), 1)
            interfaces.append(item)
        measured = bool(interfaces) and all(item["receive_bps"] is not None for item in interfaces)
        receive = sum(item["receive_bps"] for item in interfaces) if measured else None
        transmit = sum(item["transmit_bps"] for item in interfaces) if measured else None
        self._last_ethernet = (timestamp, counters)
        self._ethernet_sample = {
            "value": receive + transmit if measured else None,
            "unit": "bps", "receive_bps": receive, "transmit_bps": transmit,
            "interfaces": interfaces,
            "message": "" if measured else "collecting sample" if interfaces else "No connected Ethernet interfaces",
        }
        return self._ethernet_sample

    @staticmethod
    def _ethernet_counters() -> dict[str, dict[str, Any]]:
        if os.name == "nt":
            class Guid(ctypes.Structure):
                _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
                            ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8)]

            class InterfaceRow(ctypes.Structure):
                _fields_ = [
                    ("luid", ctypes.c_uint64), ("index", ctypes.c_uint32), ("guid", Guid),
                    ("alias", ctypes.c_wchar * 257), ("description", ctypes.c_wchar * 257),
                    ("address_length", ctypes.c_uint32),
                    ("address", ctypes.c_ubyte * 32), ("permanent_address", ctypes.c_ubyte * 32),
                    ("mtu", ctypes.c_uint32), ("type", ctypes.c_uint32),
                    ("tunnel_type", ctypes.c_uint32), ("media_type", ctypes.c_uint32),
                    ("physical_medium", ctypes.c_uint32), ("access_type", ctypes.c_uint32),
                    ("direction_type", ctypes.c_uint32), ("flags", ctypes.c_ubyte),
                    ("oper_status", ctypes.c_uint32), ("admin_status", ctypes.c_uint32),
                    ("media_state", ctypes.c_uint32), ("network_guid", Guid),
                    ("connection_type", ctypes.c_uint32),
                    ("transmit_link_bps", ctypes.c_uint64), ("receive_link_bps", ctypes.c_uint64),
                    ("received_bytes", ctypes.c_uint64), ("receive_counters", ctypes.c_uint64 * 8),
                    ("transmitted_bytes", ctypes.c_uint64), ("transmit_counters", ctypes.c_uint64 * 8),
                ]

            class InterfaceTable(ctypes.Structure):
                _fields_ = [("count", ctypes.c_uint32), ("rows", InterfaceRow * 1)]

            api = ctypes.windll.iphlpapi
            table = ctypes.POINTER(InterfaceTable)()
            api.GetIfTable2.argtypes = [ctypes.POINTER(ctypes.POINTER(InterfaceTable))]
            api.GetIfTable2.restype = ctypes.c_uint32
            api.FreeMibTable.argtypes = [ctypes.c_void_p]
            api.FreeMibTable.restype = None
            error = api.GetIfTable2(ctypes.byref(table))
            if error:
                raise OSError(error, "GetIfTable2 failed")
            try:
                rows = (InterfaceRow * table.contents.count).from_address(
                    ctypes.addressof(table.contents) + InterfaceTable.rows.offset)
                return {
                    str(row.luid): {
                        "name": row.alias, "received_bytes": int(row.received_bytes),
                        "transmitted_bytes": int(row.transmitted_bytes),
                        "receive_link_bps": int(row.receive_link_bps),
                        "transmit_link_bps": int(row.transmit_link_bps),
                    }
                    for row in rows
                    if row.type == 6 and row.flags & 1 and row.oper_status == 1
                    and row.physical_medium in {0, 14}
                }
            finally:
                api.FreeMibTable(table)

        counters = {}
        for interface in Path("/sys/class/net").iterdir():
            if not (interface / "device").exists() or (interface / "wireless").exists():
                continue
            try:
                if (interface / "type").read_text().strip() != "1" or (interface / "operstate").read_text().strip() != "up":
                    continue
                received = int((interface / "statistics/rx_bytes").read_text())
                transmitted = int((interface / "statistics/tx_bytes").read_text())
            except FileNotFoundError:
                continue  # An adapter can disappear while enumerating it.
            try:
                speed = max(0, int((interface / "speed").read_text())) * 1_000_000
            except (OSError, ValueError):
                speed = 0
            counters[interface.name] = {
                "name": interface.name, "received_bytes": received, "transmitted_bytes": transmitted,
                "receive_link_bps": speed, "transmit_link_bps": speed,
            }
        return counters

    @staticmethod
    def _uptime() -> dict[str, Any]:
        if os.name == "nt":
            seconds = int(ctypes.windll.kernel32.GetTickCount64()) / 1000.0
        else:
            seconds = float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
        return {"value": round(seconds, 1), "unit": "seconds"}

    @staticmethod
    def _platform() -> dict[str, Any]:
        value = platform.platform(aliased=True, terse=False)
        if not value:
            raise RuntimeError("platform information is unavailable")
        return {"value": value}
