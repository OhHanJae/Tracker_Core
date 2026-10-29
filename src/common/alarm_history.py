from __future__ import annotations

import asyncio
import copy
import json
import logging
import queue
import sqlite3
import threading
import time
import uuid
from concurrent.futures import Future
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.common.status import (
    AUTO_RESET_FAULT_MASKS, FAULT_PRIORITY, WARNING_PRIORITY, AlarmBook, DeviceSummaryBit,
    PLC_DEVICE_MODULES, PLC_MODULE_WORD_OFFSETS, plc_module_alarm_code,
)


MAP_VERSION = "v7"
ALARM_DEVICES = (3, 4, 2, 7, 8, 0, 1, 11, 12)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class AlarmHistory:
    """Observe raw alarm causes in memory; one bounded worker owns all disk writes."""

    def __init__(self, path: Path, map_path: Path, retention_days: int = 30) -> None:
        self.path = path
        self.retention_days = retention_days
        self._metadata: dict[tuple[str, str, int], dict[str, Any]] = {}
        self._current: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=128)
        self._thread: threading.Thread | None = None
        self._stop_writer = threading.Event()
        self._retry_count = 0
        self._ready: Future[None] = Future()
        self._last_error = ""
        self._dropped_updates = 0
        self._session = uuid.uuid4().hex
        self._map_path = map_path

    async def start(self) -> None:
        if self._thread is not None:
            return
        if self._ready.done():
            self._ready = Future()
            self._session = uuid.uuid4().hex
            for row in self._current.values():
                old = dict(row, status="unknown", cause_active=None, latched=None,
                           cause_observed=False, unverified_at=utc_stamp())
                self._pending[old["occurrence_id"]] = old
            self._current.clear()
        self._stop_writer.clear()
        self._thread = threading.Thread(target=self._run, name="core-alarm-history", daemon=True)
        self._thread.start()
        # Startup happens before the control loop starts.
        await asyncio.wrap_future(self._ready)
        if self._pending and self._thread.is_alive():
            await asyncio.to_thread(self._queue.put, ("write", list(self._pending.values()), None), True, 1)
            self._pending.clear()

    async def close(self) -> None:
        if self._thread is None:
            return
        if self._pending and self._thread.is_alive():
            try:
                await asyncio.to_thread(self._queue.put, ("write", list(self._pending.values()), None), True, 2)
                self._pending.clear()
            except queue.Full:
                self._last_error = "alarm history pending updates could not be drained"
        self._stop_writer.set()
        await asyncio.to_thread(self._thread.join, 5)
        if self._thread.is_alive():
            self._last_error = "alarm history writer did not stop within 5 seconds"
        else:
            self._thread = None

    def recording_status(self) -> dict[str, Any]:
        return {"running": bool(self._thread and self._thread.is_alive()),
                "retention_days": self.retention_days, "last_error": self._last_error,
                "dropped_updates": self._dropped_updates,
                "pending_updates": len(self._pending) + self._retry_count}

    def _load_metadata(self) -> None:
        text = self._map_path.read_text(encoding="utf-8")
        data = json.loads(text[text.index("{"):text.rindex("}") + 1])
        for sheet in data["sheets"]:
            if sheet["key"] != "03_Device_Alarm_Detail":
                continue
            for row in sheet["rows"]:
                if len(row) < 9 or str(row[4]).lower().startswith("reserved"):
                    continue
                try:
                    code = int(row[3])
                except ValueError:
                    continue
                self._metadata[str(row[0]), str(row[1]), code] = {"message": row[4], "description": row[8],
                                              "clear_policy": row[6], "device_name": row[0]}

    def capture(self, book: AlarmBook, latch_enabled: bool, *, reset: bool = False) -> None:
        """Called after a cycle or reset; never waits for storage or device I/O."""
        stamp = utc_stamp()
        rows: dict[str, dict[str, Any]] = {}
        fault_codes, warning_codes = dict(FAULT_PRIORITY), dict(WARNING_PRIORITY)
        for device in ALARM_DEVICES:
            suppressed = device in book.suppressed_devices
            for severity, values, code_map in (("Warning", book.warnings, warning_codes),
                                               ("Fault", book.faults, fault_codes)):
                active = values.get(device, 0)
                retained = (book.latched_faults.get(device, 0) & ~AUTO_RESET_FAULT_MASKS.get(device, 0)
                            if severity == "Fault" and latch_enabled else 0)
                if device == int(DeviceSummaryBit.PAN_TILT_MOTOR):
                    active |= values.get(int(DeviceSummaryBit.TILT_MOTOR), 0)
                    if severity == "Fault" and latch_enabled:
                        retained |= (book.latched_faults.get(int(DeviceSummaryBit.TILT_MOTOR), 0)
                                     & ~AUTO_RESET_FAULT_MASKS.get(DeviceSummaryBit.TILT_MOTOR, 0))
                if device == int(DeviceSummaryBit.LASER):
                    active = retained = 0
                for bit in range(16):
                    key = f"{device}:{severity}:{bit}"
                    cause, latched = bool(active & (1 << bit)), bool(retained & (1 << bit))
                    previous = self._current.get(key)
                    # Bypass clears output/internal words in existing Core code. It is
                    # not evidence that a previously observed physical cause recovered.
                    if suppressed and previous is not None:
                        cause, latched = previous["cause_active"], previous["latched"]
                    if not cause and not latched and previous is None:
                        continue
                    if previous is None:
                        module = PLC_DEVICE_MODULES[DeviceSummaryBit(device)]
                        alarm_offset = PLC_MODULE_WORD_OFFSETS[module]
                        byte_code = plc_module_alarm_code(DeviceSummaryBit(device), bit)
                        packed_code = byte_code << 8 if severity == "Fault" else byte_code
                        meta = self._metadata.get((module, severity, byte_code), {})
                        row = {"occurrence_id": uuid.uuid4().hex, "alarm_key": key,
                               "session_id": self._session, "device": DeviceSummaryBit(device).name.lower(),
                               "severity": severity, "code": code_map.get(device),
                               "relative_address": f"D+{alarm_offset}", "bit": bit,
                               "plc_code": packed_code, "module": module,
                               "byte_code": byte_code, "byte": "high" if severity == "Fault" else "low",
                               "map_version": MAP_VERSION, "message": meta.get("message", f"{severity} detail {bit + 1}"),
                               "description": meta.get("description", "Unmapped diagnostic code"),
                               "clear_policy": meta.get("clear_policy", "unknown"),
                               "occurred_at": stamp, "recovered_at": None, "closed_at": None,
                               "reset_result": None}
                    else:
                        row = dict(previous)
                    row.update(cause_active=cause, latched=latched, suppressed=suppressed,
                               cause_observed=not suppressed,
                               status="active" if cause else "latched" if latched else "recovered")
                    if previous is not None and previous["cause_active"] and not cause:
                        row["recovered_at"] = stamp
                    if previous is not None and not previous["cause_active"] and cause:
                        # A recurrence while a latch is retained is still a new occurrence.
                        old = dict(previous, status="recovered", closed_at=stamp)
                        rows[old["occurrence_id"]] = old
                        row.update(occurrence_id=uuid.uuid4().hex, occurred_at=stamp,
                                   recovered_at=None, closed_at=None, reset_result=None)
                    if reset:
                        row["reset_result"] = {"timestamp": stamp, "cause_still_active": cause,
                                               "latch_cleared": not latched}
                    if not cause and not latched:
                        row["closed_at"] = stamp
                        self._current.pop(key, None)
                    else:
                        self._current[key] = row
                    if previous != row:
                        rows[row["occurrence_id"]] = row
        self._pending.update(rows)
        if not self._pending or self._thread is None:
            return
        try:
            self._queue.put_nowait(("write", list(self._pending.values()), None))
            self._pending.clear()
        except queue.Full:
            self._dropped_updates += 1
            self._last_error = "alarm history queue full; storage is behind control loop"
            # Bound memory even under a prolonged disk failure; expose any loss.
            if len(self._pending) > 4096:
                self._pending = dict(list(self._pending.items())[-4096:])

    def active(self) -> dict[str, Any]:
        entries = copy.deepcopy(list(self._current.values()))
        entries.sort(key=lambda row: (row["severity"] != "Fault", row["occurred_at"]), reverse=False)
        return {"schema_version": 1, "session_id": self._session, "entries": entries, "total": len(entries),
                "next_cursor": None, "recording": self.recording_status()}

    async def query(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._thread is None or not self._thread.is_alive():
            result = await asyncio.to_thread(self.read_database, self.path, params)
        else:
            future: Future[dict[str, Any]] = Future()
            try:
                await asyncio.to_thread(self._queue.put, ("query", dict(params), future), True, 1)
                result = await asyncio.wait_for(asyncio.wrap_future(future), timeout=3)
            except queue.Full as exc:
                raise RuntimeError("alarm history query queue is busy") from exc
            except asyncio.TimeoutError as exc:
                raise RuntimeError("alarm history query timed out") from exc
        return {"schema_version": 1, "session_id": self._session, **result, "recording": self.recording_status()}

    @staticmethod
    def _query(connection: sqlite3.Connection, params: dict[str, Any]) -> dict[str, Any]:
        limit = max(1, min(500, int(params.get("limit", 100))))
        clauses, values = [], []
        for key in ("device", "severity", "status"):
            if params.get(key):
                clauses.append(f"{key} = ?")
                values.append(str(params[key]))
        for key, operator in (("from", ">="), ("to", "<=")):
            if params.get(key):
                stamp = datetime.fromisoformat(str(params[key]).replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ValueError(f"{key} timestamp must include a timezone")
                clauses.append(f"occurred_at {operator} ?")
                values.append(stamp.astimezone(timezone.utc).isoformat(timespec="milliseconds"))
        if params.get("search"):
            clauses.append("payload LIKE ?")
            values.append("%" + str(params["search"])[:256] + "%")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        total = connection.execute("SELECT COUNT(*) FROM occurrences" + where, values).fetchone()[0]
        if params.get("cursor") is not None:
            clauses.append("seq < ?")
            values.append(int(params["cursor"]))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        records = connection.execute("SELECT seq, payload FROM occurrences" + where +
                                     " ORDER BY seq DESC LIMIT ?", [*values, limit + 1]).fetchall()
        entries = [dict(json.loads(payload), cursor=seq) for seq, payload in records[:limit]]
        return {"entries": entries, "total": total,
                "next_cursor": str(records[limit - 1][0]) if len(records) > limit else None}

    @staticmethod
    def read_database(path: Path, params: dict[str, Any]) -> dict[str, Any]:
        if not path.exists():
            return {"entries": [], "total": 0, "next_cursor": None}
        # Read-only URI prevents a management request from creating/altering storage.
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
            return AlarmHistory._query(connection, params)

    def _run(self) -> None:
        connection = None
        retry_rows: dict[str, dict[str, Any]] = {}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=1)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS occurrences (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                               "id TEXT UNIQUE NOT NULL, device TEXT, severity TEXT, status TEXT, occurred_at TEXT, payload TEXT)")
            connection.execute("CREATE INDEX IF NOT EXISTS occurrence_filters ON occurrences(status, occurred_at)")
            self._load_metadata()
            for seq, payload in connection.execute("SELECT seq, payload FROM occurrences WHERE status IN ('active','latched')").fetchall():
                row = json.loads(payload)
                row.update(status="unknown", cause_active=None, latched=None,
                           cause_observed=False, unverified_at=utc_stamp())
                connection.execute("UPDATE occurrences SET status='unknown', payload=? WHERE seq=?", (json.dumps(row), seq))
            connection.commit()
            self._ready.set_result(None)
            last_prune = 0.0
            while True:
                stopping = False
                try:
                    item = self._queue.get(timeout=0.25)
                except queue.Empty:
                    item = ("retry", None, None)
                    stopping = self._stop_writer.is_set()
                kind, value, future = item
                if kind == "write":
                    retry_rows.update((row["occurrence_id"], row) for row in value)
                    if len(retry_rows) > 4096:
                        self._dropped_updates += len(retry_rows) - 4096
                        retry_rows = dict(list(retry_rows.items())[-4096:])
                self._retry_count = len(retry_rows)
                try:
                    if retry_rows:
                        for row in retry_rows.values():
                            connection.execute("INSERT INTO occurrences(id,device,severity,status,occurred_at,payload) VALUES(?,?,?,?,?,?) "
                                               "ON CONFLICT(id) DO UPDATE SET status=excluded.status,payload=excluded.payload",
                                               (row["occurrence_id"], row["device"], row["severity"], row["status"], row["occurred_at"],
                                                json.dumps(row, ensure_ascii=False)))
                        if time.monotonic() - last_prune > 3600:
                            cutoff = datetime.fromtimestamp(time.time() - self.retention_days * 86400, timezone.utc).isoformat(timespec="milliseconds")
                            connection.execute("DELETE FROM occurrences WHERE status IN ('recovered','unknown') AND occurred_at < ?", (cutoff,))
                            last_prune = time.monotonic()
                        connection.commit()
                        retry_rows.clear()
                        self._retry_count = 0
                    if kind == "query":
                        result = self._query(connection, value)
                        if not future.done():
                            future.set_result(result)
                except Exception as exc:
                    if not (future is not None and isinstance(exc, (ValueError, TypeError))):
                        if str(exc) != self._last_error:
                            logging.getLogger("core.alarm_history").error("Alarm history recording failed: %s", exc)
                        self._last_error = str(exc)
                    connection.rollback()
                    if future is not None:
                        if not future.done():
                            future.set_exception(exc)
                if stopping:
                    break
        except Exception as exc:
            self._last_error = str(exc)
            logging.getLogger("core.alarm_history").error("Alarm history writer unavailable: %s", exc)
            if not self._ready.done():
                # Recording failure is visible; it cannot prevent PLC operation.
                self._ready.set_result(None)
        finally:
            self._dropped_updates += len(retry_rows)
            if connection is not None:
                connection.close()
