from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.common.settings import VisionSettings
from src.communication.json_tcp import JsonTcpError, send_json_request


@dataclass
class VisionSnapshot:
    online: bool = False
    tracking_active: bool = False
    tracking_bypass: bool = False
    tracking_bypass_reason: str = ""
    camera_ip: str | None = None
    tracker_valid: bool = False
    degraded: bool = False
    fault: bool = False
    position_error_mm: float | None = None
    pan_error_deg: float | None = None
    tilt_error_deg: float | None = None
    confidence: float | None = None
    camera_required_mask: int = 0
    camera_online_mask: int = 0
    camera_valid_mask: int = 0
    camera_warning_words: dict[int, int] = field(default_factory=dict)
    camera_fault_words: dict[int, int] = field(default_factory=dict)
    vision_warning_word: int = 0
    vision_fault_word: int = 0
    tracker_warning_word: int = 0
    tracker_fault_word: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "online": self.online,
            "tracking_active": self.tracking_active,
            "tracking_bypass": self.tracking_bypass,
            "tracking_bypass_reason": self.tracking_bypass_reason,
            "camera_ip": self.camera_ip,
            "tracker_valid": self.tracker_valid,
            "degraded": self.degraded,
            "fault": self.fault,
            "position_error_mm": self.position_error_mm,
            "pan_error_deg": self.pan_error_deg,
            "tilt_error_deg": self.tilt_error_deg,
            "confidence": self.confidence,
            "camera_required_mask": self.camera_required_mask,
            "camera_online_mask": self.camera_online_mask,
            "camera_valid_mask": self.camera_valid_mask,
            "camera_warning_words": self.camera_warning_words,
            "camera_fault_words": self.camera_fault_words,
            "vision_warning_word": self.vision_warning_word,
            "vision_fault_word": self.vision_fault_word,
            "tracker_warning_word": self.tracker_warning_word,
            "tracker_fault_word": self.tracker_fault_word,
            "raw": self.raw,
        }


class VisionTcpClient:
    def __init__(self, settings: VisionSettings) -> None:
        self.settings = settings

    async def snapshot(self) -> VisionSnapshot:
        if not self.settings.enabled:
            return VisionSnapshot(online=False)
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
        return normalize_vision_snapshot(data, self.settings.camera_count)


def normalize_vision_snapshot(data: dict[str, Any], camera_count: int) -> VisionSnapshot:
    tracker = _dict(data.get("tracker"))
    cameras = data.get("cameras")
    if not isinstance(cameras, list):
        cameras = []

    required_mask = 1
    online_mask = _int(data.get("camera_online_mask"), 0) & 1
    valid_mask = _int(data.get("camera_valid_mask"), 0) & 1
    camera_warning_words: dict[int, int] = {}
    camera_fault_words: dict[int, int] = {}
    camera_ip = data.get("camera_ip")

    if cameras:
        camera_data = _dict(cameras[0])
        online_mask = int(bool(camera_data.get("online") or camera_data.get("connected")))
        valid_mask = int(bool(camera_data.get("valid") or camera_data.get("ready")))
        camera_warning_words[1] = _int(camera_data.get("warning_word"), 0)
        camera_fault_words[1] = _int(camera_data.get("fault_word"), 0)
        camera_ip = camera_data.get("ip", camera_ip)

    position_error = _first_number(
        data,
        tracker,
        "position_error_mm",
        "position_error",
        "tracker_error_mm",
        "nutrunner_error_mm",
    )
    pan_error = _first_number(data, tracker, "pan_error_deg", "pan_error")
    tilt_error = _first_number(data, tracker, "tilt_error_deg", "tilt_error")

    tracker_valid = bool(
        data.get("tracker_valid")
        or tracker.get("valid")
        or tracker.get("tracking")
        or data.get("tracking_valid")
    )
    tracking_active = bool(data.get("tracking_active") or tracker.get("active"))

    return VisionSnapshot(
        online=bool(data.get("online", True)),
        tracking_active=tracking_active,
        tracking_bypass=data.get("tracking_bypass") is True,
        tracking_bypass_reason=(data.get("tracking_bypass_reason")
                                if isinstance(data.get("tracking_bypass_reason"), str) else ""),
        camera_ip=camera_ip.strip() or None if isinstance(camera_ip, str) else None,
        tracker_valid=tracker_valid,
        degraded=bool(data.get("degraded") or data.get("vision_degraded")),
        fault=bool(data.get("fault") or data.get("vision_fault")),
        position_error_mm=position_error,
        pan_error_deg=pan_error,
        tilt_error_deg=tilt_error,
        confidence=_first_number(data, tracker, "confidence", "tracking_confidence"),
        camera_required_mask=required_mask,
        camera_online_mask=online_mask,
        camera_valid_mask=valid_mask,
        camera_warning_words=camera_warning_words,
        camera_fault_words=camera_fault_words,
        vision_warning_word=_int(data.get("vision_warning_word"), 0) & ~((1 << 0) | (1 << 3)),
        vision_fault_word=_int(data.get("vision_fault_word"), 0) & ~(1 << 1),
        tracker_warning_word=_int(data.get("tracker_warning_word"), 0),
        tracker_fault_word=_int(data.get("tracker_fault_word"), 0),
        raw=data,
    )


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _first_number(
    primary: dict[str, Any],
    secondary: dict[str, Any],
    *keys: str,
) -> float | None:
    for key in keys:
        for source in (primary, secondary):
            value = source.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None
