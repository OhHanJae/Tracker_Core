from __future__ import annotations

import struct
from dataclasses import dataclass, field

from src.common.status import (
    AlarmBook,
    CommandResult,
    CommandRuntime,
    CommandStatus,
    ControllerState,
    DeviceSummaryBit,
    TrackingState,
    bit,
)


WORD_COUNT = 100
BYTE_COUNT = WORD_COUNT * 2


def _u16(value: int) -> int:
    return int(value) & 0xFFFF


def _bit_index(bit_name: str | int | None) -> int | None:
    if bit_name is None or bit_name == "-":
        return None
    if isinstance(bit_name, int):
        return bit_name
    text = str(bit_name).strip().upper()
    if text in {"A", "B", "C", "D", "E", "F"}:
        return 10 + ord(text) - ord("A")
    return int(text)


def words_from_bytes(payload: bytes, word_count: int = WORD_COUNT) -> list[int]:
    data = payload[: word_count * 2].ljust(word_count * 2, b"\x00")
    return list(struct.unpack("<" + "H" * word_count, data))


def words_to_bytes(words: list[int], word_count: int = WORD_COUNT) -> bytes:
    normalized = [_u16(value) for value in words[:word_count]]
    normalized.extend([0] * (word_count - len(normalized)))
    return struct.pack("<" + "H" * word_count, *normalized)


def get_word_bit(words: list[int], word_index: int, bit_index: int) -> bool:
    if word_index >= len(words):
        return False
    return bool(words[word_index] & (1 << bit_index))


def set_word_bit(words: list[int], word_index: int, bit_index: int, enabled: bool) -> None:
    while len(words) <= word_index:
        words.append(0)
    if enabled:
        words[word_index] = _u16(words[word_index] | (1 << bit_index))
    else:
        words[word_index] = _u16(words[word_index] & ~(1 << bit_index))


@dataclass
class PlcInput:
    run_enable: bool = False
    tracking_enable: bool = False
    laser_enable: bool = False
    force_stop: bool = False
    heartbeat: int = 0
    tracking_tolerance_mm_x100: int = 0
    laser_tolerance_deg_x100: int = 0
    req_recipe_id: int = 0
    req_point_id: int = 0
    command_code: int = 0
    command_seq: int = 0
    admin_command: int = 0
    admin_seq: int = 0
    admin_param1: int = 0
    admin_param2: int = 0
    teach_enable_key: int = 0
    raw_words: list[int] = field(default_factory=lambda: [0] * WORD_COUNT)

    @classmethod
    def from_words(cls, words: list[int]) -> "PlcInput":
        padded = list(words[:WORD_COUNT])
        padded.extend([0] * (WORD_COUNT - len(padded)))
        return cls(
            run_enable=get_word_bit(padded, 0, 0),
            tracking_enable=get_word_bit(padded, 0, 2),
            laser_enable=get_word_bit(padded, 0, 3),
            force_stop=get_word_bit(padded, 0, 15),
            heartbeat=padded[7],
            tracking_tolerance_mm_x100=padded[8],
            laser_tolerance_deg_x100=padded[9],
            req_recipe_id=padded[10],
            req_point_id=padded[11],
            command_code=padded[12],
            command_seq=padded[13],
            admin_command=padded[40],
            admin_seq=padded[41],
            admin_param1=padded[42],
            admin_param2=padded[43],
            teach_enable_key=padded[49],
            raw_words=padded,
        )

    @classmethod
    def from_bytes(cls, payload: bytes) -> "PlcInput":
        return cls.from_words(words_from_bytes(payload))

    def applied_tracking_tolerance(self, default_value: int) -> int:
        if 1 <= self.tracking_tolerance_mm_x100 <= 10000:
            return self.tracking_tolerance_mm_x100
        return default_value

    def applied_laser_tolerance(self, default_value: int) -> int:
        if 1 <= self.laser_tolerance_deg_x100 <= 10000:
            return self.laser_tolerance_deg_x100
        return default_value


@dataclass
class CameraMasks:
    required: int = 0
    online: int = 0
    valid: int = 0


@dataclass
class ProcessFlags:
    controller_online: bool = True
    system_ready: bool = False
    tracking_active: bool = False
    homing: bool = False
    motion_moving: bool = False
    laser_on: bool = False
    homed: bool = False
    stop_active: bool = False
    force_stop_active: bool = False
    camera_ready: bool = False
    laser_target_ready: bool = False
    position_ok: bool = False
    position_stable: bool = False


