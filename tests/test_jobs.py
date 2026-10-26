import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from farm_core import (
    FileProgressEvent,
    FileResultEvent,
    FinishedEvent,
    OverallEvent,
    Printer,
    PrinterStatusEvent,
)
from farm_jobs import JobError, JobManager, _initial_state


class FakeMoonraker:
    def __init__(self, status=201, delay=0.0):
        self.status = status
        self.delay = delay
        self.requests = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                if self.path != "/server/files/upload":
                    self.send_response(404)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                with outer._lock:
                    outer.requests.append({"path": self.path, "body": body})
                time.sleep(outer.delay)
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"result": {"ok": true}}')

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


class JobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "part.gcode"
        self.path.write_bytes(b"G1 X0\n")
        self.server = FakeMoonraker(delay=0.5).start()
        self.addCleanup(self.server.stop)
        self.printer = Printer("test", "127.0.0.1", self.server.port)

    def test_job_reaches_finished(self):
        manager = JobManager()
        manager.start([self.printer], [self.path])
        deadline = time.time() + 10
        while manager.running and time.time() < deadline:
            time.sleep(0.05)
        snapshot = manager.snapshot()
        self.assertFalse(snapshot["running"])
        self.assertEqual(snapshot["ok"], 1)
        self.assertEqual(snapshot["failed"], 0)

    def test_second_start_rejected(self):
        manager = JobManager()
        manager.start([self.printer], [self.path])
        self.addCleanup(manager.cancel)
        with self.assertRaises(JobError):
            manager.start([self.printer], [self.path])

    def test_cancel_marks_snapshot(self):
        manager = JobManager()
        manager.start([self.printer], [self.path])
        manager.cancel()
        deadline = time.time() + 10
        while manager.running and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(manager.snapshot()["cancelled"])

    def test_cancel_after_finish_is_noop(self):
        manager = JobManager()
        manager.start([self.printer], [self.path])
        deadline = time.time() + 10
        while manager.running and time.time() < deadline:
            time.sleep(0.05)
        manager.cancel()
        snapshot = manager.snapshot()
        self.assertEqual(snapshot["ok"], 1)
        self.assertFalse(snapshot["cancelled"])
        self.assertFalse(snapshot["cancelling"])

    def test_invalid_files_rejected(self):
        manager = JobManager()
        with self.assertRaises(JobError):
            manager.start([self.printer], [Path(self.tmp.name) / "missing.gcode"])


class EventMappingTests(unittest.TestCase):
    def setUp(self):
        self.printer = Printer("test", "127.0.0.1", 1)
        self.key = self.printer.key
        self.events = []

    def make_manager(self, on_event=None):
        manager = JobManager(on_event=on_event or self.events.append)
        manager._state = _initial_state([self.printer], [Path("part.gcode")])
        return manager

    def test_status_events_set_terminal_progress(self):
        manager = self.make_manager()
        manager._on_event(PrinterStatusEvent(self.key, "отправка 1/1"))
        self.assertEqual(manager.snapshot()["per_printer"][self.key]["status"], "отправка 1/1")
        manager._on_event(PrinterStatusEvent(self.key, "готово"))
        row = manager.snapshot()["per_printer"][self.key]
        self.assertEqual(row["progress"], 100.0)
        self.assertEqual(row["speed"], 0.0)
        manager._on_event(PrinterStatusEvent(self.key, "отменено"))
        self.assertEqual(manager.snapshot()["per_printer"][self.key]["progress"], 0.0)
        manager._on_event(PrinterStatusEvent(self.key, "ошибка: timeout"))
        row = manager.snapshot()["per_printer"][self.key]
        self.assertEqual(row["progress"], 0.0)
        self.assertEqual(row["speed"], 0.0)

    def test_progress_percent_math(self):
        manager = self.make_manager()
        manager._on_event(FileProgressEvent(self.key, "part.gcode", 1, 1, 50, 200, 1234.5))
        row = manager.snapshot()["per_printer"][self.key]
        self.assertEqual(row["file"], "part.gcode")
        self.assertEqual(row["progress"], 25.0)
        self.assertEqual(row["speed"], 1234.5)
        manager._on_event(FileProgressEvent(self.key, "part.gcode", 1, 1, 0, 0, 0.0))
        self.assertEqual(manager.snapshot()["per_printer"][self.key]["progress"], 100.0)

    def test_file_result_stores_last_error(self):
        manager = self.make_manager()
        manager._on_event(FileResultEvent(self.key, "part.gcode", False, "timeout"))
        self.assertEqual(manager.snapshot()["per_printer"][self.key]["last_error"], "timeout")
        manager._on_event(FileResultEvent(self.key, "part.gcode", True, "OK"))
        self.assertEqual(manager.snapshot()["per_printer"][self.key]["last_error"], "timeout")

    def test_overall_and_finished_events(self):
        manager = self.make_manager()
        manager._state["running"] = True
        manager._on_event(OverallEvent(1, 2, 3, 4, 55.5))
        snapshot = manager.snapshot()
        self.assertEqual(snapshot["printers_total"], 2)
        self.assertEqual(snapshot["files_processed"], 3)
        self.assertEqual(snapshot["aggregate_speed"], 55.5)
        manager._on_event(FinishedEvent(ok=3, failed=1, cancelled=False))
        snapshot = manager.snapshot()
        self.assertFalse(snapshot["running"])
        self.assertTrue(snapshot["finished"])
        self.assertEqual(snapshot["ok"], 3)
        self.assertEqual(snapshot["failed"], 1)
        self.assertFalse(snapshot["cancelled"])
        self.assertEqual(snapshot["aggregate_speed"], 0.0)

    def test_in_flight_cancel_marks_cancelled(self):
        manager = self.make_manager()
        manager._state["running"] = True
        manager._state["cancelling"] = True
        manager._on_event(FinishedEvent(ok=0, failed=0, cancelled=False))
        self.assertTrue(manager.snapshot()["cancelled"])

    def test_stale_generation_events_ignored(self):
        manager = self.make_manager()
        manager._generation = 10
        manager._on_event(OverallEvent(9, 9, 9, 9, 9.0), generation=9)
        snapshot = manager.snapshot()
        self.assertEqual(snapshot["printers_done"], 0)
        self.assertEqual(snapshot["files_processed"], 0)
        manager._on_event(OverallEvent(1, 1, 1, 1, 1.0), generation=10)
        self.assertEqual(manager.snapshot()["files_processed"], 1)

    def test_events_forwarded(self):
        manager = self.make_manager()
        event = PrinterStatusEvent(self.key, "готово")
        manager._on_event(event)
        self.assertEqual(self.events, [event])

    def test_on_event_exception_contained(self):
        def boom(event):
            raise RuntimeError("boom")

        manager = self.make_manager(on_event=boom)
        with self.assertLogs("farm_jobs", level="ERROR"):
            manager._on_event(PrinterStatusEvent(self.key, "готово"))
        self.assertEqual(manager.snapshot()["per_printer"][self.key]["status"], "готово")


if __name__ == "__main__":
    unittest.main()
