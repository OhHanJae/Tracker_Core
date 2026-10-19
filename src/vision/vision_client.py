from __future__ import annotations

import math
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
    tracker_present: bool | None = None
    result_ready: bool | None = None
    result_age_seconds: float | None = None
    point_match: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "online": self.online,
            "tracking_active": self.tracking_active,
            "tracking_bypass": self.tracking_bypass,
            "tracking_bypass_reason": self.tracking_bypass_reason,
            "camera_ip": self.camera_ip,
            "tracker_valid": self.tracker_valid,
            "tracker_present": self.tracker_present,
            "result_ready": self.result_ready,
            "result_age_seconds": self.result_age_seconds,
            "point_match": self.point_match,
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

    async def request_nutrunner_data(
        self, recipe_id: int, point_id: int, command_seq: int
    ) -> dict[str, Any]:
        return await self._request_result(
            self.settings.result_command, recipe_id, point_id, command_seq
        )

    async def set_target(self, recipe_id: int, point_id: int, command_seq: int) -> None:
        params = self._target_params(recipe_id, point_id, command_seq)
        data = await self._request("vision.target.set", params)
        if not isinstance(data, dict) or data.get("accepted") is not True:
            raise JsonTcpError("vision.target.set was not accepted")
        for key, value in params.items():
            if type(data.get(key)) is not int or data[key] != value:
                raise JsonTcpError(f"vision.target.set {key} does not match the request")

    async def notify_fastening_complete(
        self, recipe_id: int, point_id: int, command_seq: int, event_id: str
    ) -> dict[str, Any]:
        return await self._request_result(
            "vision.fastening_complete", recipe_id, point_id, command_seq, event_id
        )

    @staticmethod
    def _target_params(recipe_id: int, point_id: int, command_seq: int) -> dict[str, Any]:
        params = {
            "recipe_id": recipe_id,
            "point_id": point_id,
            "command_seq": command_seq,
        }
        for key, value in params.items():
            minimum = 0 if key == "command_seq" else 1
            if type(value) is not int or not minimum <= value <= 65535:
                raise JsonTcpError(f"{key} must be an integer from {minimum} to 65535")
        return params

    async def _request_result(
        self, command: str, recipe_id: int, point_id: int, command_seq: int,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        params = self._target_params(recipe_id, point_id, command_seq)
        if event_id is not None:
            if not isinstance(event_id, str) or not event_id.strip():
                raise JsonTcpError("event_id must be a nonempty string")
            params["event_id"] = event_id
        data = await self._request(command, params)
        if event_id is not None and (not isinstance(data, dict) or data.get("event_id") != event_id):
            raise JsonTcpError("vision.fastening_complete event_id does not match the request")
        result = normalize_nutrunner_result(data, recipe_id, point_id, command_seq)
        if event_id is not None:
            result["event_id"] = event_id
        return result

    async def _request(self, command: str, params: dict[str, Any]) -> Any:
        response = await send_json_request(
            self.settings.host,
            self.settings.port,
            command,
            params,
            self.settings.timeout_s,
        )
        if not response.ok:
            code = response.error.get("code") if isinstance(response.error, dict) else None
            raise JsonTcpError(
                str(response.error), code=code if isinstance(code, str) else None
            )
        return response.result


def normalize_nutrunner_result(
    data: Any, recipe_id: int, point_id: int, command_seq: int
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise JsonTcpError("vision.result must be an object")
    expected = {
        "recipe_id": recipe_id,
        "point_id": point_id,
        "command_seq": command_seq,
    }
    for key, value in expected.items():
        if type(data.get(key)) is not int or data[key] != value:
            raise JsonTcpError(f"vision.result {key} does not match the request")
    for key in ("supported", "data_valid"):
        if type(data.get(key)) is not bool:
            raise JsonTcpError(f"vision.result {key} must be a boolean")
    result = {
        **expected,
        "supported": data["supported"],
        "data_valid": data["data_valid"],
        "tightening_angle_deg": None,
        "inclination_deg": None,
        "fastening_coordinate_mm": {"x": None, "y": None, "z": None},
        "timestamp_ms": None,
        "coordinate_frame": None,
    }
    if not data["data_valid"]:
        return result
    if not data["supported"]:
        raise JsonTcpError("unsupported vision.result cannot contain valid data")
    coordinate = data.get("fastening_coordinate_mm")
    if not isinstance(coordinate, dict):
        raise JsonTcpError("valid vision.result requires fastening_coordinate_mm")
    for source, keys in (
        (data, ("tightening_angle_deg", "inclination_deg")),
        (coordinate, ("x", "y", "z")),
    ):
        for key in keys:
            value = source.get(key)
            if source is data and value is None:
                continue
            try:
                valid = type(value) in (int, float) and math.isfinite(value)
            except OverflowError:
                valid = False
            if not valid:
                raise JsonTcpError(f"valid vision.result requires finite {key}")
    timestamp_ms = data.get("timestamp_ms")
    if type(timestamp_ms) is not int or timestamp_ms <= 0:
        raise JsonTcpError("valid vision.result requires positive integer timestamp_ms")
    coordinate_frame = data.get("coordinate_frame")
    if coordinate_frame is not None and (
        not isinstance(coordinate_frame, str) or not coordinate_frame.strip()
    ):
        raise JsonTcpError("coordinate_frame must be a nonempty string or null")
    result.update(
        tightening_angle_deg=float(data["tightening_angle_deg"]) if data.get("tightening_angle_deg") is not None else None,
        inclination_deg=float(data["inclination_deg"]) if data.get("inclination_deg") is not None else None,
        fastening_coordinate_mm={key: float(coordinate[key]) for key in ("x", "y", "z")},
        timestamp_ms=timestamp_ms,
        coordinate_frame=coordinate_frame,
    )
    return result


def normalize_vision_snapshot(data: dict[str, Any], camera_count: int) -> VisionSnapshot:
    tracker = _dict(data.get("tracker"))
    cameras = data.get("cameras")
    if not isinstance(cameras, list):
        cameras = []

    camera_count = 2 if camera_count == 2 else 1
    required_mask = (1 << camera_count) - 1
    online_mask = _int(data.get("camera_online_mask"), 0) & required_mask
    valid_mask = _int(data.get("camera_valid_mask"), 0) & required_mask
    camera_warning_words: dict[int, int] = {}
    camera_fault_words: dict[int, int] = {}
    camera_ip = data.get("camera_ip")

    for index, camera in enumerate(cameras[:camera_count]):
        camera_data = _dict(camera)
        bit = 1 << index
        if "online" in camera_data or "connected" in camera_data:
            online = bool(camera_data.get("online") or camera_data.get("connected"))
            online_mask = online_mask | bit if online else online_mask & ~bit
        if "valid" in camera_data or "ready" in camera_data:
            valid = bool(camera_data.get("valid") or camera_data.get("ready"))
            valid_mask = valid_mask | bit if valid else valid_mask & ~bit
        camera_warning_words[index + 1] = _int(camera_data.get("warning_word"), 0)
        camera_fault_words[index + 1] = _int(camera_data.get("fault_word"), 0)
        if index == 0:
            camera_ip = camera_data.get("ip", camera_ip)
    valid_mask &= online_mask

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
    result_age = data.get("result_age_seconds", tracker.get("result_age_seconds"))
    try:
        valid_age = type(result_age) in (int, float) and math.isfinite(result_age) and result_age >= 0
    except OverflowError:
        valid_age = False
    if not valid_age:
        result_age = None

    return VisionSnapshot(
        online=bool(data.get("online", True)),
        tracking_active=tracking_active,
        tracking_bypass=data.get("tracking_bypass") is True,
        tracking_bypass_reason=(data.get("tracking_bypass_reason")
                                if isinstance(data.get("tracking_bypass_reason"), str) else ""),
        camera_ip=camera_ip.strip() or None if isinstance(camera_ip, str) else None,
        tracker_valid=tracker_valid,
        tracker_present=_first_bool(data, tracker, "tracker_present"),
        result_ready=_first_bool(data, tracker, "result_ready"),
        result_age_seconds=result_age,
        point_match=_normalize_point_match(data["point_match"]) if "point_match" in data else None,
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


def _normalize_point_match(value: Any) -> dict[str, Any]:
    source = _dict(value)
    result: dict[str, Any] = {}
    for key in ("recipe_id", "point_id", "command_seq", "matched_recipe_id", "matched_point_id"):
        item = source.get(key)
        minimum = 0 if key == "command_seq" else 1
        result[key] = item if type(item) is int and minimum <= item <= 65535 else None
    age = source.get("age_seconds")
    try:
        valid_age = type(age) in (int, float) and math.isfinite(age) and age >= 0
    except OverflowError:
        valid_age = False
    result["age_seconds"] = float(age) if valid_age else None
    wrong = source.get("wrong_point")
    identified = all(result[key] is not None for key in (
        "recipe_id", "point_id", "command_seq", "matched_recipe_id", "matched_point_id"))
    different = (result["recipe_id"], result["point_id"]) != (
        result["matched_recipe_id"], result["matched_point_id"])
    result["wrong_point"] = wrong if type(wrong) is bool and identified and valid_age and wrong == different else None
    return result


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _first_bool(primary: dict[str, Any], secondary: dict[str, Any], key: str) -> bool | None:
    for source in (primary, secondary):
        value = source.get(key)
        if isinstance(value, bool):
            return value
    return None


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
