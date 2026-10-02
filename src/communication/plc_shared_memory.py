from __future__ import annotations

import os
import struct
import time
from dataclasses import dataclass
from multiprocessing import shared_memory
from typing import Any


HEADER_FORMAT = "<4sHH9IiQQ"
HEADER_SIZE = 64
MAGIC = b"XGSM"


class SharedMemoryError(RuntimeError):
    pass


@dataclass
class SharedMemoryHeader:
    magic: bytes
    layout_version: int
    header_size: int
    total_size: int
    read_offset: int
    read_length: int
    write_offset: int
    write_length: int
    read_sequence: int
    write_sequence: int
    write_ack_sequence: int
    status_flags: int
    last_error_code: int
    last_read_time_ns: int
    last_write_time_ns: int

    @classmethod
    def unpack(cls, payload: bytes) -> "SharedMemoryHeader":
        values = struct.unpack(HEADER_FORMAT, payload[:HEADER_SIZE])
        return cls(*values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "magic": self.magic.decode("ascii", errors="replace"),
            "layout_version": self.layout_version,
            "header_size": self.header_size,
            "total_size": self.total_size,
            "read_offset": self.read_offset,
            "read_length": self.read_length,
            "write_offset": self.write_offset,
            "write_length": self.write_length,
            "read_sequence": self.read_sequence,
            "write_sequence": self.write_sequence,
            "write_ack_sequence": self.write_ack_sequence,
            "status_flags": self.status_flags,
            "last_error_code": self.last_error_code,
            "last_read_time_ns": self.last_read_time_ns,
            "last_write_time_ns": self.last_write_time_ns,
        }


class PlcSharedMemoryClient:
    def __init__(self, name: str) -> None:
        self.name = name
        self.connected_name: str | None = None
        self._shm: shared_memory.SharedMemory | None = None

    @property
    def connected(self) -> bool:
        return self._shm is not None

    def connect(self) -> None:
        if self._shm is not None:
            return
        errors: list[str] = []
        for candidate in shared_memory_name_candidates(self.name):
            try:
                self._shm = shared_memory.SharedMemory(name=candidate)
                self.connected_name = candidate
                return
            except FileNotFoundError as exc:
                errors.append(f"{candidate}: {exc}")
        detail = "; ".join(errors)
        raise SharedMemoryError(f"shared memory not found: {self.name} ({detail})")

    def close(self) -> None:
        if self._shm is None:
            return
        self._shm.close()
        self._shm = None
        self.connected_name = None

    def reconnect(self) -> None:
        self.close()
        self.connect()

    def header(self) -> SharedMemoryHeader:
        shm = self._require_shm()
        payload = bytes(shm.buf[:HEADER_SIZE])
        header = SharedMemoryHeader.unpack(payload)
        if header.magic != MAGIC:
            raise SharedMemoryError(
                f"invalid shared memory magic: {header.magic!r}, expected {MAGIC!r}"
            )
        if header.header_size != HEADER_SIZE:
            raise SharedMemoryError(f"unsupported header size: {header.header_size}")
        return header

    def read_plc_data(self, retry_count: int = 3, retry_delay_s: float = 0.002) -> bytes:
        return self._read_stable_area(
            area="read",
            retry_count=retry_count,
            retry_delay_s=retry_delay_s,
        )

    def read_write_data(
        self,
        retry_count: int = 3,
        retry_delay_s: float = 0.002,
    ) -> bytes:
        return self._read_stable_area(
            area="write",
            retry_count=retry_count,
            retry_delay_s=retry_delay_s,
        )

    def _read_stable_area(
        self,
        *,
        area: str,
        retry_count: int,
        retry_delay_s: float,
    ) -> bytes:
        shm = self._require_shm()
        for _ in range(retry_count):
            first = self.header()
            if area == "read":
                sequence = first.read_sequence
                start = first.read_offset
                length = first.read_length
            else:
                sequence = first.write_sequence
                start = first.write_offset
                length = first.write_length
            if sequence % 2 == 1:
                time.sleep(retry_delay_s)
                continue
            end = start + length
            if end > len(shm.buf):
                raise SharedMemoryError(f"{area} area exceeds shared memory size")
            payload = bytes(shm.buf[start:end])
            second = self.header()
            second_sequence = (
                second.read_sequence if area == "read" else second.write_sequence
            )
            if (
                sequence == second_sequence
                and second_sequence % 2 == 0
            ):
                return payload
            time.sleep(retry_delay_s)
        raise SharedMemoryError(f"failed to read a stable PLC {area} snapshot")

    def write_plc_data(self, payload: bytes) -> int:
        shm = self._require_shm()
        header = self.header()
        if len(payload) != header.write_length:
            raise SharedMemoryError(
                f"TX length mismatch: got {len(payload)}, expected {header.write_length}"
            )
        start = header.write_offset
        end = start + header.write_length
        if end > len(shm.buf):
            raise SharedMemoryError("TX area exceeds shared memory size")

        current = header.write_sequence
        odd = (current + 1) & 0xFFFFFFFF
        if odd % 2 == 0:
            odd = (odd + 1) & 0xFFFFFFFF
        even = (odd + 1) & 0xFFFFFFFF
        if even % 2 == 1:
            even = (even + 1) & 0xFFFFFFFF

        self._write_u32(32, odd)
        shm.buf[start:end] = payload
        self._write_u32(32, even)
        return even

    def write_acknowledged(self) -> bool:
        header = self.header()
        return (
            header.write_sequence == header.write_ack_sequence
            and header.write_sequence % 2 == 0
        )

    def _write_u32(self, offset: int, value: int) -> None:
        shm = self._require_shm()
        shm.buf[offset : offset + 4] = struct.pack("<I", value & 0xFFFFFFFF)

    def _require_shm(self) -> shared_memory.SharedMemory:
        if self._shm is None:
            raise SharedMemoryError("shared memory is not connected")
        return self._shm

    def __enter__(self) -> "PlcSharedMemoryClient":
        self.connect()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def shared_memory_name_candidates(name: str, platform_name: str | None = None) -> list[str]:
    platform_name = platform_name or os.name
    normalized = name.strip()
    if not normalized:
        return [normalized]
    if platform_name == "posix":
        if normalized.startswith("/"):
            return [normalized, normalized[1:]]
        return [normalized, f"/{normalized}"]
    return [normalized.lstrip("/")]
