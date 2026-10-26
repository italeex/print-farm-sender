import logging
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from farm_core import AppConfig, Printer
from farm_errors import ErrorInfo
from farm_monitor import FarmMonitor, PrinterView, build_view, summarize
from farm_thumbs import ThumbCache
from tests.fake_moonraker import FakeMoonraker


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


class FakeClock:
    def __init__(self, now=100.0):
        self.now = now
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            self.calls += 1
            return self.now


class PollerTests(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.WARNING)
        self.addCleanup(logging.disable, logging.NOTSET)

    def make_monitor(self, server, interval=5, clock=time.time):
        printer = Printer("test", "127.0.0.1", server.port)
        config = AppConfig(printers=[printer], refresh_seconds=interval)
        monitor = FarmMonitor(config, thumb_cache=ThumbCache(), interval=interval, clock=clock)
        monitor.set_printers(config.printers)
        self.addCleanup(monitor.stop)
        return monitor

    def drain_thumbs(self, monitor, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with monitor._lock:
                pending = [future for future in monitor._thumb_futures if not future.done()]
            if not pending:
                return
            time.sleep(0.02)
        self.fail("Превью не загрузилось вовремя")

    def test_poll_once_builds_snapshot(self):
        server = FakeMoonraker(thumb=b"\x89PNG\r\n\x1a\nx").start()
        self.addCleanup(server.stop)
        monitor = self.make_monitor(server)
        monitor.poll_once()
        self.drain_thumbs(monitor)
        snapshot = monitor.snapshot()
        view = snapshot["printers"][0]
        self.assertEqual(view["state"], "printing")
        self.assertTrue(view["thumb_version"])
        self.assertEqual(snapshot["summary"]["printing"], 1)
        self.assertIn("updated_at", snapshot)

    def test_offline_backoff(self):
        server = FakeMoonraker(failures=1).start()
        self.addCleanup(server.stop)
        monitor = self.make_monitor(server)
        monitor.poll_once()
        self.assertEqual(monitor.snapshot()["printers"][0]["state"], "offline")
        monitor.poll_once()
        self.assertEqual(server.request_count("/printer/info"), 1)
        monitor._next_due["127.0.0.1:%d" % server.port] = 0
        monitor.poll_once()
        self.assertEqual(monitor.snapshot()["printers"][0]["state"], "printing")

    def test_klipper_error_not_offline(self):
        server = FakeMoonraker(info={"state": "shutdown", "state_message": "MCU shutdown"}).start()
        self.addCleanup(server.stop)
        monitor = self.make_monitor(server)
        monitor.poll_once()
        view = monitor.snapshot()["printers"][0]
        self.assertEqual(view["state"], "error")
        self.assertTrue(view["online"])

    def test_stop_is_clean(self):
        server = FakeMoonraker().start()
        self.addCleanup(server.stop)
        monitor = self.make_monitor(server)
        monitor.start()
        monitor.stop()
        self.assertFalse(monitor.running)

    def test_garbage_log_line_does_not_kill_poll(self):
        server = FakeMoonraker(info={"state": "error", "state_message": ""}, log_tail="!! []\n").start()
        self.addCleanup(server.stop)
        monitor = self.make_monitor(server)
        monitor.poll_once()
        view = monitor.snapshot()["printers"][0]
        self.assertEqual(view["state"], "error")
        self.assertTrue(view["online"])
        self.assertIsNotNone(view["error"])

    def test_error_persists_until_recovery(self):
        server = FakeMoonraker(info={"state": "shutdown", "state_message": "MCU shutdown"}).start()
        self.addCleanup(server.stop)
        monitor = self.make_monitor(server)
        monitor.poll_once()
        first = monitor.snapshot()["printers"][0]
        self.assertEqual(first["state"], "error")
        self.assertIsNotNone(first["error"])
        server.info = {"state": "ready", "state_message": "Printer is ready"}
        monitor.refresh()
        second = monitor.snapshot()["printers"][0]
        self.assertEqual(second["state"], "printing")
        self.assertIsNone(second["error"])

    def test_offline_keeps_last_seen(self):
        server = FakeMoonraker().start()
        self.addCleanup(server.stop)
        clock = FakeClock(100.0)
        monitor = self.make_monitor(server, clock=clock)
        printer = monitor._printers[0]
        monitor.poll_once()
        self.assertEqual(monitor.snapshot()["printers"][0]["last_seen"], 100.0)
        server.stop()
        clock.now = 200.0
        monitor._next_due[printer.key] = 0
        monitor.poll_once()
        view = monitor.snapshot()["printers"][0]
        self.assertEqual(view["state"], "offline")
        self.assertEqual(view["last_seen"], 100.0)

    def test_complete_print_still_loads_thumb(self):
        server = FakeMoonraker(
            objects={"print_stats": {"state": "complete", "filename": "part.gcode"}},
            thumb=b"\x89PNG\r\n\x1a\nx",
        ).start()
        self.addCleanup(server.stop)
        monitor = self.make_monitor(server)
        monitor.poll_once()
        self.drain_thumbs(monitor)
        view = monitor.snapshot()["printers"][0]
        self.assertEqual(view["state"], "idle")
        self.assertEqual(view["file"], "part.gcode")
        self.assertTrue(view["thumb_version"])

    def test_objects_failure_is_error_not_offline(self):
        server = FakeMoonraker(objects=None).start()
        self.addCleanup(server.stop)
        clock = FakeClock(50.0)
        monitor = self.make_monitor(server, clock=clock)
        monitor.poll_once()
        view = monitor.snapshot()["printers"][0]
        self.assertEqual(view["state"], "error")
        self.assertTrue(view["online"])
        self.assertEqual(view["last_seen"], 50.0)
        self.assertEqual(monitor._next_due[monitor._printers[0].key], 55.0)
        self.assertEqual(monitor._failures[monitor._printers[0].key], 0)

    def test_backoff_cap_at_60_seconds(self):
        server = FakeMoonraker(failures=100).start()
        self.addCleanup(server.stop)
        clock = FakeClock(0.0)
        monitor = self.make_monitor(server, clock=clock)
        key = monitor._printers[0].key
        for delay in (10.0, 20.0, 40.0, 60.0, 60.0, 60.0):
            monitor.poll_once()
            self.assertEqual(monitor._next_due[key], clock.now + delay)
            self.assertEqual(monitor.snapshot()["printers"][0]["state"], "offline")
            clock.now = monitor._next_due[key]

    def test_snapshot_before_first_poll(self):
        server = FakeMoonraker().start()
        self.addCleanup(server.stop)
        monitor = self.make_monitor(server)
        snapshot = monitor.snapshot()
        self.assertEqual(len(snapshot["printers"]), 1)
        view = snapshot["printers"][0]
        self.assertEqual(view["state"], "offline")
        self.assertFalse(view["online"])
        self.assertEqual(view["last_seen"], 0)
        self.assertEqual(snapshot["summary"]["offline"], 1)

    def test_set_printers_purges_state_and_resets_schedule(self):
        server = FakeMoonraker(failures=1).start()
        self.addCleanup(server.stop)
        monitor = self.make_monitor(server)
        printer = monitor._printers[0]
        monitor.poll_once()
        self.assertEqual(monitor._failures[printer.key], 1)
        self.assertGreater(monitor._next_due[printer.key], 0)
        monitor.set_printers([])
        self.assertEqual(monitor.snapshot()["printers"], [])
        self.assertNotIn(printer.key, monitor._views)
        self.assertNotIn(printer.key, monitor._failures)
        self.assertNotIn(printer.key, monitor._next_due)
        monitor.set_printers([printer])
        self.assertEqual(monitor._next_due[printer.key], 0)
        monitor.poll_once()
        self.assertEqual(monitor.snapshot()["printers"][0]["state"], "printing")

    def test_refresh_while_running_keeps_single_loop(self):
        server = FakeMoonraker().start()
        self.addCleanup(server.stop)
        monitor = self.make_monitor(server)
        monitor.start()
        deadline = time.monotonic() + 5
        while server.request_count("/printer/info") == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        before = server.request_count("/printer/info")
        monitor.refresh()
        self.assertTrue(monitor.running)
        deadline = time.monotonic() + 5
        while server.request_count("/printer/info") == before and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertGreater(server.request_count("/printer/info"), before)
        self.assertEqual(sum(1 for item in threading.enumerate() if item.name == "farm-monitor"), 1)

    def test_stop_timeout_does_not_start_second_loop(self):
        server = FakeMoonraker().start()
        self.addCleanup(server.stop)
        monitor = self.make_monitor(server)
        gate = threading.Event()
        entered = threading.Event()
        original = monitor._fetch

        def slow_fetch(printer):
            entered.set()
            gate.wait(10)
            return original(printer)

        monitor._fetch = slow_fetch
        monitor.start()
        self.assertTrue(entered.wait(5))
        monitor.stop()
        self.assertTrue(monitor.running)
        monitor.start()
        self.assertEqual(sum(1 for item in threading.enumerate() if item.name == "farm-monitor"), 1)
        gate.set()
        monitor.stop()
        self.assertFalse(monitor.running)

    def test_thumb_transport_error_retries_on_next_poll(self):
        server = FakeMoonraker(thumb_status=500).start()
        self.addCleanup(server.stop)
        monitor = self.make_monitor(server)
        monitor.poll_once()
        self.drain_thumbs(monitor)
        self.assertEqual(monitor.snapshot()["printers"][0]["thumb_version"], "")
        server.thumb_status = 206
        server.thumb = b"\x89PNG\r\n\x1a\nx"
        monitor.refresh()
        self.drain_thumbs(monitor)
        self.assertTrue(monitor.snapshot()["printers"][0]["thumb_version"])

    def test_missing_thumbnail_fetched_once_per_interval(self):
        server = FakeMoonraker().start()
        self.addCleanup(server.stop)
        monitor = self.make_monitor(server)
        key = monitor._printers[0].key
        monitor.poll_once()
        self.drain_thumbs(monitor)
        self.assertEqual(server.request_count("/server/files/gcodes/"), 1)
        monitor._next_due[key] = 0
        monitor.poll_once()
        self.drain_thumbs(monitor)
        self.assertEqual(server.request_count("/server/files/gcodes/"), 1)

    def test_objects_failure_keeps_previous_print_data(self):
        server = FakeMoonraker(thumb=b"\x89PNG\r\n\x1a\nx").start()
        self.addCleanup(server.stop)
        clock = FakeClock(50.0)
        monitor = self.make_monitor(server, clock=clock)
        key = monitor._printers[0].key
        monitor.poll_once()
        self.drain_thumbs(monitor)
        first = monitor.snapshot()["printers"][0]
        self.assertEqual(first["state"], "printing")
        self.assertTrue(first["thumb_version"])
        server.objects = None
        monitor._next_due[key] = 0
        monitor.poll_once()
        view = monitor.snapshot()["printers"][0]
        self.assertEqual(view["state"], "error")
        self.assertTrue(view["online"])
        self.assertEqual(view["file"], first["file"])
        self.assertEqual(view["progress"], first["progress"])
        self.assertEqual(view["layer"], first["layer"])
        self.assertEqual(view["thumb_version"], first["thumb_version"])
        self.assertEqual(view["error"]["label"], "Klipper не отвечает")
        self.assertEqual(view["error"]["code"], "")
        self.assertIn("500", view["error"]["detail"])

    def test_objects_failure_before_first_success_has_empty_file(self):
        server = FakeMoonraker(objects=None).start()
        self.addCleanup(server.stop)
        monitor = self.make_monitor(server)
        monitor.poll_once()
        view = monitor.snapshot()["printers"][0]
        self.assertEqual(view["state"], "error")
        self.assertTrue(view["online"])
        self.assertEqual(view["file"], "")
        self.assertEqual(view["error"]["label"], "Klipper не отвечает")

    def test_objects_failure_with_shutdown_reads_log(self):
        server = FakeMoonraker(
            info={"state": "shutdown", "state_message": ""},
            objects=None,
            log_tail='!! {"code": "2560", "msg": "MCU lost"}\n',
        ).start()
        self.addCleanup(server.stop)
        monitor = self.make_monitor(server)
        monitor.poll_once()
        view = monitor.snapshot()["printers"][0]
        self.assertEqual(view["state"], "error")
        self.assertEqual(view["error"]["code"], "2560")
        self.assertEqual(view["error"]["detail"], "MCU lost")


if __name__ == "__main__":
    unittest.main()
