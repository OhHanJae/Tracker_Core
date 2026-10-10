from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


class EventHistory(logging.Handler):
    """Bounded, persistent diagnostics shared by the web host and child processes."""

    def __init__(self, path: Path, capacity: int = 5000) -> None:
        super().__init__(logging.WARNING)
        self.entries: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._mutex = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        for archived in (*reversed([path.with_name(path.name + f".{n}") for n in range(1, 4)]), path):
            if archived.exists():
                with archived.open(encoding="utf-8") as stream:
                    for line in stream:
                        try:
                            self.entries.append(self._validate(json.loads(line)))
                        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                            continue
        self._file = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        self._file.setFormatter(logging.Formatter("%(message)s"))

    @staticmethod
    def _validate(entry: Any) -> dict[str, Any]:
        if not isinstance(entry, dict):
            raise ValueError("log entry must be an object")
        stamp = entry.get("timestamp")
        if not isinstance(stamp, str):
            raise ValueError("timestamp is required")
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        level = str(entry.get("level", "ERROR")).upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("invalid log level")
        source, message = entry.get("source"), entry.get("message")
        if not isinstance(source, str) or not isinstance(message, str):
            raise ValueError("source and message must be strings")
        normalized = dict(entry, timestamp=stamp, timestamp_ms=int(parsed.timestamp() * 1000), level=level)
        if len(json.dumps(normalized, ensure_ascii=False)) > 32768:
            raise ValueError("log entry exceeds 32 KiB")
        return normalized

    def add(self, source: str, level: str, message: str, *, event: str = "diagnostic",
            details: Any = None, timestamp_ms: int | None = None) -> None:
        stamp = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
        entry = self._validate({
            "timestamp": datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat(timespec="milliseconds"),
            "level": level, "source": source, "message": str(message)[:16000],
            "event": event, "details": details,
        })
        self._append(entry)

    def _append(self, entry: dict[str, Any]) -> None:
        with self._mutex:
            self.entries.append(entry)
            record = logging.LogRecord("history", logging.INFO, "", 0,
                                       json.dumps(entry, ensure_ascii=False), (), None)
            self._file.emit(record)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            details = logging.Formatter().formatException(record.exc_info) if record.exc_info else None
            self.add(record.name, record.levelname, record.getMessage(), details=details,
                     timestamp_ms=int(record.created * 1000))
        except Exception:
            self.handleError(record)

    def query(self, *, limit: int = 500, level: str = "", source: str = "") -> dict[str, Any]:
        with self._mutex:
            rows = [dict(entry) for entry in self.entries
                    if (not level or entry["level"] == level.upper())
                    and (not source or source.lower() in entry["source"].lower())]
        rows.sort(key=lambda entry: entry["timestamp_ms"])
        return {"entries": list(reversed(rows[-max(1, min(int(limit), 5000)):])), "total": len(rows)}

    def import_entries(self, entries: Any) -> int:
        if not isinstance(entries, list) or len(entries) > 5000:
            raise ValueError("entries must be an array of at most 5000 logs")
        # Validate the entire file before adding anything to existing history.
        validated = [self._validate(entry) for entry in entries]
        for entry in validated:
            self._append(entry)
        return len(validated)

    def close(self) -> None:
        with self._mutex:
            self._file.close()
        super().close()
