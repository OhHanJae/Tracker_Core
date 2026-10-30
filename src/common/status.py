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
    NUTRUNNER_DATA_REQUEST = 5
    FASTENING_COMPLETE = 6
    # Legacy import alias; PLC command 5 requests nutrunner data, not a config reload.
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
    TESTING = 9
    MANUAL = 10


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
    PAN_TILT_MOTOR = 8
    PAN_MOTOR = 8
    TILT_MOTOR = 9
    LASER = 10
    RECIPE = 11
    CALIBRATION = 12


class PtmFaultMask(IntEnum):
    COMMUNICATION_LOST = 1 << 0
    DRIVER_FAULT = 1 << 1
    COMMUNICATION_TIMEOUT = 1 << 8


class VisionFaultMask(IntEnum):
    REQUIRED_CAMERA_UNAVAILABLE = 1 << 0
    PIPELINE_STOPPED = 1 << 2
    RESULT_TIMEOUT = 1 << 5
    COMMUNICATION_TIMEOUT = 1 << 6
    STATUS_STALE = 1 << 7
    COMMUNICATION_LOST = 1 << 8


VISION_COMMUNICATION_FAULT_MASK = (
    VisionFaultMask.COMMUNICATION_TIMEOUT
    | VisionFaultMask.STATUS_STALE
    | VisionFaultMask.COMMUNICATION_LOST
)


# Status faults recover from current data; motion, configuration and calibration
# faults still require an explicit reset.
AUTO_RESET_FAULT_MASKS = {
    DeviceSummaryBit.PLC_COMMUNICATION: 0x003F,
    DeviceSummaryBit.VISION_COMMON: (
        VisionFaultMask.REQUIRED_CAMERA_UNAVAILABLE
        | VisionFaultMask.RESULT_TIMEOUT
        | VISION_COMMUNICATION_FAULT_MASK
    ),
    DeviceSummaryBit.CAMERA_1: (1 << 0) | (1 << 1) | (1 << 6),
    DeviceSummaryBit.CAMERA_2: (1 << 0) | (1 << 1) | (1 << 6),
    DeviceSummaryBit.CAMERA_3: (1 << 0) | (1 << 1) | (1 << 6),
    DeviceSummaryBit.CAMERA_4: (1 << 0) | (1 << 1) | (1 << 6),
    DeviceSummaryBit.TRACKER: (1 << 0) | (1 << 1) | (1 << 2) | (1 << 4) | (1 << 6),
    DeviceSummaryBit.PAN_TILT_MOTOR: (
        PtmFaultMask.COMMUNICATION_LOST | PtmFaultMask.COMMUNICATION_TIMEOUT
    ),
    DeviceSummaryBit.TILT_MOTOR: (
        PtmFaultMask.COMMUNICATION_LOST | PtmFaultMask.COMMUNICATION_TIMEOUT
    ),
}


FAULT_PRIORITY = [
    (DeviceSummaryBit.CONTROLLER, 1001),
    (DeviceSummaryBit.PLC_COMMUNICATION, 1101),
    (DeviceSummaryBit.VISION_COMMON, 2001),
    (DeviceSummaryBit.CAMERA_1, 2101),
    (DeviceSummaryBit.CAMERA_2, 2201),
    (DeviceSummaryBit.CAMERA_3, 2301),
    (DeviceSummaryBit.CAMERA_4, 2401),
    (DeviceSummaryBit.TRACKER, 3001),
    (DeviceSummaryBit.PAN_TILT_MOTOR, 4001),
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
    (DeviceSummaryBit.PAN_TILT_MOTOR, 4000),
    (DeviceSummaryBit.RECIPE, 6000),
    (DeviceSummaryBit.CALIBRATION, 6100),
]


# PLC v6: low byte = device kind, high byte = detail number (old bit + 1).
# Kind 10 (separate Tilt) and 11 (GPIO laser) remain unallocated.
PLC_ALARM_KINDS = {device: int(device) + 1 for device, _ in FAULT_PRIORITY}


def plc_alarm_code(device: DeviceSummaryBit, detail_bit: int) -> int:
    """Legacy v6 API code; not a v7 PLC word."""
    return ((detail_bit + 1) << 8) | PLC_ALARM_KINDS[device]


