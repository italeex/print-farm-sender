import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from farm_errors import ErrorInfo
from farm_history import PERIODS, HistoryStore
from farm_monitor import PrinterView

KEY = "192.168.7.158:7125"
BASE = 1_700_000_000.0


def make_view(state, file="", parts=None, error=None, progress=1.0):
    return PrinterView(
        key=KEY,
        name="F008 158",
        host="192.168.7.158",
        port=7125,
        mainsail_url="http://192.168.7.158:4408",
        mainsail_port=4408,
        ui_kind="",
        online=state != "offline",
        state=state,
        file=file,
        progress=progress,
        layer=0,
        layer_total=0,
        eta_seconds=None,
        finish_at=None,
        nozzle=0.0,
        bed=0.0,
        filament="",
        thumb_version="",
        error=error,
        last_seen=0.0,
        parts=list(parts or []),
    )


class Clock:
    def __init__(self, now=BASE):
        self.now = now

    def __call__(self):
        return self.now


class HistoryStoreTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(folder) / "farm_history.db"
        self.clock = Clock()
        self.history = HistoryStore(self.path, clock=self.clock)
        self.addCleanup(self.history.close)

    def record(self, view, previous=None, now=None):
        return self.history.record(view, previous, self.clock.now if now is None else now)

    def print_cycle(self):
        parts = [{"name": "Корпус", "count": 30}, {"name": "Крышка", "count": 5}]
        printing = make_view("printing", "деталь.gcode", parts)
        self.record(printing, None, BASE)
        paused = make_view("paused", "деталь.gcode", parts)
        self.record(paused, printing, BASE + 100)
        resumed = make_view("printing", "деталь.gcode", parts)
        self.record(resumed, paused, BASE + 200)
        idle = make_view("idle", "деталь.gcode")
        self.record(idle, resumed, BASE + 300)
        return printing, idle

    def test_print_cycle_events(self):
        self.print_cycle()
        events = self.history.events(KEY)
        self.assertEqual([event["event"] for event in events], ["state", "pause", "resume", "complete"])
        self.assertEqual([event["state"] for event in events], ["printing", "paused", "printing", "idle"])
        complete = events[-1]
        self.assertEqual(complete["file"], "деталь.gcode")
        self.assertEqual(complete["parts"], 35)
        self.assertEqual(complete["duration"], 300.0)

    def test_repeated_view_is_ignored(self):
        view = make_view("printing", "деталь.gcode")
        self.assertIsNotNone(self.record(view, None, BASE))
        self.assertIsNone(self.record(view, view, BASE + 10))
        self.assertEqual(len(self.history.events(KEY)), 1)

    def test_file_change_recorded_as_state(self):
        first = make_view("printing", "a.gcode")
        self.record(first, None, BASE)
        second = make_view("printing", "b.gcode")
        self.record(second, first, BASE + 10)
        events = self.history.events(KEY)
        self.assertEqual([event["event"] for event in events], ["state", "state"])
        self.assertEqual(events[-1]["file"], "b.gcode")

    def test_error_resets_print_start(self):
        printing = make_view("printing", "a.gcode", progress=0.5)
        self.record(printing, None, BASE)
        error = make_view("error", "a.gcode", progress=0.5)
        self.record(error, printing, BASE + 10)
        resumed = make_view("printing", "a.gcode", progress=1.0)
        self.record(resumed, error, BASE + 20)
        idle = make_view("idle", "a.gcode")
        self.record(idle, resumed, BASE + 30)
        complete = self.history.last_completion(KEY)
        self.assertEqual(complete["ts"], BASE + 30)
        self.assertEqual(complete["duration"], 10.0)

    def test_offline_resets_print_start(self):
        printing = make_view("printing", "a.gcode", progress=0.5)
        self.record(printing, None, BASE)
        offline = make_view("offline")
        self.record(offline, printing, BASE + 10)
        resumed = make_view("printing", "a.gcode", progress=1.0)
        self.record(resumed, offline, BASE + 20)
        idle = make_view("idle", "a.gcode")
        self.record(idle, resumed, BASE + 30)
        self.assertEqual(self.history.last_completion(KEY)["duration"], 10.0)

    def test_file_change_while_printing_resets_start(self):
        first = make_view("printing", "a.gcode", progress=0.5)
        self.record(first, None, BASE)
        second = make_view("printing", "b.gcode", progress=1.0)
        self.record(second, first, BASE + 10)
        idle = make_view("idle", "b.gcode")
        self.record(idle, second, BASE + 20)
        complete = self.history.last_completion(KEY)
        self.assertEqual(complete["file"], "b.gcode")
        self.assertEqual(complete["duration"], 10.0)

    def test_cancelled_is_not_completion(self):
        printing = make_view("printing", "a.gcode", [{"name": "Корпус", "count": 30}], progress=0.4)
        self.record(printing, None, BASE)
        idle = make_view("idle", "a.gcode")
        self.record(idle, printing, BASE + 300)
        events = self.history.events(KEY)
        self.assertEqual([event["event"] for event in events], ["state", "cancelled"])
        self.assertIsNone(events[-1]["parts"])
        self.assertEqual(events[-1]["duration"], 300.0)
        self.assertIsNone(self.history.last_completion(KEY))
        self.assertEqual(self.history.plan(now=BASE + 300)["day"]["parts_done"], 0)
        self.assertEqual(self.history.summary(0.0, BASE + 400)["printers"], {})

    def test_cancelled_after_pause(self):
        printing = make_view("printing", "a.gcode", [{"name": "Корпус", "count": 30}], progress=0.5)
        self.record(printing, None, BASE)
        paused = make_view("paused", "a.gcode", progress=0.5)
        self.record(paused, printing, BASE + 10)
        idle = make_view("idle", "a.gcode")
        self.record(idle, paused, BASE + 20)
        self.assertEqual([event["event"] for event in self.history.events(KEY)], ["state", "pause", "cancelled"])
        self.assertIsNone(self.history.last_completion(KEY))

    def test_error_and_offline_messages(self):
        printing = make_view("printing", "a.gcode")
        self.record(printing, None, BASE)
        error = ErrorInfo("2560", "Нет связи", "Проверьте кабель", "MCU shutdown")
        self.record(make_view("error", "a.gcode", error=error), printing, BASE + 10)
        offline = make_view("offline")
        self.record(offline, make_view("error", "a.gcode", error=error), BASE + 20)
        events = self.history.events(KEY)
        self.assertEqual([event["event"] for event in events], ["state", "error", "offline"])
        self.assertEqual(events[1]["message"], "Нет связи (2560)")

    def test_events_window_and_limit(self):
        for index in range(5):
            state = "printing" if index % 2 == 0 else "paused"
            previous = make_view("paused" if state == "printing" else "printing", "a.gcode") if index else None
            self.record(make_view(state, "a.gcode"), previous, BASE + index)
        self.assertEqual(len(self.history.events(KEY)), 5)
        self.assertEqual(len(self.history.events(KEY, since=BASE + 2)), 3)
        self.assertEqual(len(self.history.events(KEY, until=BASE + 2)), 3)
        self.assertEqual(len(self.history.events(KEY, limit=2)), 2)
        self.assertEqual(self.history.events(KEY, limit=2)[-1]["ts"], BASE + 4)

    def test_segments_clip_and_merge(self):
        self.clock.now = BASE + 500
        self.print_cycle()
        segments = self.history.segments(KEY, BASE + 50, BASE + 250)
        self.assertEqual(
            [(item["state"], item["start"], item["end"]) for item in segments],
            [
                ("printing", BASE + 50, BASE + 100),
                ("paused", BASE + 100, BASE + 200),
                ("printing", BASE + 200, BASE + 250),
            ],
        )
        self.assertEqual(segments[0]["file"], "деталь.gcode")

    def test_segments_without_events(self):
        self.assertEqual(self.history.segments(KEY, 0.0), [])

    def test_last_completion(self):
        self.assertIsNone(self.history.last_completion(KEY))
        self.print_cycle()
        completion = self.history.last_completion(KEY)
        self.assertEqual(completion["parts"], 35)
        self.assertEqual(completion["ts"], BASE + 300)

    def test_plan_counts_parts_and_hours(self):
        self.clock.now = BASE + 1800
        printing = make_view("printing", "деталь.gcode", [{"name": "Корпус", "count": 35}])
        self.record(printing, None, BASE)
        idle = make_view("idle", "деталь.gcode")
        self.record(idle, printing, BASE + 1800)
        self.history.set_goal("day", 70, 2.0)
        plan = self.history.plan(now=BASE + 1800)
        self.assertEqual(sorted(plan), sorted(PERIODS))
        day = plan["day"]
        self.assertEqual(day["parts_target"], 70)
        self.assertEqual(day["hours_target"], 2.0)
        self.assertEqual(day["parts_done"], 35)
        self.assertEqual(day["hours_done"], 0.5)
        self.assertEqual(day["parts_percent"], 50.0)
        self.assertEqual(day["hours_percent"], 25.0)
        self.assertLessEqual(day["start"], BASE)
        self.assertGreaterEqual(day["end"], BASE + 1800)

    def test_plan_defaults_without_goals(self):
        plan = self.history.plan(now=BASE + 1800)
        for period in PERIODS:
            self.assertEqual(plan[period]["parts_target"], 0)
            self.assertEqual(plan[period]["hours_target"], 0.0)
            self.assertEqual(plan[period]["parts_percent"], 0.0)
            self.assertEqual(plan[period]["hours_percent"], 0.0)

    def test_set_goal_upserts(self):
        self.assertEqual(self.history.set_goal("week", 10, 1.5)["parts_target"], 10)
        self.assertEqual(self.history.set_goal("week", 20, 3.0)["hours_target"], 3.0)
        plan = self.history.plan(now=BASE + 1800)
        self.assertEqual(plan["week"]["parts_target"], 20)
        self.assertEqual(plan["week"]["hours_target"], 3.0)
        self.assertEqual(plan["day"]["parts_target"], 0)
        with self.assertRaises(ValueError):
            self.history.set_goal("year", 1, 1)

    def test_summary_per_printer(self):
        self.print_cycle()
        summary = self.history.summary(0.0, BASE + 400)
        self.assertEqual(summary["printers"][KEY]["complete"], 1)
        self.assertEqual(summary["printers"][KEY]["parts"], 35)

    def test_forget_clears_print_start(self):
        self.record(make_view("printing", "a.gcode"), None, BASE)
        self.assertIn(KEY, self.history._printing_since)
        self.history.forget(KEY)
        self.assertNotIn(KEY, self.history._printing_since)

    def test_retention_pruned_once_per_day(self):
        self.record(make_view("printing", "a.gcode"), None, BASE)
        self.clock.now = BASE + 181 * 86400
        self.record(make_view("idle", "a.gcode"), make_view("printing", "a.gcode"), self.clock.now)
        self.assertEqual([event["state"] for event in self.history.events(KEY)], ["idle"])

    def test_old_events_pruned_on_open(self):
        self.record(make_view("printing", "a.gcode"), None, 1000.0)
        self.history.close()
        self.clock.now = 1000.0 + 181 * 86400
        reopened = HistoryStore(self.path, clock=self.clock)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.events(KEY), [])

    def test_complete_stores_parts_json(self):
        self.print_cycle()
        row = self.history._conn.execute(
            "SELECT parts_json FROM events WHERE event = 'complete'"
        ).fetchone()
        self.assertEqual(
            json.loads(row["parts_json"]),
            [{"name": "Корпус", "count": 30}, {"name": "Крышка", "count": 5}],
        )
        self.assertIsNone(
            self.history._conn.execute(
                "SELECT parts_json FROM events WHERE event = 'state' ORDER BY id LIMIT 1"
            ).fetchone()["parts_json"]
        )

    def test_migration_adds_parts_json(self):
        legacy = self.path.parent / "legacy.db"
        connection = sqlite3.connect(str(legacy))
        connection.executescript(
            "CREATE TABLE events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, printer_key TEXT NOT NULL, ts REAL NOT NULL, "
            "event TEXT NOT NULL, state TEXT, file TEXT, parts INTEGER, duration REAL, message TEXT);"
        )
        connection.commit()
        connection.close()
        store = HistoryStore(legacy, clock=self.clock)
        self.addCleanup(store.close)
        columns = {row[1] for row in store._conn.execute("PRAGMA table_info(events)")}
        self.assertIn("parts_json", columns)

    def today(self, moment=BASE):
        return datetime.fromtimestamp(moment).date().isoformat()

    def test_import_plan_replaces_dates(self):
        day = self.today()
        other = (datetime.fromtimestamp(BASE).date() + timedelta(days=1)).isoformat()
        self.assertEqual(
            self.history.import_plan(
                [
                    {"date": day, "key": "ГР3.01.42000.01", "code": "МВСЛ.ГР3.01.42000.01", "name": "Крышка", "target": 10},
                    {"date": other, "key": "ГР3.02.10000.01", "code": "МВСЛ.ГР3.02.10000.01", "name": "Корпус", "target": 4},
                ]
            ),
            {"imported": 2, "days": 2},
        )
        self.assertEqual(
            self.history.import_plan(
                [{"date": day, "key": "ГР3.01.42000.01", "code": "МВСЛ.ГР3.01.42000.01", "name": "Крышка", "target": 3}]
            ),
            {"imported": 1, "days": 1},
        )
        rows = self.history._conn.execute(
            "SELECT date, part_key, target FROM plan_items ORDER BY date, part_key"
        ).fetchall()
        self.assertEqual(
            [(row["date"], row["part_key"], row["target"]) for row in rows],
            [(day, "ГР3.01.42000.01", 3), (other, "ГР3.02.10000.01", 4)],
        )

    def test_import_plan_clears_full_range(self):
        monday = datetime.fromtimestamp(BASE).date() - timedelta(
            days=datetime.fromtimestamp(BASE).weekday()
        )
        days = [(monday + timedelta(days=offset)).isoformat() for offset in range(6)]
        self.assertEqual(
            self.history.import_plan(
                [
                    {"date": day, "key": "ГР3.01.42000.01", "code": "", "name": "Крышка", "target": 1}
                    for day in days
                ]
            )["days"],
            6,
        )
        self.history.import_plan(
            [{"date": days[0], "key": "ГР3.01.42000.01", "code": "", "name": "Крышка", "target": 7}],
            start=days[0],
            end=days[-1],
        )
        rows = self.history._conn.execute(
            "SELECT date, target FROM plan_items ORDER BY date"
        ).fetchall()
        self.assertEqual([(row["date"], row["target"]) for row in rows], [(days[0], 7)])

    def test_import_empty_keeps_rows(self):
        day = self.today()
        self.history.import_plan(
            [{"date": day, "key": "ГР3.01.42000.01", "code": "", "name": "Крышка", "target": 5}]
        )
        result = self.history.import_plan([], start=day, end=day)
        self.assertEqual(result, {"imported": 0, "days": 0})
        rows = self.history._conn.execute("SELECT date, target FROM plan_items").fetchall()
        self.assertEqual([(row["date"], row["target"]) for row in rows], [(day, 5)])

    def test_import_plan_keeps_rows_outside_range(self):
        monday = datetime.fromtimestamp(BASE).date() - timedelta(
            days=datetime.fromtimestamp(BASE).weekday()
        )
        inside = monday.isoformat()
        outside = (monday - timedelta(days=1)).isoformat()
        self.history.import_plan(
            [
                {"date": inside, "key": "ГР3.01.42000.01", "code": "", "name": "Крышка", "target": 1},
                {"date": outside, "key": "ГР3.02.10000.01", "code": "", "name": "Корпус", "target": 2},
            ]
        )
        self.history.import_plan(
            [{"date": inside, "key": "ГР3.01.42000.01", "code": "", "name": "Крышка", "target": 3}],
            start=inside,
            end=inside,
        )
        rows = self.history._conn.execute(
            "SELECT date, target FROM plan_items ORDER BY date"
        ).fetchall()
        self.assertEqual(
            [(row["date"], row["target"]) for row in rows],
            [(outside, 2), (inside, 3)],
        )

    def test_plan_items_actuals_from_complete(self):
        day = self.today()
        self.history.import_plan(
            [{"date": day, "key": "ГР3.01.42000.01", "code": "МВСЛ.ГР3.01.42000.01", "name": "Крышка", "target": 10}]
        )
        printing = make_view(
            "printing",
            "Крышка.gcode",
            [
                {"name": "МВСЛ.ГР3.01.42000.01_Крышка.stl", "count": 4},
                {"name": "МВСЛ.ГР3.02.10000.01 Корпус", "count": 2},
            ],
        )
        self.record(printing, None, BASE)
        self.record(make_view("idle", "Крышка.gcode"), printing, BASE + 60)
        plan = self.history.plan(now=BASE + 60)["day"]
        self.assertEqual(plan["parts_target"], 10)
        self.assertEqual(plan["parts_done"], 6)
        self.assertEqual(plan["parts_percent"], 60.0)
        self.assertEqual(len(plan["items"]), 2)
        item = plan["items"][0]
        self.assertEqual(item["key"], "ГР3.01.42000.01")
        self.assertEqual(item["code"], "МВСЛ.ГР3.01.42000.01")
        self.assertEqual(item["name"], "Крышка")
        self.assertEqual(item["target"], 10)
        self.assertEqual(item["actual"], 4)
        self.assertEqual(item["percent"], 40.0)
        self.assertEqual(item["days"], [{"date": day, "target": 10, "actual": 4}])
        extra = plan["items"][1]
        self.assertEqual(extra["key"], "ГР3.02.10000.01")
        self.assertEqual(extra["code"], "")
        self.assertEqual(extra["name"], "")
        self.assertEqual(extra["target"], 0)
        self.assertEqual(extra["actual"], 2)
        self.assertEqual(extra["percent"], 0.0)

    def test_plan_completed_totals_match_calendar(self):
        day = self.today()
        self.history.import_plan(
            [{"date": day, "key": "ГР3.01.42000.01", "code": "МВСЛ.ГР3.01.42000.01", "name": "Крышка", "target": 10}]
        )
        printing = make_view(
            "printing",
            "Крышка.gcode",
            [
                {"name": "МВСЛ.ГР3.01.42000.01_Крышка.stl", "count": 4},
                {"name": "МВСЛ.ГР9.99.99999.99 Другое.stl", "count": 3},
            ],
        )
        self.record(printing, None, BASE)
        self.record(make_view("idle", "Крышка.gcode"), printing, BASE + 60)
        plan = self.history.plan(now=BASE + 60)["day"]
        data = self.history.calendar(now=BASE + 60)
        calendar_day = next(item for item in data["days"] if item["date"] == day)
        self.assertEqual(plan["parts_done"], 7)
        self.assertEqual(calendar_day["actual"], 7)
        self.assertEqual(plan["parts_in_progress"], 0)
        self.assertEqual(calendar_day["parts_in_progress"], 0)

    def test_plan_items_match_bare_stl_name(self):
        day = self.today()
        self.history.import_plan(
            [{"date": day, "key": "ГР3.01.42000.01", "code": "МВСЛ.ГР3.01.42000.01", "name": "Крышка", "target": 10}]
        )
        printing = make_view("printing", "Крышка.gcode", [{"name": "МВСЛ.ГР3.01.42000.01.stl", "count": 6}])
        self.record(printing, None, BASE)
        self.record(make_view("idle", "Крышка.gcode"), printing, BASE + 60)
        plan = self.history.plan(now=BASE + 60)["day"]
        self.assertEqual(plan["items"][0]["actual"], 6)
        self.assertEqual(plan["parts_percent"], 60.0)

    def test_plan_items_aggregate_week_and_month(self):
        moment = datetime.fromtimestamp(BASE)
        monday = moment.date() - timedelta(days=moment.weekday())
        first = monday.isoformat()
        second = (monday + timedelta(days=1)).isoformat()
        self.history.import_plan(
            [
                {"date": first, "key": "ГР3.01.42000.01", "code": "МВСЛ.ГР3.01.42000.01", "name": "Крышка", "target": 2},
                {"date": second, "key": "ГР3.01.42000.01", "code": "МВСЛ.ГР3.01.42000.01", "name": "Крышка", "target": 3},
            ]
        )
        week = self.history.plan(now=BASE)["week"]
        self.assertEqual(len(week["items"]), 1)
        self.assertEqual(week["items"][0]["target"], 5)
        self.assertEqual(
            [(day["date"], day["target"]) for day in week["items"][0]["days"]],
            [(first, 2), (second, 3)],
        )
        self.assertEqual(week["parts_target"], 5)
        self.assertEqual(self.history.plan(now=BASE)["month"]["items"][0]["target"], 5)
        day = self.history.plan(now=BASE)["day"]
        expected = {first: 2, second: 3}.get(moment.date().isoformat(), 0)
        self.assertEqual(day["parts_target"], expected)

    def test_calendar_days_and_weeks(self):
        self.history.import_plan(
            [
                {"date": "2024-03-04", "key": "ГР3.01.42000.01", "code": "МВСЛ.ГР3.01.42000.01", "name": "Крышка", "target": 10},
                {"date": "2024-03-18", "key": "ГР3.01.42000.01", "code": "МВСЛ.ГР3.01.42000.01", "name": "Крышка", "target": 6},
                {"date": "2024-03-18", "key": "ГР3.02.10000.01", "code": "МВСЛ.ГР3.02.10000.01", "name": "Корпус", "target": 4},
            ]
        )
        printing = make_view(
            "printing", "Крышка.gcode", [{"name": "МВСЛ.ГР3.01.42000.01_Крышка.stl", "count": 4}]
        )
        self.record(printing, None, datetime(2024, 3, 4, 10, 0).timestamp())
        self.record(
            make_view("idle", "Крышка.gcode"),
            printing,
            datetime(2024, 3, 4, 10, 1).timestamp(),
        )
        extra = make_view(
            "printing", "Другое.gcode", [{"name": "МВСЛ.ГР9.99.99999.99 Другое.stl", "count": 3}]
        )
        self.record(extra, None, datetime(2024, 3, 5, 9, 0).timestamp())
        self.record(
            make_view("idle", "Другое.gcode"),
            extra,
            datetime(2024, 3, 5, 9, 1).timestamp(),
        )
        data = self.history.calendar("2024-03", now=datetime(2024, 3, 18, 12, 0).timestamp())
        self.assertEqual(data["month"], "2024-03")
        self.assertEqual(data["month_start"], "2024-03-01")
        self.assertEqual(data["month_end"], "2024-03-31")
        self.assertEqual(data["today"], "2024-03-18")
        self.assertEqual(len(data["days"]), 31)
        days = {day["date"]: day for day in data["days"]}
        self.assertEqual(days["2024-03-01"]["weekday"], 5)
        self.assertEqual(days["2024-03-04"]["target"], 10)
        self.assertEqual(days["2024-03-04"]["actual"], 4)
        self.assertEqual(
            days["2024-03-04"]["items"],
            [
                {
                    "key": "ГР3.01.42000.01",
                    "code": "МВСЛ.ГР3.01.42000.01",
                    "name": "Крышка",
                    "target": 10,
                    "actual": 4,
                    "in_progress": 0,
                    "percent": 40.0,
                }
            ],
        )
        self.assertEqual(days["2024-03-05"]["target"], 0)
        self.assertEqual(days["2024-03-05"]["actual"], 3)
        self.assertEqual(days["2024-03-05"]["items"][0]["key"], "ГР9.99.99999.99")
        self.assertEqual(days["2024-03-05"]["items"][0]["target"], 0)
        self.assertEqual(days["2024-03-05"]["items"][0]["percent"], 0.0)
        self.assertEqual(days["2024-03-06"]["target"], 0)
        self.assertEqual(days["2024-03-06"]["actual"], 0)
        self.assertEqual(days["2024-03-06"]["items"], [])
        self.assertEqual(days["2024-03-18"]["target"], 10)
        self.assertEqual(
            [item["key"] for item in days["2024-03-18"]["items"]],
            ["ГР3.01.42000.01", "ГР3.02.10000.01"],
        )
        weeks = data["weeks"]
        self.assertEqual(len(weeks), 5)
        self.assertEqual((weeks[0]["year"], weeks[0]["week"]), (2024, 9))
        self.assertEqual((weeks[0]["start"], weeks[0]["end"]), ("2024-02-26", "2024-03-03"))
        self.assertEqual(weeks[0]["target"], 0)
        self.assertEqual(weeks[0]["actual"], 0)
        self.assertEqual(len(weeks[0]["days"]), 7)
        self.assertEqual(weeks[0]["days"][0], {"date": "2024-02-26", "target": 0, "actual": 0})
        self.assertEqual((weeks[1]["year"], weeks[1]["week"]), (2024, 10))
        self.assertEqual((weeks[1]["start"], weeks[1]["end"]), ("2024-03-04", "2024-03-10"))
        self.assertEqual(weeks[1]["target"], 10)
        self.assertEqual(weeks[1]["actual"], 7)
        self.assertEqual(
            [item["key"] for item in weeks[1]["items"]],
            ["ГР3.01.42000.01", "ГР9.99.99999.99"],
        )
        self.assertEqual(weeks[1]["items"][1]["target"], 0)
        self.assertEqual((weeks[4]["year"], weeks[4]["week"]), (2024, 13))
        self.assertEqual((weeks[4]["start"], weeks[4]["end"]), ("2024-03-25", "2024-03-31"))
        self.assertEqual(len(weeks[4]["days"]), 7)

    def test_plan_live_parts_in_progress(self):
        day = self.today()
        self.history.import_plan(
            [{"date": day, "key": "ГР3.01.42000.01", "code": "МВСЛ.ГР3.01.42000.01", "name": "Крышка", "target": 20}]
        )
        printing = make_view("printing", "Крышка.gcode", [{"name": "МВСЛ.ГР3.01.42000.01_Крышка.stl", "count": 4}])
        self.record(printing, None, BASE)
        self.record(make_view("idle", "Крышка.gcode"), printing, BASE + 60)
        live = [{"key": "ГР3.01.42000.01", "code": "МВСЛ.ГР3.01.42000.01", "name": "Крышка", "count": 16}]
        plan = self.history.plan(now=BASE + 60, live_parts=live)["day"]
        self.assertEqual(plan["parts_done"], 4)
        self.assertEqual(plan["parts_in_progress"], 16)
        item = plan["items"][0]
        self.assertEqual(item["target"], 20)
        self.assertEqual(item["actual"], 4)
        self.assertEqual(item["in_progress"], 16)
        self.assertEqual(item["percent"], 20.0)

    def test_plan_live_parts_without_plan_row(self):
        live = [{"key": "ГР9.99.99999.99", "code": "МВСЛ.ГР9.99.99999.99", "name": "Другое", "count": 3}]
        plan = self.history.plan(now=BASE, live_parts=live)
        for period in PERIODS:
            self.assertEqual(plan[period]["parts_done"], 0)
            self.assertEqual(plan[period]["parts_in_progress"], 3)
            item = plan[period]["items"][0]
            self.assertEqual(item["key"], "ГР9.99.99999.99")
            self.assertEqual(item["code"], "МВСЛ.ГР9.99.99999.99")
            self.assertEqual(item["name"], "Другое")
            self.assertEqual(item["target"], 0)
            self.assertEqual(item["actual"], 0)
            self.assertEqual(item["in_progress"], 3)
            self.assertEqual(item["percent"], 0.0)

    def test_plan_live_parts_merge_duplicates(self):
        live = [
            {"key": "ГР3.01.42000.01", "code": "МВСЛ.ГР3.01.42000.01", "name": "Крышка", "count": 10},
            {"key": "ГР3.01.42000.01", "code": "", "name": "", "count": 6},
        ]
        plan = self.history.plan(now=BASE, live_parts=live)["day"]
        self.assertEqual(plan["parts_in_progress"], 16)
        self.assertEqual(plan["items"][0]["in_progress"], 16)

    def test_plan_live_parts_drop_after_idle(self):
        day = self.today()
        self.history.import_plan(
            [{"date": day, "key": "КОРПУС", "code": "МВСЛ.КОРПУС", "name": "Корпус", "target": 20}]
        )
        printing = make_view("printing", "Корпус.gcode", [{"name": "Корпус", "count": 35}])
        self.record(printing, None, BASE)
        self.record(make_view("idle", "Корпус.gcode"), printing, BASE + 60)
        live = [{"key": "КОРПУС", "code": "МВСЛ.КОРПУС", "name": "Корпус", "count": 35}]
        busy = self.history.plan(now=BASE + 60, live_parts=live)["day"]
        self.assertEqual(busy["parts_done"], 35)
        self.assertEqual(busy["parts_in_progress"], 35)
        self.assertEqual(busy["items"][0]["actual"], 35)
        self.assertEqual(busy["items"][0]["in_progress"], 35)
        idle = self.history.plan(now=BASE + 60, live_parts=[])["day"]
        self.assertEqual(idle["parts_done"], 35)
        self.assertEqual(idle["parts_in_progress"], 0)
        self.assertEqual(idle["items"][0]["actual"], 35)
        self.assertEqual(idle["items"][0]["in_progress"], 0)

    def test_calendar_live_parts_only_today(self):
        moment = datetime(2024, 3, 18, 12, 0).timestamp()
        self.history.import_plan(
            [
                {"date": "2024-03-18", "key": "ГР3.01.42000.01", "code": "МВСЛ.ГР3.01.42000.01", "name": "Крышка", "target": 10},
                {"date": "2024-03-19", "key": "ГР3.01.42000.01", "code": "МВСЛ.ГР3.01.42000.01", "name": "Крышка", "target": 5},
            ]
        )
        live = [{"key": "ГР3.01.42000.01", "code": "МВСЛ.ГР3.01.42000.01", "name": "Крышка", "count": 16}]
        data = self.history.calendar("2024-03", now=moment, live_parts=live)
        days = {day["date"]: day for day in data["days"]}
        self.assertEqual(days["2024-03-17"]["parts_in_progress"], 0)
        self.assertEqual(days["2024-03-18"]["target"], 10)
        self.assertEqual(days["2024-03-18"]["actual"], 0)
        self.assertEqual(days["2024-03-18"]["parts_in_progress"], 16)
        self.assertEqual(days["2024-03-18"]["items"][0]["in_progress"], 16)
        self.assertEqual(days["2024-03-19"]["parts_in_progress"], 0)
        self.assertEqual(days["2024-03-19"]["items"][0]["in_progress"], 0)
        self.assertEqual(days["2024-03-19"]["items"][0]["target"], 5)
        weeks = {week["start"]: week for week in data["weeks"]}
        self.assertEqual(weeks["2024-03-11"]["parts_in_progress"], 0)
        self.assertEqual(weeks["2024-03-18"]["parts_in_progress"], 16)
        self.assertEqual(weeks["2024-03-18"]["items"][0]["in_progress"], 16)
        self.assertEqual(weeks["2024-03-25"]["parts_in_progress"], 0)

    def test_calendar_live_parts_outside_month(self):
        moment = datetime(2024, 3, 18, 12, 0).timestamp()
        live = [{"key": "ГР3.01.42000.01", "code": "МВСЛ.ГР3.01.42000.01", "name": "Крышка", "count": 16}]
        data = self.history.calendar("2024-02", now=moment, live_parts=live)
        self.assertTrue(all(day["parts_in_progress"] == 0 for day in data["days"]))
        self.assertTrue(all(week["parts_in_progress"] == 0 for week in data["weeks"]))
        self.assertTrue(all(item["in_progress"] == 0 for week in data["weeks"] for item in week["items"]))

    def test_calendar_live_parts_without_plan_row(self):
        moment = datetime(2024, 3, 18, 12, 0).timestamp()
        live = [{"key": "ГР9.99.99999.99", "code": "МВСЛ.ГР9.99.99999.99", "name": "Другое", "count": 3}]
        data = self.history.calendar("2024-03", now=moment, live_parts=live)
        days = {day["date"]: day for day in data["days"]}
        item = days["2024-03-18"]["items"][0]
        self.assertEqual(item["key"], "ГР9.99.99999.99")
        self.assertEqual(item["target"], 0)
        self.assertEqual(item["actual"], 0)
        self.assertEqual(item["in_progress"], 3)
        self.assertEqual(days["2024-03-18"]["parts_in_progress"], 3)

    def test_calendar_default_month_and_validation(self):
        moment = datetime(2024, 3, 18, 12, 0).timestamp()
        data = self.history.calendar(now=moment)
        self.assertEqual(data["month"], "2024-03")
        self.assertEqual(data["today"], "2024-03-18")
        self.assertEqual(self.history.calendar("2024-02", now=moment)["month_end"], "2024-02-29")
        self.assertEqual(self.history.calendar()["month"], "2023-11")
        for value in ("2024-13", "2024-00", "2024-3", "24-03", "март", "2024/03"):
            with self.assertRaises(ValueError, msg=value):
                self.history.calendar(value, now=moment)

    def test_calendar_actuals_query_is_bounded(self):
        printing = make_view("printing", "деталь.gcode", [{"name": "Корпус", "count": 2}])
        self.record(printing, None, datetime(2024, 3, 4, 10, 0).timestamp())
        self.record(make_view("idle", "деталь.gcode"), printing, datetime(2024, 3, 4, 10, 1).timestamp())
        statements = []
        self.history._conn.set_trace_callback(statements.append)
        try:
            data = self.history.calendar("2024-03", now=datetime(2024, 3, 18, 12, 0).timestamp())
        finally:
            self.history._conn.set_trace_callback(None)
        queries = [sql for sql in statements if "FROM events" in sql]
        self.assertEqual(len(queries), 1)
        self.assertIn("ts >=", queries[0])
        self.assertIn("ts <", queries[0])
        day = next(item for item in data["days"] if item["date"] == "2024-03-04")
        self.assertEqual(day["actual"], 2)

    def test_qc_groups_shift_completions(self):
        def complete(name, count, moment):
            printing = make_view("printing", "a.gcode", [{"name": name, "count": count}])
            self.record(printing, None, moment)
            self.record(make_view("idle", "a.gcode"), printing, moment + 60)

        complete("МВСЛ.ГР3.01.42000.01 Крышка", 3, datetime(2026, 10, 28, 10, 0).timestamp())
        complete("МФСЛ.ГР3.01.42000.01_Крышка.stl", 2, datetime(2026, 10, 29, 8, 30).timestamp())
        complete("МВСЛ.ГР3.01.42000.01 Крышка", 9, datetime(2026, 10, 29, 9, 30).timestamp())
        result = self.history.qc("2026-10-29")
        self.assertEqual(result["total"], 5)
        self.assertEqual(result["positions"], 1)
        self.assertEqual(result["items"][0]["count"], 5)

    def test_events_event_ts_index(self):
        names = {str(row[1]) for row in self.history._conn.execute("PRAGMA index_list('events')")}
        self.assertIn("events_event_ts", names)

    def test_calendar_four_week_month(self):
        data = self.history.calendar("2027-02", now=datetime(2027, 2, 15, 12, 0).timestamp())
        self.assertEqual(data["month_start"], "2027-02-01")
        self.assertEqual(data["month_end"], "2027-02-28")
        self.assertEqual(len(data["weeks"]), 4)
        self.assertEqual(data["weeks"][0]["start"], "2027-02-01")
        self.assertEqual(data["weeks"][-1]["end"], "2027-02-28")

    def test_calendar_month_overflow(self):
        with self.assertRaises(ValueError) as caught:
            self.history.calendar("9999-12")
        self.assertIn("9999-12", str(caught.exception))
        self.assertNotIn("формате", str(caught.exception))

    def test_plan_keeps_goal_for_periods_without_items(self):
        self.history.set_goal("day", 70, 2.0)
        self.history.import_plan(
            [
                {
                    "date": self.today(),
                    "key": "ГР3.01.42000.01",
                    "code": "МВСЛ.ГР3.01.42000.01",
                    "name": "Крышка",
                    "target": 9,
                }
            ]
        )
        current = self.history.plan(now=BASE)["day"]
        self.assertEqual(current["parts_target"], 9)
        self.assertEqual(current["hours_target"], 2.0)
        later = self.history.plan(now=BASE + 40 * 86400)["day"]
        self.assertEqual(later["items"], [])
        self.assertEqual(later["parts_target"], 70)

    def test_set_goal_keeps_missing_field(self):
        self.history.set_goal("week", 100, 5.0)
        self.history.set_goal("week", None, 7.5)
        plan = self.history.plan(now=BASE)["week"]
        self.assertEqual(plan["parts_target"], 100)
        self.assertEqual(plan["hours_target"], 7.5)

    def test_plan_periods_expose_iso_dates(self):
        plan = self.history.plan(now=BASE)
        for period in PERIODS:
            data = plan[period]
            self.assertRegex(data["start_date"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertRegex(data["end_date"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertRegex(data["week_start"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertLessEqual(data["start_date"], data["end_date"])
        week = plan["week"]
        self.assertEqual(week["start_date"], week["week_start"])
        self.assertEqual(week["start_date"], "2023-11-13")
        self.assertEqual(week["end_date"], "2023-11-19")
        self.assertEqual(week["start_date"], self.history.plan(now=BASE)["day"]["week_start"])

    def test_prune_drops_old_plan_items(self):
        today = datetime.fromtimestamp(BASE).date()
        old = (today - timedelta(days=200)).isoformat()
        fresh = (today - timedelta(days=1)).isoformat()
        self.history.import_plan(
            [
                {"date": old, "key": "ГР3.01.42000.01", "code": "", "name": "Крышка", "target": 1},
                {"date": fresh, "key": "ГР3.01.42000.01", "code": "", "name": "Крышка", "target": 2},
            ]
        )
        self.history._prune(BASE)
        self.history._conn.commit()
        rows = self.history._conn.execute("SELECT date FROM plan_items ORDER BY date").fetchall()
        self.assertEqual([row["date"] for row in rows], [fresh])


if __name__ == "__main__":
    unittest.main()
