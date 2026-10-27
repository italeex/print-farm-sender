import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from farm_errors import ErrorInfo
from farm_history import PERIODS, HistoryStore
from farm_monitor import PrinterView

KEY = "192.168.7.158:7125"
BASE = 1_700_000_000.0


def make_view(state, file="", parts=None, error=None):
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
        progress=0.0,
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

    def test_old_events_pruned_on_open(self):
        self.record(make_view("printing", "a.gcode"), None, 1000.0)
        self.history.close()
        self.clock.now = 1000.0 + 181 * 86400
        reopened = HistoryStore(self.path, clock=self.clock)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.events(KEY), [])


if __name__ == "__main__":
    unittest.main()