PLC_ALARM_MODULES = {
    "core": (DeviceSummaryBit.CONTROLLER, DeviceSummaryBit.RECIPE, DeviceSummaryBit.CALIBRATION),
    "ptm": (DeviceSummaryBit.PAN_TILT_MOTOR,),
    "vision": (DeviceSummaryBit.VISION_COMMON, DeviceSummaryBit.CAMERA_1,
               DeviceSummaryBit.CAMERA_2, DeviceSummaryBit.CAMERA_3,
               DeviceSummaryBit.CAMERA_4, DeviceSummaryBit.TRACKER),
    "plc": (DeviceSummaryBit.PLC_COMMUNICATION,),
}
PLC_MODULE_WORD_OFFSETS = {"core": 21, "ptm": 22, "vision": 23, "plc": 24}
PLC_MODULE_CODE_BASES = {
    device: index * 16
    for devices in PLC_ALARM_MODULES.values()
    for index, device in enumerate(devices)
}
PLC_DEVICE_MODULES = {
    device: module for module, devices in PLC_ALARM_MODULES.items() for device in devices
}


def plc_module_alarm_code(device: DeviceSummaryBit, detail_bit: int) -> int:
    if device == DeviceSummaryBit.TILT_MOTOR:
        device = DeviceSummaryBit.PAN_TILT_MOTOR
    return PLC_MODULE_CODE_BASES[device] + detail_bit + 1


# Active PLC v5.2 alarm layout. Camera 2~4 and the GPIO-only laser have no
# allocated output alarms; their internal diagnostics are not changed here.
PLC_V5_2_DETAIL_OFFSETS = {
    DeviceSummaryBit.CAMERA_1: 50,
    DeviceSummaryBit.VISION_COMMON: 58,
    DeviceSummaryBit.TRACKER: 60,
    DeviceSummaryBit.PAN_TILT_MOTOR: 62,
    DeviceSummaryBit.CONTROLLER: 68,
    DeviceSummaryBit.PLC_COMMUNICATION: 70,
    DeviceSummaryBit.RECIPE: 72,
    DeviceSummaryBit.CALIBRATION: 74,
}
PLC_V5_2_DETAIL_MASKS = {
    DeviceSummaryBit.CAMERA_1: (0x00FF, 0x00FF),
    DeviceSummaryBit.VISION_COMMON: (0x0016, 0x003D),
    DeviceSummaryBit.TRACKER: (0x003F, 0x007F),
    DeviceSummaryBit.PAN_TILT_MOTOR: (0x003F, 0x00FF),
    DeviceSummaryBit.CONTROLLER: (0x00FF, 0x01FF),
    DeviceSummaryBit.PLC_COMMUNICATION: (0x000F, 0x003F),
    DeviceSummaryBit.RECIPE: (0x0007, 0x003F),
    DeviceSummaryBit.CALIBRATION: (0x0007, 0x007F),
}


