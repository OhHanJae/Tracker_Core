from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class CommandCode(IntEnum):
    NONE = 0
    TARGET_APPLY_MOVE = 1
    HOME = 2
    ERROR_RESET = 3
    CONTROLLER_RESTART = 4
    RELOAD_CONFIG = 5


class ControllerState(IntEnum):
    INIT = 0
    STANDBY = 1
    READY = 2
    HOMING = 3
    TARGET_MOVING = 4
    TRACKING = 5
    STOPPED = 6
    FAULT = 7
    TEACH_CALIBRATION = 8


class TrackingState(IntEnum):
    OFF = 0
    INITIALIZING = 1
    SEARCHING = 2
    TRACKING = 3
    DEGRADED = 4
    LOST = 5
    FAULT = 6


class CommandStatus(IntEnum):
    IDLE = 0
    RECEIVED = 1
    BUSY = 2
    COMPLETE = 3
    REJECTED = 4
    ERROR = 5


class CommandResult(IntEnum):
    OK = 0
    INVALID_COMMAND = 1
    RECIPE_NOT_FOUND = 2
    POINT_NOT_FOUND = 3
    SYSTEM_NOT_READY = 4
    HOMING_REQUIRED = 5
    FAULT_ACTIVE = 6
    FORCE_STOP_ACTIVE = 7
    TRACKING_UNAVAILABLE = 8
    MOTION_TIMEOUT = 9
    CONFIG_OR_CAL_ERROR = 10
    COMMAND_BUSY = 11
    INTERNAL_ERROR = 12


class DeviceSummaryBit(IntEnum):
    CONTROLLER = 0
    PLC_COMMUNICATION = 1
    VISION_COMMON = 2
    CAMERA_1 = 3
    CAMERA_2 = 4
    CAMERA_3 = 5
    CAMERA_4 = 6
    TRACKER = 7
    PAN_MOTOR = 8
    TILT_MOTOR = 9
    LASER = 10
    RECIPE = 11
    CALIBRATION = 12


FAULT_PRIORITY = [
    (DeviceSummaryBit.CONTROLLER, 1001),
    (DeviceSummaryBit.PLC_COMMUNICATION, 1101),
    (DeviceSummaryBit.VISION_COMMON, 2001),
    (DeviceSummaryBit.CAMERA_1, 2101),
    (DeviceSummaryBit.CAMERA_2, 2201),
    (DeviceSummaryBit.CAMERA_3, 2301),
    (DeviceSummaryBit.CAMERA_4, 2401),
    (DeviceSummaryBit.TRACKER, 3001),
    (DeviceSummaryBit.PAN_MOTOR, 4001),
    (DeviceSummaryBit.TILT_MOTOR, 4101),
    (DeviceSummaryBit.LASER, 5001),
    (DeviceSummaryBit.RECIPE, 6001),
    (DeviceSummaryBit.CALIBRATION, 6101),
]

WARNING_PRIORITY = [
    (DeviceSummaryBit.CONTROLLER, 1000),
    (DeviceSummaryBit.PLC_COMMUNICATION, 1100),
    (DeviceSummaryBit.VISION_COMMON, 2000),
    (DeviceSummaryBit.CAMERA_1, 2100),
    (DeviceSummaryBit.CAMERA_2, 2200),
    (DeviceSummaryBit.CAMERA_3, 2300),
    (DeviceSummaryBit.CAMERA_4, 2400),
    (DeviceSummaryBit.TRACKER, 3000),
    (DeviceSummaryBit.PAN_MOTOR, 4000),
    (DeviceSummaryBit.TILT_MOTOR, 4100),
    (DeviceSummaryBit.LASER, 5000),
    (DeviceSummaryBit.RECIPE, 6000),
    (DeviceSummaryBit.CALIBRATION, 6100),
]


def now_ms() -> int:
    return int(time.monotonic() * 1000)


def bit(value: bool, index: int) -> int:
    return (1 << index) if value else 0


def count_bits(value: int) -> int:
    return int(value & 0xFFFF).bit_count()


@dataclass
class AlarmBook:
    warnings: dict[int, int] = field(default_factory=dict)
    faults: dict[int, int] = field(default_factory=dict)
    latched_faults: dict[int, int] = field(default_factory=dict)
    last_fault_code: int = 0
    alarm_sequence: int = 0

    def set_warning_word(self, device: DeviceSummaryBit, word: int) -> None:
        self.warnings[int(device)] = word & 0xFFFF

    def clear_device(self, device: DeviceSummaryBit) -> None:
        self.warnings.pop(int(device), None)
        self.faults.pop(int(device), None)
        self.latched_faults.pop(int(device), None)

    def set_fault_word(
        self, device: DeviceSummaryBit, word: int, latch_enabled: bool = True
    ) -> None:
        word &= 0xFFFF
        self.faults[int(device)] = word
        if word:
            previous = self.latched_faults.get(int(device), 0)
            if latch_enabled:
                self.latched_faults[int(device)] = previous | word
            code = self.primary_fault_code()
            if code and code != self.last_fault_code:
                self.last_fault_code = code
                self.alarm_sequence = (self.alarm_sequence + 1) & 0xFFFF

    def reset_latched_faults(self) -> None:
        self.latched_faults.clear()

    def fault_word(self, device: DeviceSummaryBit, latch_enabled: bool = True) -> int:
        if latch_enabled:
            return self.latched_faults.get(int(device), 0) & 0xFFFF
        return self.faults.get(int(device), 0) & 0xFFFF

    def warning_word(self, device: DeviceSummaryBit) -> int:
        return self.warnings.get(int(device), 0) & 0xFFFF

    def fault_summary(self, latch_enabled: bool = True) -> int:
        summary = 0
        for device, _ in FAULT_PRIORITY:
            if self.fault_word(device, latch_enabled):
                summary |= 1 << int(device)
        return summary & 0xFFFF

    def warning_summary(self) -> int:
        summary = 0
        for device, _ in WARNING_PRIORITY:
            if self.warning_word(device):
                summary |= 1 << int(device)
        return summary & 0xFFFF

    def primary_fault_code(self, latch_enabled: bool = True) -> int:
        for device, code in FAULT_PRIORITY:
            if self.fault_word(device, latch_enabled):
                return code
        return 0

    def primary_warning_code(self) -> int:
        for device, code in WARNING_PRIORITY:
            if self.warning_word(device):
                return code
        return 0

    def active_fault_count(self, latch_enabled: bool = True) -> int:
        return sum(
            count_bits(self.fault_word(device, latch_enabled))
            for device, _ in FAULT_PRIORITY
        )

    def active_warning_count(self) -> int:
        return sum(count_bits(self.warning_word(device)) for device, _ in WARNING_PRIORITY)


@dataclass
class CommandRuntime:
    last_code: int = 0
    last_seq: int = 0
    status: CommandStatus = CommandStatus.IDLE
    result: CommandResult = CommandResult.OK
    busy_since_ms: int = 0
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_code": self.last_code,
            "last_seq": self.last_seq,
            "status": self.status.name,
            "result": self.result.name,
            "busy_since_ms": self.busy_since_ms,
            "message": self.message,
        }
