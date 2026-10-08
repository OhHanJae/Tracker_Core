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

    def snapshot(self, memory_warn_percent: int = 90, disk_warn_percent: int = 90) -> dict[str, Any]:
        result = {
            "cpu": self._metric(self._cpu),
            "memory": self._metric(self._memory),
            "disk": self._metric(self._disk),
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
