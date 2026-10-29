import json
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from farm_core import APP_DIR
from farm_plan import part_key, split_code_name

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
CREATE INDEX IF NOT EXISTS events_event_ts ON events (event, ts);
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
CREATE TABLE IF NOT EXISTS parts_catalog (
    part_key TEXT PRIMARY KEY,
    number INTEGER,
    code TEXT,
    name TEXT,
    file_path TEXT,
    file_size INTEGER,
    file_mtime REAL,
    updated_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS parts_catalog_number ON parts_catalog(number) WHERE number IS NOT NULL;
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
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


def _iso_day(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return ""


def qc_position_text(item) -> str:
    """Текст позиции ОТК: "Номер N · код · имя". Зеркало qcPositionText() в web/app.js."""
    data = item if isinstance(item, dict) else {}
    name = str(data.get("name") or "")
    code = str(data.get("code") or "")
    base = name or code
    if name and code and code != name:
        base = code + " · " + name
    number = data.get("number")
    text = "" if number is None else str(number).strip()
    if text:
        return "Номер " + text + (" · " + base if base else "")
    return base or "—"


def _period_dates(start: float, end: float) -> tuple[str, str, str]:
    first = date.fromisoformat(_day_of(start))
    last = date.fromisoformat(_day_of(end - 1.0))
    return first.isoformat(), last.isoformat(), (first - timedelta(days=first.weekday())).isoformat()


def _local_ts(moment: datetime) -> float:
    try:
        return moment.timestamp()
    except (OSError, OverflowError, ValueError):
        return (moment - datetime(1970, 1, 1)).total_seconds()


def _month_first(month) -> date:
    text = str(month or "").strip()
    parts = text.split("-")
    if (
        len(parts) != 2
        or len(parts[0]) != 4
        or len(parts[1]) != 2
        or not parts[0].isdigit()
        or not parts[1].isdigit()
    ):
        raise ValueError("Месяц должен быть в формате ГГГГ-ММ")
    try:
        return date(int(parts[0]), int(parts[1]), 1)
    except ValueError:
        raise ValueError("Месяц должен быть в формате ГГГГ-ММ") from None


def _next_month(first: date) -> date:
    if first.year == 9999 and first.month == 12:
        raise ValueError("Месяц должен быть не позже 9999-12")
    return date(first.year + first.month // 12, first.month % 12 + 1, 1)


def _calendar_items(entries: dict[str, dict]) -> list[dict]:
    items = []
    for key in sorted(entries):
        entry = entries[key]
        target = int(entry["target"])
        actual = int(entry["actual"])
        items.append(
            {
                "key": key,
                "code": str(entry["code"]),
                "name": str(entry["name"]),
                "target": target,
                "actual": actual,
                "in_progress": int(entry.get("in_progress", 0)),
                "percent": round(actual / target * 100.0, 1) if target > 0 else 0.0,
            }
        )
    return items


def _live_counts(live_parts) -> dict[str, dict]:
    counts: dict[str, dict] = {}
    for part in live_parts or []:
        if not isinstance(part, dict):
            continue
        key = str(part.get("key") or "").strip()
        if not key:
            continue
        try:
            count = int(part.get("count") or 0)
        except (TypeError, ValueError):
            continue
        if count <= 0:
            continue
        entry = counts.setdefault(key, {"code": "", "name": "", "in_progress": 0})
        if not entry["code"]:
            entry["code"] = str(part.get("code") or "")
        if not entry["name"]:
            entry["name"] = str(part.get("name") or "")
        entry["in_progress"] += count
    return counts


def _with_live(items: list[dict], counts: dict[str, dict]) -> list[dict]:
    result = []
    seen = set()
    for item in items:
        entry = counts.get(item["key"])
        item["in_progress"] = int(entry["in_progress"]) if entry is not None else 0
        seen.add(item["key"])
        result.append(item)
    for key in sorted(counts):
        if key in seen:
            continue
        entry = counts[key]
        result.append(
            {
                "key": key,
                "code": entry["code"],
                "name": entry["name"],
                "target": 0,
                "actual": 0,
                "percent": 0.0,
                "days": [],
                "in_progress": int(entry["in_progress"]),
            }
        )
    result.sort(key=lambda item: item["key"])
    return result


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

    def plan(self, now=None, live_parts=None) -> dict:
        moment = float(self._clock() if now is None else now)
        today = _day_of(moment)
        live = _live_counts(live_parts)
        with self._lock:
            goals = {
                str(row["period"]): row
                for row in self._conn.execute("SELECT period, parts_target, hours_target FROM goals")
            }
            printers = [str(row[0]) for row in self._conn.execute("SELECT DISTINCT printer_key FROM events")]
            result = {}
            for period in PERIODS:
                start, end = _period_bounds(period, moment)
                start_date, end_date, week_start = _period_dates(start, end)
                items = self._period_items(start, end, self._actuals(start, end))
                plan_rows = any(item["target"] > 0 for item in items)
                in_progress = live if start_date <= today <= end_date else {}
                items = _with_live(items, in_progress)
                numbers = self._catalog_numbers_locked()
                for item in items:
                    item.setdefault("number", numbers.get(item["key"]))
                parts_target = sum(item["target"] for item in items)
                parts_done = sum(item["actual"] for item in items)
                parts_in_progress = sum(item["in_progress"] for item in items)
                hours = 0.0
                for key in printers:
                    for segment in self.segments(key, start, end):
                        if segment["state"] == "printing":
                            hours += float(segment["end"]) - float(segment["start"])
                goal = goals.get(period)
                if goal is not None and not plan_rows:
                    parts_target = int(goal["parts_target"])
                hours_target = float(goal["hours_target"]) if goal is not None else 0.0
                parts_done = int(parts_done)
                result[period] = {
                    "period": period,
                    "start": start,
                    "end": end,
                    "start_date": start_date,
                    "end_date": end_date,
                    "week_start": week_start,
                    "parts_target": parts_target,
                    "hours_target": hours_target,
                    "parts_done": parts_done,
                    "parts_in_progress": parts_in_progress,
                    "hours_done": round(hours / 3600.0, 3),
                    "parts_percent": round(parts_done / parts_target * 100.0, 1) if parts_target > 0 else 0.0,
                    "hours_percent": round(hours / 3600.0 / hours_target * 100.0, 1) if hours_target > 0 else 0.0,
                    "items": items,
                }
            return result

    def calendar(self, month=None, now=None, live_parts=None) -> dict:
        moment = float(self._clock() if now is None else now)
        today = _day_of(moment)
        live = _live_counts(live_parts)
        first = _month_first(str(month or "").strip() or today[:7])
        following = _next_month(first)
        last = following - timedelta(days=1)
        span_first = first - timedelta(days=first.weekday())
        span_last = last + timedelta(days=6 - last.weekday())
        span_start = span_first.isoformat()
        span_end = span_last.isoformat()
        with self._lock:
            rows = self._conn.execute(
                "SELECT date, part_key, code, name, target FROM plan_items "
                "WHERE date >= ? AND date <= ? ORDER BY date, part_key",
                (span_start, span_end),
            ).fetchall()
            actuals = self._day_actuals(span_start, span_end)
        per_day: dict[str, dict[str, dict]] = {}
        for row in rows:
            day = str(row["date"])
            key = str(row["part_key"])
            entry = per_day.setdefault(day, {}).setdefault(
                key, {"code": "", "name": "", "target": 0, "actual": 0}
            )
            if not entry["code"]:
                entry["code"] = str(row["code"] or "")
            if not entry["name"]:
                entry["name"] = str(row["name"] or "")
            entry["target"] += int(row["target"] or 0)
        for (day, key), count in actuals.items():
            entry = per_day.setdefault(day, {}).setdefault(
                key, {"code": "", "name": "", "target": 0, "actual": 0}
            )
            entry["actual"] += count
        if live and span_start <= today <= span_end:
            for key, live_entry in live.items():
                entry = per_day.setdefault(today, {}).setdefault(
                    key, {"code": "", "name": "", "target": 0, "actual": 0}
                )
                if not entry["code"]:
                    entry["code"] = live_entry["code"]
                if not entry["name"]:
                    entry["name"] = live_entry["name"]
                entry["in_progress"] = int(live_entry["in_progress"])
        day_rows = {}
        for offset in range((span_last - span_first).days + 1):
            day = span_first + timedelta(days=offset)
            items = _calendar_items(per_day.get(day.isoformat(), {}))
            day_rows[day.isoformat()] = {
                "date": day.isoformat(),
                "weekday": day.weekday() + 1,
                "target": sum(item["target"] for item in items),
                "actual": sum(item["actual"] for item in items),
                "parts_in_progress": sum(item["in_progress"] for item in items),
                "items": items,
            }
        days = [day_rows[(first + timedelta(days=offset)).isoformat()] for offset in range((last - first).days + 1)]
        weeks = []
        cursor = span_first
        while cursor <= last:
            merged: dict[str, dict] = {}
            week_days = []
            for offset in range(7):
                row = day_rows[(cursor + timedelta(days=offset)).isoformat()]
                week_days.append({"date": row["date"], "target": row["target"], "actual": row["actual"]})
                for item in row["items"]:
                    entry = merged.setdefault(
                        item["key"],
                        {
                            "code": item["code"],
                            "name": item["name"],
                            "target": 0,
                            "actual": 0,
                            "in_progress": 0,
                        },
                    )
                    if not entry["code"]:
                        entry["code"] = item["code"]
                    if not entry["name"]:
                        entry["name"] = item["name"]
                    entry["target"] += item["target"]
                    entry["actual"] += item["actual"]
                    entry["in_progress"] += item["in_progress"]
            iso = cursor.isocalendar()
            weeks.append(
                {
                    "year": iso[0],
                    "week": iso[1],
                    "start": cursor.isoformat(),
                    "end": (cursor + timedelta(days=6)).isoformat(),
                    "target": sum(day["target"] for day in week_days),
                    "actual": sum(day["actual"] for day in week_days),
                    "parts_in_progress": sum(entry["in_progress"] for entry in merged.values()),
                    "items": _calendar_items(merged),
                    "days": week_days,
                }
            )
            cursor += timedelta(days=7)
        return {
            "month": first.isoformat()[:7],
            "month_start": first.isoformat(),
            "month_end": last.isoformat(),
            "today": today,
            "days": days,
            "weeks": weeks,
        }

    def qc(self, day: str, start_hour: int = 9) -> dict:
        try:
            target = date.fromisoformat(str(day or "").strip()[:10])
        except ValueError:
            raise ValueError("Дата должна быть в формате ГГГГ-ММ-ДД") from None
        try:
            hour = int(start_hour)
        except (TypeError, ValueError):
            raise ValueError("Час начала смены должен быть числом от 0 до 23") from None
        if hour < 0 or hour > 23:
            raise ValueError("Час начала смены должен быть числом от 0 до 23")
        start = _local_ts(datetime(target.year, target.month, target.day, hour) - timedelta(days=1))
        end = _local_ts(datetime(target.year, target.month, target.day, hour))
        with self._lock:
            rows = self._conn.execute(
                "SELECT parts_json FROM events "
                "WHERE event = 'complete' AND parts_json IS NOT NULL AND ts >= ? AND ts < ?",
                (start, end),
            ).fetchall()
            numbers = self._catalog_numbers_locked()
        entries: dict[str, dict] = {}
        for row in rows:
            try:
                parts = json.loads(row["parts_json"] or "[]")
            except ValueError:
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
                entry = entries.get(key)
                if entry is None:
                    code, name = split_code_name(part.get("name") or "")
                    entry = entries[key] = {"code": code, "name": name, "count": 0}
                entry["count"] += count
        items = [
            {
                "part_key": key,
                "number": numbers.get(key),
                "code": entry["code"],
                "name": entry["name"],
                "count": entry["count"],
            }
            for key, entry in entries.items()
        ]
        items.sort(key=lambda item: (item["number"] is None, item["number"] if item["number"] is not None else 0, item["name"]))
        return {
            "date": target.isoformat(),
            "start": start,
            "end": end,
            "items": items,
            "positions": len(items),
            "total": sum(item["count"] for item in items),
        }

    def import_plan(self, items, start=None, end=None) -> dict:
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
        if not rows:
            return {"imported": 0, "days": 0}
        days = sorted({row[0] for row in rows})
        first = _iso_day(start) or (days[0] if days else "")
        last = _iso_day(end) or (days[-1] if days else "")
        with self._lock:
            if first and last and first <= last:
                self._conn.execute(
                    "DELETE FROM plan_items WHERE date >= ? AND date <= ?", (first, last)
                )
            elif days:
                marks = ", ".join("?" for _ in days)
                self._conn.execute(f"DELETE FROM plan_items WHERE date IN ({marks})", days)
            if rows:
                self._conn.executemany(
                    "INSERT OR REPLACE INTO plan_items (date, part_key, code, name, target) "
                    "VALUES (?, ?, ?, ?, ?)",
                    rows,
                )
            if rows or first:
                self._conn.commit()
        return {"imported": len(rows), "days": len(days)}

    def set_goal(self, period, parts_target=None, hours_target=None) -> dict:
        if period not in PERIODS:
            raise ValueError(f"Неизвестный период: {period}")
        with self._lock:
            row = self._conn.execute(
                "SELECT parts_target, hours_target FROM goals WHERE period = ?", (period,)
            ).fetchone()
            if parts_target is None and row is not None:
                parts = int(row["parts_target"])
            else:
                parts = int(parts_target or 0)
            if hours_target is None and row is not None:
                hours = float(row["hours_target"])
            else:
                hours = float(hours_target or 0)
            if parts < 0 or hours < 0:
                raise ValueError("Цели не могут быть отрицательными")
            self._conn.execute(
                "INSERT INTO goals (period, parts_target, hours_target) VALUES (?, ?, ?) "
                "ON CONFLICT(period) DO UPDATE SET "
                "parts_target = excluded.parts_target, hours_target = excluded.hours_target",
                (period, parts, hours),
            )
            self._conn.commit()
        return {"period": period, "parts_target": parts, "hours_target": hours}

    def catalog(self) -> list[dict]:
        with self._lock:
            self._ensure_catalog_schema_locked()
            self._seed_catalog_plans_locked()
            self._seed_catalog_events_locked()
            self._conn.commit()
            rows = self._conn.execute(
                "SELECT part_key, number, code, name, file_path, file_size, file_mtime "
                "FROM parts_catalog "
                "ORDER BY CASE WHEN number IS NULL THEN 1 ELSE 0 END, number, part_key"
            ).fetchall()
        return [self._catalog_row(row) for row in rows]

    def catalog_map(self) -> dict[str, dict]:
        with self._lock:
            self._ensure_catalog_schema_locked()
            try:
                rows = self._conn.execute(
                    "SELECT part_key, number, code, name, file_path, file_size, file_mtime FROM parts_catalog"
                ).fetchall()
            except sqlite3.Error:
                return {}
        return {str(row["part_key"]): self._catalog_row(row) for row in rows}

    def set_catalog(self, part_key, number=None, file_path=None) -> dict:
        key = str(part_key or "").strip()
        if not key:
            raise ValueError("Не указан part_key")
        if number is not None:
            if isinstance(number, bool) or not isinstance(number, int):
                raise ValueError("Номер должен быть целым числом больше нуля")
            if number <= 0:
                raise ValueError("Номер должен быть целым числом больше нуля")
        file_value = None
        file_size = None
        file_mtime = None
        if file_path is not None:
            text = str(file_path).strip()
            if text:
                target = Path(text)
                if not target.is_file():
                    raise ValueError(f"Файл не найден: {target}")
                try:
                    stat = target.stat()
                except OSError:
                    raise ValueError("Не удалось прочитать файл") from None
                file_value = str(target)
                file_size = stat.st_size
                file_mtime = stat.st_mtime
        with self._lock:
            self._ensure_catalog_schema_locked()
            self._seed_catalog_plans_locked()
            self._seed_catalog_events_locked()
            self._conn.commit()
            if number is not None:
                clash = self._conn.execute(
                    "SELECT part_key FROM parts_catalog WHERE number = ? AND part_key <> ?",
                    (number, key),
                ).fetchone()
                if clash is not None:
                    raise ValueError(f"Номер {number} уже занят")
            row = self._conn.execute(
                "SELECT part_key, number, code, name, file_path, file_size, file_mtime "
                "FROM parts_catalog WHERE part_key = ?",
                (key,),
            ).fetchone()
            try:
                if row is None:
                    code, name = self._lookup_part_names_locked(key)
                    assigned = number
                    if assigned is None:
                        assigned = self._next_number_locked()
                    self._conn.execute(
                        "INSERT INTO parts_catalog (part_key, number, code, name, file_path, file_size, "
                        "file_mtime, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (key, assigned, code, name, file_value, file_size, file_mtime, self._clock()),
                    )
                else:
                    assigned = number if number is not None else row["number"]
                    if assigned is not None:
                        try:
                            assigned = int(assigned)
                        except (TypeError, ValueError):
                            assigned = None
                    if file_path is None:
                        file_value = row["file_path"]
                        file_size = row["file_size"]
                        file_mtime = row["file_mtime"]
                    self._conn.execute(
                        "UPDATE parts_catalog SET number = ?, file_path = ?, file_size = ?, "
                        "file_mtime = ?, updated_at = ? WHERE part_key = ?",
                        (assigned, file_value, file_size, file_mtime, self._clock(), key),
                    )
                self._conn.commit()
            except sqlite3.IntegrityError:
                self._conn.rollback()
                raise ValueError(f"Номер {assigned} уже занят") from None
            fresh = self._conn.execute(
                "SELECT part_key, number, code, name, file_path, file_size, file_mtime "
                "FROM parts_catalog WHERE part_key = ?",
                (key,),
            ).fetchone()
        return self._catalog_row(fresh)

    @staticmethod
    def _catalog_row(row) -> dict:
        try:
            number = int(row["number"]) if row["number"] is not None else None
        except (TypeError, ValueError):
            number = None
        try:
            size = int(row["file_size"]) if row["file_size"] is not None else None
        except (TypeError, ValueError):
            size = None
        try:
            mtime = float(row["file_mtime"]) if row["file_mtime"] is not None else None
        except (TypeError, ValueError):
            mtime = None
        return {
            "part_key": str(row["part_key"]),
            "number": number,
            "code": str(row["code"] or ""),
            "name": str(row["name"] or ""),
            "file_path": str(row["file_path"]) if row["file_path"] else None,
            "file_size": size,
            "file_mtime": mtime,
        }

    def _ensure_catalog_schema_locked(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS parts_catalog ("
            "part_key TEXT PRIMARY KEY, number INTEGER, code TEXT, name TEXT, "
            "file_path TEXT, file_size INTEGER, file_mtime REAL, updated_at REAL)"
        )
        catalog_columns = {str(row[1]) for row in self._conn.execute("PRAGMA table_info(parts_catalog)")}
        for column, kind in (
            ("number", "INTEGER"),
            ("code", "TEXT"),
            ("name", "TEXT"),
            ("file_path", "TEXT"),
            ("file_size", "INTEGER"),
            ("file_mtime", "REAL"),
            ("updated_at", "REAL"),
        ):
            if column not in catalog_columns:
                self._conn.execute(f"ALTER TABLE parts_catalog ADD COLUMN {column} {kind}")
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS parts_catalog_number "
            "ON parts_catalog(number) WHERE number IS NOT NULL"
        )
        self._conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")

    def _seed_catalog_plans_locked(self) -> None:
        known: dict[str, tuple[str, str]] = {}
        for row in self._conn.execute("SELECT DISTINCT part_key, code, name FROM plan_items"):
            key = str(row["part_key"] or "").strip()
            if key and key not in known:
                known[key] = (str(row["code"] or ""), str(row["name"] or ""))
        if known:
            self._insert_catalog_missing_locked(known)

    def _seed_catalog_events_locked(self) -> None:
        try:
            last = self._catalog_event_mark_locked()
            rows = self._conn.execute(
                "SELECT id, parts_json FROM events "
                "WHERE event = 'complete' AND parts_json IS NOT NULL AND id > ? ORDER BY id",
                (last,),
            ).fetchall()
        except sqlite3.Error:
            return
        if rows:
            known: dict[str, tuple[str, str]] = {}
            for row in rows:
                try:
                    parts = json.loads(row["parts_json"] or "[]")
                except ValueError:
                    continue
                if not isinstance(parts, list):
                    continue
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    key = part_key(part.get("name") or "")
                    if not key or key in known:
                        continue
                    code, name = split_code_name(part.get("name") or "")
                    known[key] = (code, name)
            if known:
                self._insert_catalog_missing_locked(known)
            try:
                top = max(int(row["id"]) for row in rows)
            except (TypeError, ValueError):
                top = last
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES ('catalog_events_id', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(max(top, last)),),
            )

    def _catalog_event_mark_locked(self) -> int:
        try:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = 'catalog_events_id'"
            ).fetchone()
        except sqlite3.Error:
            return 0
        if row is None:
            return 0
        try:
            return max(int(row[0]), 0)
        except (TypeError, ValueError):
            return 0

    def _insert_catalog_missing_locked(self, known: dict[str, tuple[str, str]]) -> None:
        existing = {
            str(row[0]) for row in self._conn.execute("SELECT part_key FROM parts_catalog")
        }
        missing = sorted(key for key in known if key not in existing)
        if missing:
            current = self._next_number_locked()
            moment = self._clock()
            for key in missing:
                code, name = known[key]
                self._conn.execute(
                    "INSERT OR IGNORE INTO parts_catalog (part_key, number, code, name, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (key, current, code, name, moment),
                )
                current += 1
        for key, (code, name) in known.items():
            if key not in existing:
                continue
            self._conn.execute(
                "UPDATE parts_catalog SET code = CASE WHEN COALESCE(code, '') = '' THEN ? ELSE code END, "
                "name = CASE WHEN COALESCE(name, '') = '' THEN ? ELSE name END "
                "WHERE part_key = ?",
                (code, name, key),
            )

    def _next_number_locked(self) -> int:
        try:
            row = self._conn.execute("SELECT MAX(number) FROM parts_catalog").fetchone()
        except sqlite3.Error:
            return 1
        try:
            top = int(row[0]) if row is not None and row[0] is not None else 0
        except (TypeError, ValueError):
            top = 0
        return max(top, 0) + 1

    def _lookup_part_names_locked(self, key: str) -> tuple[str, str]:
        row = self._conn.execute(
            "SELECT code, name FROM plan_items WHERE part_key = ? LIMIT 1", (key,)
        ).fetchone()
        if row is not None:
            return str(row["code"] or ""), str(row["name"] or "")
        try:
            event_rows = self._conn.execute(
                "SELECT parts_json FROM events WHERE event = 'complete' AND parts_json IS NOT NULL"
            ).fetchall()
        except sqlite3.Error:
            return "", ""
        for event_row in event_rows:
            try:
                parts = json.loads(event_row["parts_json"] or "[]")
            except ValueError:
                continue
            if not isinstance(parts, list):
                continue
            for part in parts:
                if not isinstance(part, dict):
                    continue
                if part_key(part.get("name") or "") == key:
                    return split_code_name(part.get("name") or "")
        return "", ""

    def _catalog_numbers_locked(self) -> dict[str, int | None]:
        try:
            self._ensure_catalog_schema_locked()
            self._seed_catalog_plans_locked()
            self._conn.commit()
        except sqlite3.Error:
            return {}
        try:
            rows = self._conn.execute("SELECT part_key, number FROM parts_catalog").fetchall()
        except sqlite3.Error:
            return {}
        numbers: dict[str, int | None] = {}
        for row in rows:
            try:
                numbers[str(row["part_key"])] = (
                    int(row["number"]) if row["number"] is not None else None
                )
            except (TypeError, ValueError):
                numbers[str(row["part_key"])] = None
        return numbers

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
                entry = entries[key] = {
                    "key": key,
                    "code": "",
                    "name": "",
                    "target": 0,
                    "actual": 0,
                    "days": {},
                }
            entry["actual"] += count
            entry["days"].setdefault(day, {"date": day, "target": 0, "actual": 0})["actual"] += count
        items = []
        numbers = self._catalog_numbers_locked()
        for key in sorted(entries):
            entry = entries[key]
            entry["percent"] = round(entry["actual"] / entry["target"] * 100.0, 1) if entry["target"] > 0 else 0.0
            entry["days"] = [entry["days"][day] for day in sorted(entry["days"])]
            entry["number"] = numbers.get(key)
            items.append(entry)
        return items

    def _actuals(self, start, end) -> dict[tuple[str, str], int]:
        return self._day_actuals(_day_of(start), _day_of(end - 1.0))

    def _day_actuals(self, first: str, last: str) -> dict[tuple[str, str], int]:
        try:
            start = _local_ts(datetime.fromisoformat(first))
            end = _local_ts(datetime.fromisoformat(last) + timedelta(days=1))
        except (OSError, OverflowError, ValueError):
            return {}
        totals: dict[tuple[str, str], int] = {}
        rows = self._conn.execute(
            "SELECT ts, parts_json FROM events "
            "WHERE event = 'complete' AND parts_json IS NOT NULL AND ts >= ? AND ts < ?",
            (start, end),
        )
        for row in rows:
            try:
                day = _day_of(float(row["ts"]))
                parts = json.loads(row["parts_json"] or "[]")
            except (OverflowError, ValueError):
                continue
            if day < first or day > last:
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
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS parts_catalog ("
            "part_key TEXT PRIMARY KEY, number INTEGER, code TEXT, name TEXT, "
            "file_path TEXT, file_size INTEGER, file_mtime REAL, updated_at REAL)"
        )
        catalog_columns = {str(row[1]) for row in self._conn.execute("PRAGMA table_info(parts_catalog)")}
        for column, kind in (
            ("number", "INTEGER"),
            ("code", "TEXT"),
            ("name", "TEXT"),
            ("file_path", "TEXT"),
            ("file_size", "INTEGER"),
            ("file_mtime", "REAL"),
            ("updated_at", "REAL"),
        ):
            if column not in catalog_columns:
                self._conn.execute(f"ALTER TABLE parts_catalog ADD COLUMN {column} {kind}")
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS parts_catalog_number "
            "ON parts_catalog(number) WHERE number IS NOT NULL"
        )

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
        cutoff = moment - RETENTION_SECONDS
        self._conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        self._conn.execute("DELETE FROM plan_items WHERE date < ?", (_day_of(cutoff),))

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
