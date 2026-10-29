import statistics
from datetime import date as date_cls
from datetime import datetime

SHIFT_START = "09:00"
SHIFT_END = "18:00"

DEFAULT_ESTIMATE = 14400.0


def estimate_seconds(history_durations):
    if not history_durations:
        return (DEFAULT_ESTIMATE, True)
    return (float(statistics.median(history_durations)), False)


def _shift_ts(day, hhmm):
    parsed = datetime.strptime(hhmm, "%H:%M").time()
    return datetime.combine(day, parsed).timestamp()


def build_schedule(now, date, items, printers, ready, estimates):
    day = date_cls.fromisoformat(date) if isinstance(date, str) else date
    start_ts = _shift_ts(day, SHIFT_START)
    end_ts = _shift_ts(day, SHIFT_END)
    closed = now >= end_ts
    free = {}
    for printer in printers:
        state = printer.get("state")
        if state == "idle":
            free[printer["key"]] = max(now, start_ts)
        elif state == "printing" and printer.get("finish_at") is not None:
            free[printer["key"]] = max(now, float(printer["finish_at"]))
    by_key = {printer["key"]: printer for printer in printers}
    tasks = []
    pending = []
    for item in items:
        estimate = estimates.get(item["part_key"])
        if estimate is None:
            pending.append((item, item["remaining"], "файл не готов"))
            continue
        left = item["remaining"]
        for _ in range(left):
            best = None
            for key, free_at in free.items():
                if free_at >= end_ts:
                    continue
                if best is None or (free_at, by_key[key].get("name", "")) < (free[best], by_key[best].get("name", "")):
                    best = key
            if best is None:
                break
            start = free[best]
            finish = start + float(estimate)
            tasks.append({
                "printer_key": best,
                "printer": by_key[best].get("name", ""),
                "part_key": item["part_key"],
                "number": item["number"],
                "name": item["name"],
                "start": start,
                "finish": finish,
                "file_ready": item["part_key"] in (ready.get(best) or set()),
            })
            free[best] = finish
        done = sum(1 for task in tasks if task["part_key"] == item["part_key"] and task["number"] == item["number"])
        if done < left:
            if estimates.get(item["part_key"]) is None:
                reason = "файл не готов"
            elif closed:
                reason = "смена закрыта"
            else:
                reason = "нет свободных принтеров до 18:00"
            pending.append((item, left - done, reason))
    unscheduled = [
        {"part_key": item["part_key"], "number": item["number"], "name": item["name"],
         "remaining": count, "reason": reason}
        for item, count, reason in pending
    ]
    copies = sum(item["remaining"] for item in items)
    scheduled = len(tasks)
    return {
        "date": date if isinstance(date, str) else day.isoformat(),
        "window": {"start": start_ts, "end": end_ts},
        "tasks": tasks,
        "unscheduled": unscheduled,
        "totals": {"positions": len(items), "copies": copies,
                   "scheduled": scheduled, "unscheduled": copies - scheduled},
    }
