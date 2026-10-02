from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModuleStatus:
    id: str
    name: str
    enabled: bool
    process_running: bool = False
    communication_connected: bool = False
    device_online: bool = False
    last_communication: int = 0
    error: str = ""
    process: dict[str, Any] = field(default_factory=dict)
    communication: dict[str, Any] = field(default_factory=dict)
    device: dict[str, Any] = field(default_factory=dict)
    watchdog: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        process_status = "DISABLED" if not self.enabled else (
            "RUNNING" if self.process_running else "STOPPED"
        )
        communication_status = "DISABLED" if not self.enabled else (
            "CONNECTED" if self.communication_connected else "DISCONNECTED"
        )
        device_status = "DISABLED" if not self.enabled else (
            "ONLINE" if self.device_online else "OFFLINE"
        )
        if not self.enabled:
            status_text = "DISABLED"
        elif self.device_online:
            status_text = "ONLINE"
        elif self.communication_connected:
            status_text = "CONNECTED"
        elif self.process_running:
            status_text = "RUNNING"
        elif self.error:
            status_text = "ERROR"
        else:
            status_text = "OFFLINE"

        data = dict(self.process)
        data.update({
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "process_running": self.process_running,
            "communication_connected": self.communication_connected,
            "device_online": self.device_online,
            "last_communication": self.last_communication,
            "error": self.error,
            "status": status_text.lower(),
            "status_text": status_text,
            "process_status": process_status.lower(),
            "process_status_text": process_status,
            "communication_status": communication_status.lower(),
            "communication_status_text": communication_status,
            "device_status": device_status.lower(),
            "device_status_text": device_status,
            "process": self.process,
            "communication": self.communication,
            "device": self.device,
            "watchdog": self.watchdog,
        })
        data["running"] = self.process_running
        data["online"] = self.device_online
        return data
