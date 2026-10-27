import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from farm_core import APP_DIR

HISTORY_PATH = APP_DIR / "farm_history.db"
PERIODS = ("day", "week", "month")
RETENTION_SECONDS = 180 * 86400
PRUNE_INTERVAL = 86400
EVENT_LIMIT = 500
EVENT_COLUMNS = "id, printer_key, ts, event, state, file, parts, duration, message"
SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    printer_key TEXT NOT NULL,
    ts REAL NOT NULL,
    event TEXT NOT NULL,
    state TEXT,
    file TEXT,
    parts INTEGER,
    duration REAL,
    message TEXT
);
CREATE INDEX IF NOT EXISTS events_printer_ts ON events (printer_key, ts);
CREATE TABLE IF NOT EXISTS goals (
    period TEXT PRIMARY KEY,
    parts_target INTEGER NOT NULL DEFAULT 0,
    hours_target REAL NOT NULL DEFAULT 0
);
"""


def _event_type(previous, state, first):
    if state == "offline":
        return "offline"
    if state == "error":
        return "error"
    if not first:
        previous_state = str(previous.state or "") if previous is not None else ""
        if state == "paused" and previous_state == "printing":
            return "pause"
        if state == "printing" and previous_state == "paused":
            return "resume"
        if state == "idle" and previous_state in ("printing", "paused"):
            try:
                progress = float(previous.progress)
            except (TypeError, ValueError):
                progress = 0.0
            return "complete" if progress >= 0.99 else "cancelled"
    return "state"


def _error_message(error) -> str:
    if error is None:
        return ""
    label = str(getattr(error, "label", "") or "")
    code = str(getattr(error, "code", "") or "")
    if label and code:
        return f"{label} ({code})"
    return label or code


def _part_count(parts) -> int:
    total = 0
    for part in parts or []:
        if isinstance(part, dict):
            value = part.get("count")
        else:
            value = getattr(part, "count", 0)
        try:
            total += int(value or 0)
        except (TypeError, ValueError):
            continue
    return total


def _period_bounds(period: str, now: float) -> tuple[float, float]:
    moment = datetime.fromtimestamp(now)
    if period == "week":
        start = (moment - timedelta(days=moment.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = start + timedelta(days=7)
    elif period == "month":
        start = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
    else:
        start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    try:
        return start.timestamp(), end.timestamp()
    except (OSError, OverflowError):
        return now + (start - moment).total_seconds(), now + (end - moment).total_seconds()


class HistoryStore:
    def __init__(self, path=HISTORY_PATH, *, clock=time.time) -> None:
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.RLock()
        self._printing_since: dict[str, float] = {}
        self._last_prune = 0.0
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()
            self._prune(self._clock())
            self._conn.commit()

    def record(self, view, previous=None, now=None):
        moment = float(self._clock() if now is None else now)
        key = str(view.key)
        state = str(view.state or "")
        filename = str(view.file or "")
        if previous is not None and str(previous.state or "") == state and str(previous.file or "") == filename:
            return None
        previous_state = str(previous.state or "") if previous is not None else ""
        kind = _event_type(previous, state, previous is None)
        file_value = filename
        parts = None
        message = None
        if kind == "complete":
            file_value = str(previous.file or "") or filename
            parts = _part_count(previous.parts)
        if kind == "cancelled":
            file_value = str(previous.file or "") or filename
        if kind == "error":
            message = _error_message(view.error)
        with self._lock:
            duration = None
            if kind in ("complete", "cancelled"):
                start = self._printing_since.pop(key, None)
                if start is None:
                    row = self._last_event(key, moment)
                    if row is not None and str(row["state"] or "") in ("printing", "paused"):
                        start = float(row["ts"])
                if start is not None:
                    duration = max(0.0, moment - start)
            elif kind in ("error", "offline"):
                self._printing_since.pop(key, None)
            if state in ("printing", "paused"):
                stale = (
                    previous is None
                    or previous_state not in ("printing", "paused")
                    or str(previous.file or "") != filename
                )
                if stale:
                    start = None
                    if previous is None:
                        row = self._last_event(key, moment)
                        if (
                            row is not None
                            and str(row["state"] or "") in ("printing", "paused")
                            and str(row["file"] or "") == filename
                        ):
                            start = float(row["ts"])
                    self._printing_since[key] = moment if start is None else start
            cursor = self._conn.execute(
                "INSERT INTO events (printer_key, ts, event, state, file, parts, duration, message) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (key, moment, kind, state, file_value, parts, duration, message),
            )
            self._conn.commit()
            if moment - self._last_prune >= PRUNE_INTERVAL:
                self._prune(moment)
                self._conn.commit()
            row = self._conn.execute(
                f"SELECT {EVENT_COLUMNS} FROM events WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return dict(row) if row is not None else None

    def events(self, key, since=0, until=None, limit=EVENT_LIMIT) -> list[dict]:
        try:
            count = max(1, int(limit))
        except (TypeError, ValueError):
            count = EVENT_LIMIT
        sql = f"SELECT {EVENT_COLUMNS} FROM events WHERE printer_key = ? AND ts >= ?"
        params = [str(key), float(since)]
        if until is not None:
            sql += " AND ts <= ?"
            params.append(float(until))
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        params.append(count)
        with self._lock:
            rows = [dict(row) for row in self._conn.execute(sql, params)]
        rows.reverse()
        return rows

    def segments(self, key, since, until=None) -> list[dict]:
        since = float(since)
        now = float(self._clock())
        limit = now if until is None else min(float(until), now)
        if limit <= since:
            return []
        with self._lock:
            seed = self._conn.execute(
                f"SELECT {EVENT_COLUMNS} FROM events WHERE printer_key = ? AND ts <= ? "
                "ORDER BY ts DESC, id DESC LIMIT 1",
                (str(key), since),
            ).fetchone()
            rows = self._conn.execute(
                f"SELECT {EVENT_COLUMNS} FROM events WHERE printer_key = ? AND ts > ? AND ts <= ? "
                "ORDER BY ts ASC, id ASC",
                (str(key), since, limit),
            ).fetchall()
        result: list[dict] = []
        state = str(seed["state"] or "") if seed is not None else ""
        filename = str(seed["file"] or "") if seed is not None else ""
        start = since if seed is not None else None
        for row in rows:
            moment = float(row["ts"])
            row_state = str(row["state"] or "")
            if start is None:
                state = row_state
                filename = str(row["file"] or "")
                start = moment
                continue
            if row_state == state:
                if row["file"]:
                    filename = str(row["file"] or "")
                continue
            self._append(result, state, start, moment, filename)
            state = row_state
            filename = str(row["file"] or "")
            start = moment
        if start is not None:
            self._append(result, state, start, limit, filename)
        return result

    def last_completion(self, key) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {EVENT_COLUMNS} FROM events WHERE printer_key = ? AND event = 'complete' "
                "ORDER BY ts DESC, id DESC LIMIT 1",
                (str(key),),
            ).fetchone()
        return dict(row) if row is not None else None

    def plan(self, now=None) -> dict:
        moment = float(self._clock() if now is None else now)
        with self._lock:
            goals = {
                str(row["period"]): row
                for row in self._conn.execute("SELECT period, parts_target, hours_target FROM goals")
            }
            printers = [str(row[0]) for row in self._conn.execute("SELECT DISTINCT printer_key FROM events")]
            result = {}
            for period in PERIODS:
                start, end = _period_bounds(period, moment)
                parts_done = self._conn.execute(
                    "SELECT COALESCE(SUM(parts), 0) FROM events "
                    "WHERE event = 'complete' AND ts >= ? AND ts < ?",
                    (start, end),
                ).fetchone()[0] or 0
                hours = 0.0
                for key in printers:
                    for segment in self.segments(key, start, end):
                        if segment["state"] == "printing":
                            hours += float(segment["end"]) - float(segment["start"])
                goal = goals.get(period)
                parts_target = int(goal["parts_target"]) if goal is not None else 0
                hours_target = float(goal["hours_target"]) if goal is not None else 0.0
                parts_done = int(parts_done)
                result[period] = {
                    "period": period,
                    "start": start,
                    "end": end,
                    "parts_target": parts_target,
                    "hours_target": hours_target,
                    "parts_done": parts_done,
                    "hours_done": round(hours / 3600.0, 3),
                    "parts_percent": round(parts_done / parts_target * 100.0, 1) if parts_target > 0 else 0.0,
                    "hours_percent": round(hours / 3600.0 / hours_target * 100.0, 1) if hours_target > 0 else 0.0,
                }
            return result

    def set_goal(self, period, parts_target, hours_target) -> dict:
        if period not in PERIODS:
            raise ValueError(f"Неизвестный период: {period}")
        parts = int(parts_target)
        hours = float(hours_target)
        if parts < 0 or hours < 0:
            raise ValueError("Цели не могут быть отрицательными")
        with self._lock:
            self._conn.execute(
                "INSERT INTO goals (period, parts_target, hours_target) VALUES (?, ?, ?) "
                "ON CONFLICT(period) DO UPDATE SET "
                "parts_target = excluded.parts_target, hours_target = excluded.hours_target",
                (period, parts, hours),
            )
            self._conn.commit()
        return {"period": period, "parts_target": parts, "hours_target": hours}

    def summary(self, since, until=None) -> dict:
        since = float(since)
        end = float(self._clock() if until is None else until)
        with self._lock:
            rows = self._conn.execute(
                "SELECT printer_key, COUNT(*) AS jobs, COALESCE(SUM(parts), 0) AS parts FROM events "
                "WHERE event = 'complete' AND ts >= ? AND ts < ? GROUP BY printer_key ORDER BY printer_key",
                (since, end),
            ).fetchall()
            result = {"since": since, "until": end, "printers": {}}
            for row in rows:
                key = str(row["printer_key"])
                hours = 0.0
                for segment in self.segments(key, since, end):
                    if segment["state"] == "printing":
                        hours += float(segment["end"]) - float(segment["start"])
                result["printers"][key] = {
                    "complete": int(row["jobs"]),
                    "parts": int(row["parts"]),
                    "hours": round(hours / 3600.0, 3),
                }
            return result

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _last_event(self, key, until=None) -> sqlite3.Row | None:
        sql = f"SELECT {EVENT_COLUMNS} FROM events WHERE printer_key = ?"
        params = [str(key)]
        if until is not None:
            sql += " AND ts <= ?"
            params.append(float(until))
        sql += " ORDER BY ts DESC, id DESC LIMIT 1"
        return self._conn.execute(sql, params).fetchone()

    def forget(self, key) -> None:
        with self._lock:
            self._printing_since.pop(str(key), None)

    def _prune(self, moment: float) -> None:
        self._last_prune = moment
        self._conn.execute("DELETE FROM events WHERE ts < ?", (moment - RETENTION_SECONDS,))

    @staticmethod
    def _append(result, state, start, end, filename) -> None:
        if end <= start:
            return
        if result and result[-1]["state"] == state:
            result[-1]["end"] = end
            if filename:
                result[-1]["file"] = filename
            return
        result.append({"state": state, "start": start, "end": end, "file": filename})
