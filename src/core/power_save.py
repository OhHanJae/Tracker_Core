"""PLC work-request activity, independent of UI polling and wall-clock changes."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any


class PowerSaveState:
    def __init__(self, timeout_s: float = 1800.0, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("power-save timeout must be positive and finite")
        self.timeout_s = float(timeout_s)
        self._clock = clock
        self._last_request_at = clock()
        self._last_request: tuple[int, int, int, int] | None = None
        self.sleeping = False
        self.last_wake_source = "startup"

    def wake(self, source: str) -> None:
        self.sleeping = False
        self._last_request_at = self._clock()
        self.last_wake_source = source

    def observe_request(self, command: int, sequence: int, recipe_id: int,
                        point_id: int, *, wake: bool = True) -> bool:
        if not 0 < command <= 65535 or not 0 <= sequence <= 65535:
            return False
        if not 1 <= recipe_id <= 65535 or not 1 <= point_id <= 65535:
            return False
        key = (command, sequence, recipe_id, point_id)
        if key == self._last_request:
            return False
        self._last_request = key
        self._last_request_at = self._clock()
        if wake:
            self.wake("plc.request")
        return True

    def tick(self, *, busy: bool = False) -> bool:
        if self.sleeping or busy or self.idle_seconds < self.timeout_s:
            return False
        self.sleeping = True
        return True

    @property
    def idle_seconds(self) -> float:
        return max(0.0, self._clock() - self._last_request_at)

    def snapshot(self) -> dict[str, Any]:
        return {
            "sleeping": self.sleeping,
            "last_wake_source": self.last_wake_source,
            "timeout_s": self.timeout_s,
            "idle_seconds": round(self.idle_seconds, 3),
            "remaining_s": round(max(0.0, self.timeout_s - self.idle_seconds), 3),
            "last_plc_request": list(self._last_request) if self._last_request else None,
        }
