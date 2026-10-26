import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from farm_core import Printer
from farm_jobs import JobError, JobManager


class FakeMoonraker:
    def __init__(self, status=201):
        self.status = status
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
        self.server = FakeMoonraker().start()
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

    def test_invalid_files_rejected(self):
        manager = JobManager()
        with self.assertRaises(JobError):
            manager.start([self.printer], [Path(self.tmp.name) / "missing.gcode"])


if __name__ == "__main__":
    unittest.main()
