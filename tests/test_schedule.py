import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from farm_schedule import SHIFT_END, SHIFT_START, build_schedule, estimate_seconds

DATE = "2026-10-29"


def at(hhmm, day=DATE):
    return datetime.combine(
        datetime.fromisoformat(day).date(),
        datetime.strptime(hhmm, "%H:%M").time(),
    ).timestamp()


NOW = at("08:00")


class EstimateTests(unittest.TestCase):
    def test_median_odd(self):
        self.assertEqual(estimate_seconds([3000.0, 1000.0, 2000.0]), (2000.0, False))

    def test_median_even(self):
        self.assertEqual(estimate_seconds([1000.0, 3000.0]), (2000.0, False))

    def test_default(self):
        self.assertEqual(estimate_seconds([]), (14400.0, True))


class BuildScheduleTests(unittest.TestCase):
    def test_assigns_to_earliest_free_printer(self):
        out = build_schedule(NOW, "2026-10-29", [{"part_key": "a", "number": 1, "name": "A", "remaining": 2}],
                             [{"key": "p1", "name": "P1", "state": "idle", "finish_at": None}],
                             {"p1": {"a"}}, {"a": 3600})
        self.assertEqual([t["start"] for t in out["tasks"]], [at("09:00"), at("10:00")])
        self.assertEqual(out["totals"]["scheduled"], 2)

    def test_prefers_earliest_free_and_tiebreak_by_name(self):
        printers = [
            {"key": "pb", "name": "B", "state": "printing", "finish_at": at("12:00")},
            {"key": "pa", "name": "A", "state": "idle", "finish_at": None},
        ]
        out = build_schedule(NOW, DATE, [{"part_key": "a", "number": 1, "name": "A", "remaining": 1}],
                             printers, {"pa": {"a"}, "pb": {"a"}}, {"a": 3600})
        self.assertEqual(out["tasks"][0]["printer_key"], "pa")
        idle = [
            {"key": "p2", "name": "B", "state": "idle", "finish_at": None},
            {"key": "p1", "name": "A", "state": "idle", "finish_at": None},
        ]
        out = build_schedule(NOW, DATE, [{"part_key": "a", "number": 1, "name": "A", "remaining": 1}],
                             idle, {"p1": {"a"}, "p2": {"a"}}, {"a": 3600})
        self.assertEqual(out["tasks"][0]["printer_key"], "p1")

    def test_night_print_finish_after_close_allowed(self):
        out = build_schedule(at("17:00"), DATE, [{"part_key": "a", "number": 1, "name": "A", "remaining": 1}],
                             [{"key": "p1", "name": "P1", "state": "idle", "finish_at": None}],
                             {"p1": {"a"}}, {"a": 7200})
        self.assertEqual(len(out["tasks"]), 1)
        self.assertEqual(out["tasks"][0]["start"], at("17:00"))
        self.assertEqual(out["tasks"][0]["finish"], at("19:00"))
        self.assertEqual(out["unscheduled"], [])

    def test_night_print_blocks_next_copy(self):
        out = build_schedule(at("17:00"), DATE, [{"part_key": "a", "number": 1, "name": "A", "remaining": 2}],
                             [{"key": "p1", "name": "P1", "state": "idle", "finish_at": None}],
                             {"p1": {"a"}}, {"a": 7200})
        self.assertEqual(len(out["tasks"]), 1)
        self.assertEqual(out["unscheduled"][0]["remaining"], 1)
        self.assertEqual(out["unscheduled"][0]["reason"], "нет свободных принтеров до 18:00")

    def test_busy_printer_start_at_finish(self):
        out = build_schedule(NOW, DATE, [{"part_key": "a", "number": 1, "name": "A", "remaining": 1}],
                             [{"key": "p1", "name": "P1", "state": "printing", "finish_at": at("14:00")}],
                             {"p1": {"a"}}, {"a": 3600})
        self.assertEqual(out["tasks"][0]["start"], at("14:00"))
        self.assertEqual(out["tasks"][0]["finish"], at("15:00"))

    def test_printing_without_finish_unavailable(self):
        out = build_schedule(NOW, DATE, [{"part_key": "a", "number": 1, "name": "A", "remaining": 1}],
                             [{"key": "p1", "name": "P1", "state": "printing", "finish_at": None}],
                             {"p1": {"a"}}, {"a": 3600})
        self.assertEqual(out["tasks"], [])
        self.assertEqual(out["unscheduled"][0]["reason"], "нет свободных принтеров до 18:00")

    def test_paused_error_offline_unavailable(self):
        for state in ("paused", "error", "offline"):
            with self.subTest(state=state):
                out = build_schedule(NOW, DATE, [{"part_key": "a", "number": 1, "name": "A", "remaining": 1}],
                                     [{"key": "p1", "name": "P1", "state": state, "finish_at": at("10:00")}],
                                     {"p1": {"a"}}, {"a": 3600})
                self.assertEqual(out["tasks"], [])
                self.assertEqual(out["unscheduled"][0]["reason"], "нет свободных принтеров до 18:00")

    def test_file_not_ready_still_scheduled(self):
        out = build_schedule(NOW, DATE, [{"part_key": "a", "number": 1, "name": "A", "remaining": 1}],
                             [{"key": "p1", "name": "P1", "state": "idle", "finish_at": None}],
                             {"p1": set()}, {"a": 3600})
        self.assertEqual(len(out["tasks"]), 1)
        self.assertFalse(out["tasks"][0]["file_ready"])

    def test_file_not_ready_without_estimate(self):
        out = build_schedule(NOW, DATE, [{"part_key": "a", "number": 1, "name": "A", "remaining": 3}],
                             [{"key": "p1", "name": "P1", "state": "idle", "finish_at": None}],
                             {"p1": {"a"}}, {})
        self.assertEqual(out["tasks"], [])
        self.assertEqual(out["unscheduled"][0]["reason"], "файл не готов")
        self.assertEqual(out["unscheduled"][0]["remaining"], 3)

    def test_nothing_fits(self):
        out = build_schedule(at("17:30"), DATE, [{"part_key": "a", "number": 1, "name": "A", "remaining": 1}],
                             [{"key": "p1", "name": "P1", "state": "printing", "finish_at": at("19:00")}],
                             {"p1": {"a"}}, {"a": 3600})
        self.assertEqual(out["tasks"], [])
        self.assertEqual(out["unscheduled"][0]["reason"], "нет свободных принтеров до 18:00")

    def test_empty_plan(self):
        out = build_schedule(NOW, DATE, [],
                             [{"key": "p1", "name": "P1", "state": "idle", "finish_at": None}],
                             {}, {})
        self.assertEqual(out["tasks"], [])
        self.assertEqual(out["unscheduled"], [])
        self.assertEqual(out["totals"], {"positions": 0, "copies": 0, "scheduled": 0, "unscheduled": 0})

    def test_closed_shift(self):
        out = build_schedule(at("18:30"), DATE, [{"part_key": "a", "number": 1, "name": "A", "remaining": 2}],
                             [{"key": "p1", "name": "P1", "state": "idle", "finish_at": None}],
                             {"p1": {"a"}}, {"a": 3600})
        self.assertEqual(out["tasks"], [])
        self.assertEqual(out["unscheduled"][0]["reason"], "смена закрыта")
        self.assertEqual(out["unscheduled"][0]["remaining"], 2)

    def test_window_and_totals(self):
        out = build_schedule(NOW, DATE,
                             [{"part_key": "a", "number": 1, "name": "A", "remaining": 2},
                              {"part_key": "b", "number": 2, "name": "B", "remaining": 1}],
                             [{"key": "p1", "name": "P1", "state": "idle", "finish_at": None}],
                             {"p1": {"a", "b"}}, {"a": 3600, "b": 3600})
        self.assertEqual(out["date"], DATE)
        self.assertEqual(out["window"], {"start": at("09:00"), "end": at("18:00")})
        self.assertEqual(out["totals"]["positions"], 2)
        self.assertEqual(out["totals"]["copies"], 3)
        self.assertEqual(out["totals"]["scheduled"], 3)
        self.assertEqual(out["totals"]["unscheduled"], 0)


if __name__ == "__main__":
    unittest.main()