def plc_v5_2_detail_word(device: DeviceSummaryBit, word: int, fault: bool) -> int:
    if device == DeviceSummaryBit.TILT_MOTOR:
        device = DeviceSummaryBit.PAN_TILT_MOTOR
    if fault:
        if device == DeviceSummaryBit.PAN_TILT_MOTOR and word & PtmFaultMask.COMMUNICATION_TIMEOUT:
            word |= PtmFaultMask.COMMUNICATION_LOST
        if device == DeviceSummaryBit.VISION_COMMON and word & VISION_COMMUNICATION_FAULT_MASK:
            word |= VisionFaultMask.RESULT_TIMEOUT
    return word & PLC_V5_2_DETAIL_MASKS.get(device, (0, 0))[int(fault)]


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
    suppressed_devices: set[int] = field(default_factory=set)
    last_plc_fault_code: int = 0

    def plc_v5_2(self, latch_enabled: bool = True) -> AlarmBook:
        """Project diagnostics onto the wire contract without changing recovery/latches."""
        last_code = self.visible_last_fault_code()
        allowed_codes = {code for device, code in FAULT_PRIORITY if device in PLC_V5_2_DETAIL_OFFSETS}
        return AlarmBook(
            warnings={int(device): plc_v5_2_detail_word(device, self.warning_word(device), False)
                      for device in PLC_V5_2_DETAIL_OFFSETS},
            faults={int(device): plc_v5_2_detail_word(device, self.fault_word(device, latch_enabled), True)
                    for device in PLC_V5_2_DETAIL_OFFSETS},
            last_fault_code=last_code if last_code in allowed_codes else 0,
            alarm_sequence=self.alarm_sequence,
        )

    def set_warning_word(self, device: DeviceSummaryBit, word: int) -> None:
        canonical = DeviceSummaryBit.PAN_TILT_MOTOR if device == DeviceSummaryBit.TILT_MOTOR else device
        previous = self.warning_word(canonical)
        self.warnings[int(device)] = 0 if device == DeviceSummaryBit.LASER else word & 0xFFFF
        if self.warning_word(canonical) & ~previous:
            self.alarm_sequence = (self.alarm_sequence + 1) & 0xFFFF

    def clear_device(self, device: DeviceSummaryBit) -> None:
        self.warnings.pop(int(device), None)
        self.faults.pop(int(device), None)
        self.latched_faults.pop(int(device), None)

    def set_fault_word(
        self, device: DeviceSummaryBit, word: int, latch_enabled: bool = True
    ) -> None:
        canonical = DeviceSummaryBit.PAN_TILT_MOTOR if device == DeviceSummaryBit.TILT_MOTOR else device
        previous = self.fault_word(canonical, False)
        word &= 0xFFFF
        if device == DeviceSummaryBit.LASER:
            word = 0  # GPIO output only; no laser warning/fault feedback exists.
            self.latched_faults[int(device)] = 0
        self.faults[int(device)] = word
        if latch_enabled:
            previous_latched = self.latched_faults.get(int(device), 0)
            self.latched_faults[int(device)] = (
                (previous_latched | word) & ~AUTO_RESET_FAULT_MASKS.get(device, 0)
            )
        raised = self.fault_word(canonical, False) & ~previous
        if raised and canonical in PLC_ALARM_KINDS:
            self.last_plc_fault_code = plc_alarm_code(canonical, (raised & -raised).bit_length() - 1)
            self.alarm_sequence = (self.alarm_sequence + 1) & 0xFFFF
        if word:
            code = self.primary_fault_code()
            if code and code != self.last_fault_code:
                self.last_fault_code = code

    def reset_latched_faults(self) -> None:
        self.latched_faults.clear()

    def fault_word(self, device: DeviceSummaryBit, latch_enabled: bool = True) -> int:
        if device == DeviceSummaryBit.LASER:
            return 0
        if int(device) in self.suppressed_devices:
            return 0
        active = self.faults.get(int(device), 0)
        retained = self.latched_faults.get(int(device), 0) if latch_enabled else 0
        word = active | (retained & ~AUTO_RESET_FAULT_MASKS.get(device, 0))
        if device == DeviceSummaryBit.PAN_TILT_MOTOR:
            word |= self.fault_word(DeviceSummaryBit.TILT_MOTOR, latch_enabled)
        return word & 0xFFFF

    def warning_word(self, device: DeviceSummaryBit) -> int:
        if device == DeviceSummaryBit.LASER:
            return 0
        if int(device) in self.suppressed_devices:
            return 0
        word = self.warnings.get(int(device), 0)
        if device == DeviceSummaryBit.PAN_TILT_MOTOR:
            word |= self.warning_word(DeviceSummaryBit.TILT_MOTOR)
        return word & 0xFFFF

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

    def visible_last_fault_code(self) -> int:
        if 5000 <= self.last_fault_code < 5100:
            return 0
        for device, code in FAULT_PRIORITY:
            if code == self.last_fault_code and int(device) in self.suppressed_devices:
                return 0
        return self.last_fault_code

    def primary_warning_code(self) -> int:
        for device, code in WARNING_PRIORITY:
            if self.warning_word(device):
                return code
        return 0

    def plc_fault_code(self, latch_enabled: bool = True) -> int:
        for device, _ in FAULT_PRIORITY:
            word = self.fault_word(device, latch_enabled)
            if word:
                return plc_alarm_code(device, (word & -word).bit_length() - 1)
        return 0

    def plc_warning_code(self) -> int:
        for device, _ in WARNING_PRIORITY:
            word = self.warning_word(device)
            if word:
                return plc_alarm_code(device, (word & -word).bit_length() - 1)
        return 0

    def visible_last_plc_fault_code(self) -> int:
        kind = self.last_plc_fault_code & 0xFF
        if any(PLC_ALARM_KINDS[device] == kind and int(device) in self.suppressed_devices
               for device, _ in FAULT_PRIORITY):
            return 0
        return self.last_plc_fault_code

    def module_alarm_codes(self, module: str, latch_enabled: bool = True) -> tuple[int, int]:
        fault_code = warning_code = 0
        for device in PLC_ALARM_MODULES[module]:
            fault = self.fault_word(device, latch_enabled)
            warning = self.warning_word(device)
            if fault and not fault_code:
                fault_code = plc_module_alarm_code(device, (fault & -fault).bit_length() - 1)
            if warning and not warning_code:
                warning_code = plc_module_alarm_code(device, (warning & -warning).bit_length() - 1)
        return fault_code, warning_code

    def module_alarm_status(self, latch_enabled: bool = True) -> dict[str, dict[str, int]]:
        result = {}
        for module, offset in PLC_MODULE_WORD_OFFSETS.items():
            fault, warning = self.module_alarm_codes(module, latch_enabled)
            result[module] = {"word_offset": offset, "word": (fault << 8) | warning,
                              "fault_code": fault, "warning_code": warning}
        return result

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
