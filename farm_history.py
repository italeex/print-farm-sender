import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from farm_core import APP_DIR
from farm_plan import part_key

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
    message TEXT,
    parts_json TEXT
);
CREATE INDEX IF NOT EXISTS events_printer_ts ON events (printer_key, ts);
CREATE TABLE IF NOT EXISTS goals (
    period TEXT PRIMARY KEY,
    parts_target INTEGER NOT NULL DEFAULT 0,
    hours_target REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS plan_items (
    date TEXT NOT NULL,
    part_key TEXT NOT NULL,
    code TEXT,
    name TEXT,
    target INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, part_key)
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


def _parts_payload(parts) -> list[dict]:
    result = []
    for part in parts or []:
        if isinstance(part, dict):
            name = part.get("name")
            count = part.get("count")
        else:
            name = getattr(part, "name", "")
            count = getattr(part, "count", 0)
        try:
            amount = int(count or 0)
        except (TypeError, ValueError):
            continue
        result.append({"name": str(name or ""), "count": amount})
    return result


def _day_of(value: float) -> str:
    try:
        return datetime.fromtimestamp(value).date().isoformat()
    except (OSError, OverflowError, ValueError):
        return (datetime(1970, 1, 1) + timedelta(seconds=value)).date().isoformat()


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
            self._migrate()
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
        parts_json = None
        message = None
        if kind == "complete":
            file_value = str(previous.file or "") or filename
            parts = _part_count(previous.parts)
            parts_json = json.dumps(_parts_payload(previous.parts), ensure_ascii=False)
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
                "INSERT INTO events (printer_key, ts, event, state, file, parts, duration, message, parts_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (key, moment, kind, state, file_value, parts, duration, message, parts_json),
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
                items = self._period_items(start, end, self._actuals(start, end))
                if items:
                    parts_target = sum(item["target"] for item in items)
                    parts_done = sum(item["actual"] for item in items)
                else:
                    parts_target = 0
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
                if goal is not None and not items:
                    parts_target = int(goal["parts_target"])
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
                    "items": items,
                }
            return result

    def import_plan(self, items) -> dict:
        rows = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            day = str(item.get("date") or "").strip()
            key = str(item.get("key") or item.get("part_key") or "").strip()
            try:
                target = int(item.get("target") or 0)
            except (TypeError, ValueError):
                continue
            if not day or not key or target <= 0:
                continue
            rows.append((day, key, str(item.get("code") or ""), str(item.get("name") or ""), target))
        days = sorted({row[0] for row in rows})
        with self._lock:
            if days:
                marks = ", ".join("?" for _ in days)
                self._conn.execute(f"DELETE FROM plan_items WHERE date IN ({marks})", days)
                self._conn.executemany(
                    "INSERT OR REPLACE INTO plan_items (date, part_key, code, name, target) "
                    "VALUES (?, ?, ?, ?, ?)",
                    rows,
                )
                self._conn.commit()
        return {"imported": len(rows), "days": len(days)}

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

    def _period_items(self, start, end, actuals) -> list[dict]:
        first = _day_of(start)
        last = _day_of(end - 1.0)
        entries: dict[str, dict] = {}
        for row in self._conn.execute(
            "SELECT date, part_key, code, name, target FROM plan_items "
            "WHERE date >= ? AND date <= ? ORDER BY date, part_key",
            (first, last),
        ):
            key = str(row["part_key"])
            entry = entries.get(key)
            if entry is None:
                entry = entries[key] = {
                    "key": key,
                    "code": str(row["code"] or ""),
                    "name": str(row["name"] or ""),
                    "target": 0,
                    "actual": 0,
                    "days": {},
                }
            target = int(row["target"] or 0)
            day = str(row["date"])
            bucket = entry["days"].setdefault(day, {"date": day, "target": 0, "actual": 0})
            bucket["target"] += target
            entry["target"] += target
        for (day, key), count in actuals.items():
            entry = entries.get(key)
            if entry is None:
                continue
            entry["actual"] += count
            entry["days"].setdefault(day, {"date": day, "target": 0, "actual": 0})["actual"] += count
        items = []
        for key in sorted(entries):
            entry = entries[key]
            entry["percent"] = round(entry["actual"] / entry["target"] * 100.0, 1) if entry["target"] > 0 else 0.0
            entry["days"] = [entry["days"][day] for day in sorted(entry["days"])]
            items.append(entry)
        return items

    def _actuals(self, start, end) -> dict[tuple[str, str], int]:
        totals: dict[tuple[str, str], int] = {}
        rows = self._conn.execute(
            "SELECT ts, parts_json FROM events WHERE event = 'complete' "
            "AND parts_json IS NOT NULL AND ts >= ? AND ts < ?",
            (start, end),
        )
        for row in rows:
            try:
                day = _day_of(float(row["ts"]))
                parts = json.loads(row["parts_json"] or "[]")
            except (OverflowError, ValueError):
                continue
            if not isinstance(parts, list):
                continue
            for part in parts:
                if not isinstance(part, dict):
                    continue
                key = part_key(part.get("name") or "")
                if not key:
                    continue
                try:
                    count = int(part.get("count") or 0)
                except (TypeError, ValueError):
                    continue
                if count <= 0:
                    continue
                marker = (day, key)
                totals[marker] = totals.get(marker, 0) + count
        return totals

    def _migrate(self) -> None:
        columns = {str(row[1]) for row in self._conn.execute("PRAGMA table_info(events)")}
        if "parts_json" not in columns:
            self._conn.execute("ALTER TABLE events ADD COLUMN parts_json TEXT")

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