@dataclass
class PlcOutput:
    flags: ProcessFlags = field(default_factory=ProcessFlags)
    controller_state: ControllerState = ControllerState.INIT
    tracking_state: TrackingState = TrackingState.OFF
    position_error_mm_x100: int = 0
    pan_error_deg_x100: int = 0
    tilt_error_deg_x100: int = 0
    heartbeat_return: int = 0
    active_tracking_tolerance_mm_x100: int = 0
    active_laser_tolerance_deg_x100: int = 0
    active_recipe_id: int = 0
    active_point_id: int = 0
    command: CommandRuntime = field(default_factory=CommandRuntime)
    cameras: CameraMasks = field(default_factory=CameraMasks)
    alarms: AlarmBook = field(default_factory=AlarmBook)
    admin_status: int = 0
    admin_result: int = 0
    admin_ack_seq: int = 0
    teach_status: int = 0
    latch_faults: bool = True

    def to_words(self) -> list[int]:
        words = [0] * WORD_COUNT
        warning_summary = self.alarms.warning_summary()
        fault_summary = self.alarms.fault_summary(self.latch_faults)
        warning_active = warning_summary != 0
        fault_active = fault_summary != 0
        process_stop = fault_active or self.flags.stop_active

        words[0] = (
            bit(self.flags.controller_online, 0)
            | bit(self.flags.system_ready, 1)
            | bit(self.flags.tracking_active, 2)
            | bit(self.flags.homing, 3)
            | bit(self.flags.motion_moving, 4)
            | bit(self.command.status == CommandStatus.BUSY, 5)
            | bit(self.flags.laser_on, 6)
            | bit(fault_active, 7)
            | bit(warning_active, 8)
            | bit(self.flags.homed, 10)
            | bit(self.flags.stop_active, 11)
            | bit(self.flags.force_stop_active, 15)
        )
        words[1] = (
            bit(self.flags.camera_ready, 0)
            | bit(self.flags.laser_target_ready, 12)
            | bit(self.flags.position_ok, 13)
            | bit(self.flags.position_stable, 14)
        )
        words[2] = int(self.controller_state)
        words[3] = int(self.tracking_state)
        words[4] = _u16(self.position_error_mm_x100)
        words[5] = _u16(self.pan_error_deg_x100)
        words[6] = _u16(self.tilt_error_deg_x100)
        words[7] = _u16(self.heartbeat_return)
        words[8] = _u16(self.active_tracking_tolerance_mm_x100)
        words[9] = _u16(self.active_laser_tolerance_deg_x100)
        words[10] = _u16(self.active_recipe_id)
        words[11] = _u16(self.active_point_id)
        words[12] = _u16(self.command.last_code)
        words[13] = _u16(self.command.last_seq)
        words[14] = int(self.command.status)
        words[15] = int(self.command.result)
        words[16] = _u16(self.cameras.required)
        words[17] = _u16(self.cameras.online)
        words[18] = _u16(self.cameras.valid)

        vision_degraded = bool(
            self.alarms.warning_word(DeviceSummaryBit.VISION_COMMON)
            or any(
                self.alarms.warning_word(device)
                for device in (
                    DeviceSummaryBit.CAMERA_1,
                    DeviceSummaryBit.CAMERA_2,
                    DeviceSummaryBit.CAMERA_3,
                    DeviceSummaryBit.CAMERA_4,
                )
            )
        )
        vision_fault = bool(
            self.alarms.fault_word(DeviceSummaryBit.VISION_COMMON, self.latch_faults)
            or any(
                self.alarms.fault_word(device, self.latch_faults)
                for device in (
                    DeviceSummaryBit.CAMERA_1,
                    DeviceSummaryBit.CAMERA_2,
                    DeviceSummaryBit.CAMERA_3,
                    DeviceSummaryBit.CAMERA_4,
                )
            )
        )
        tracking_fault = bool(
            self.alarms.fault_word(DeviceSummaryBit.TRACKER, self.latch_faults)
        )
        motion_fault = bool(
            self.alarms.fault_word(DeviceSummaryBit.PAN_MOTOR, self.latch_faults)
            or self.alarms.fault_word(DeviceSummaryBit.TILT_MOTOR, self.latch_faults)
        )

        words[20] = (
            bit(warning_active, 0)
            | bit(fault_active, 1)
            | bit(process_stop, 2)
            | bit(vision_degraded, 3)
            | bit(vision_fault, 4)
            | bit(tracking_fault, 5)
            | bit(motion_fault, 6)
            | bit(bool(self.alarms.fault_word(DeviceSummaryBit.LASER, self.latch_faults)), 7)
            | bit(bool(self.alarms.fault_word(DeviceSummaryBit.CONTROLLER, self.latch_faults)), 8)
            | bit(
                bool(
                    self.alarms.fault_word(
                        DeviceSummaryBit.PLC_COMMUNICATION, self.latch_faults
                    )
                ),
                9,
            )
            | bit(bool(self.alarms.fault_word(DeviceSummaryBit.RECIPE, self.latch_faults)), 10)
            | bit(
                bool(self.alarms.fault_word(DeviceSummaryBit.CALIBRATION, self.latch_faults)),
                11,
            )
        )
        words[21] = _u16(self.alarms.primary_fault_code(self.latch_faults))
        words[22] = _u16(self.alarms.primary_warning_code())
        words[23] = _u16(self.alarms.last_fault_code)
        words[24] = _u16(self.alarms.alarm_sequence)
        words[25] = _u16(fault_summary)
        words[26] = _u16(warning_summary)
        words[27] = _u16(self.alarms.active_fault_count(self.latch_faults))
        words[28] = _u16(self.alarms.active_warning_count())

        words[40] = _u16(self.admin_status)
        words[41] = _u16(self.admin_result)
        words[42] = _u16(self.admin_ack_seq)
        words[43] = _u16(self.teach_status)

        detail_map = {
            50: DeviceSummaryBit.CAMERA_1,
            52: DeviceSummaryBit.CAMERA_2,
            54: DeviceSummaryBit.CAMERA_3,
            56: DeviceSummaryBit.CAMERA_4,
            58: DeviceSummaryBit.VISION_COMMON,
            60: DeviceSummaryBit.TRACKER,
            62: DeviceSummaryBit.PAN_MOTOR,
            64: DeviceSummaryBit.TILT_MOTOR,
            66: DeviceSummaryBit.LASER,
            68: DeviceSummaryBit.CONTROLLER,
            70: DeviceSummaryBit.PLC_COMMUNICATION,
            72: DeviceSummaryBit.RECIPE,
            74: DeviceSummaryBit.CALIBRATION,
        }
        for word_index, device in detail_map.items():
            words[word_index] = self.alarms.warning_word(device)
            words[word_index + 1] = self.alarms.fault_word(device, self.latch_faults)

        return words

    def to_bytes(self) -> bytes:
        return words_to_bytes(self.to_words())
