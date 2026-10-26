import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from farm_core import Printer
from farm_errors import ErrorInfo
from farm_monitor import PrinterView, build_view, summarize


def make_view(state, **kwargs):
    fields = {
        "key": "192.168.7.158:7125",
        "name": "F008 158",
        "host": "192.168.7.158",
        "port": 7125,
        "mainsail_url": "http://192.168.7.158:4408",
        "online": True,
        "state": state,
        "file": "",
        "progress": 0.0,
        "layer": 0,
        "layer_total": 0,
        "eta_seconds": None,
        "nozzle": 0.0,
        "bed": 0.0,
        "filament": "",
        "thumb_version": "",
        "error": None,
        "last_seen": 0,
    }
    fields.update(kwargs)
    return PrinterView(**fields)


class BuildViewTests(unittest.TestCase):
    def setUp(self):
        self.printer = Printer("F008 158", "192.168.7.158", 7125, 4408)
        self.info = {"state": "ready", "state_message": "Printer is ready"}
        self.status = {
            "print_stats": {"state": "printing", "filename": "деталь 7.gcode", "print_duration": 100},
            "virtual_sdcard": {
                "progress": 0.4,
                "layer": 51,
                "layer_count": 310,
                "cur_print_data": {"end_time": 1000, "metadata": {"filament_type": "PETG"}},
            },
            "extruder": {"temperature": 250.4},
            "heater_bed": {"temperature": 74.2},
        }

    def test_printing_view(self):
        view = build_view(self.printer, info=self.info, status=self.status, now=700, error=None, thumb_version="abc")
        self.assertEqual(view.state, "printing")
        self.assertEqual(view.file, "деталь 7.gcode")
        self.assertEqual(view.progress, 0.4)
        self.assertEqual((view.layer, view.layer_total), (51, 310))
        self.assertEqual(view.eta_seconds, 300)
        self.assertEqual((view.nozzle, view.bed), (250.4, 74.2))
        self.assertEqual(view.filament, "PETG")
        self.assertEqual(view.mainsail_url, "http://192.168.7.158:4408")

    def test_paused_and_complete_mapping(self):
        paused = build_view(self.printer, info=self.info, status={"print_stats": {"state": "paused", "filename": "a.gcode"}}, now=1, error=None, thumb_version="")
        complete = build_view(self.printer, info=self.info, status={"print_stats": {"state": "complete", "filename": "a.gcode"}}, now=1, error=None, thumb_version="")
        self.assertEqual(paused.state, "paused")
        self.assertEqual(complete.state, "idle")
        self.assertEqual(complete.file, "a.gcode")

    def test_error_state_from_info_beats_status(self):
        info = {"state": "shutdown", "state_message": "MCU shutdown"}
        error = ErrorInfo("2560", "Нет связи", "Проверьте кабель", "MCU shutdown")
        view = build_view(self.printer, info=info, status=None, now=1, error=error, thumb_version="")
        self.assertEqual(view.state, "error")
        self.assertTrue(view.online)
        self.assertEqual(view.error.label, "Нет связи")

    def test_offline_view(self):
        view = build_view(self.printer, info=None, status=None, now=1, error=None, thumb_version="")
        self.assertEqual(view.state, "offline")
        self.assertFalse(view.online)

    def test_eta_ignored_when_not_printing(self):
        status = {"print_stats": {"state": "paused"}, "virtual_sdcard": {"cur_print_data": {"end_time": 9999}}}
        view = build_view(self.printer, info=self.info, status=status, now=1, error=None, thumb_version="")
        self.assertIsNone(view.eta_seconds)


class SummaryTests(unittest.TestCase):
    def test_counts_and_nearest_finish(self):
        views = [
            make_view("printing", eta_seconds=100),
            make_view("printing", eta_seconds=50),
            make_view("paused"),
            make_view("idle"),
            make_view("error"),
            make_view("offline"),
        ]
        summary = summarize(views)
        self.assertEqual(
            (summary.printing, summary.paused, summary.idle, summary.error, summary.offline),
            (2, 1, 1, 1, 1),
        )
        self.assertEqual(summary.nearest_finish, 50)


if __name__ == "__main__":
    unittest.main()
