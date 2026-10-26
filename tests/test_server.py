import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from farm_server import FarmApp, Journal, create_server, find_running_instance


class StubApp:
    def __init__(self):
        self.last_heartbeat = 0.0
        self.journal = Journal()
        self.idle_timeout = 600
        self.stopped = False

    def state_snapshot(self):
        return {"printers": [], "summary": {"printing": 1}, "job": {}, "config": {}}

    def shutdown(self):
        self.stopped = True


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.app = StubApp()
        self.server = create_server(self.app, port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def test_state_json(self):
        response = requests.get(self.url("/api/state"), timeout=3)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"]["printing"], 1)

    def test_journal_json(self):
        self.app.journal.add("проверка журнала")
        response = requests.get(self.url("/api/journal"), timeout=3)
        self.assertEqual(response.status_code, 200)
        entries = response.json()["entries"]
        self.assertEqual(entries[-1]["message"], "проверка журнала")

    def test_static_index(self):
        response = requests.get(self.url("/"), timeout=3)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["Content-Type"])
        self.assertIn("Printer Farm", response.text)

    def test_static_assets(self):
        for path, kind in (("/styles.css", "text/css"), ("/app.js", "javascript")):
            response = requests.get(self.url(path), timeout=3)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn(kind, response.headers["Content-Type"])
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertIn("no-cache", response.headers["Cache-Control"])

    def test_origin_rejected_on_post(self):
        response = requests.post(self.url("/api/heartbeat"), headers={"Origin": "http://evil.example"}, timeout=3)
        self.assertEqual(response.status_code, 403)

    def test_origin_allowed_same_port(self):
        response = requests.post(
            self.url("/api/heartbeat"),
            headers={"Origin": f"http://127.0.0.1:{self.port}"},
            timeout=3,
        )
        self.assertEqual(response.status_code, 200)

    def test_path_traversal_blocked(self):
        response = requests.get(self.url("/../farm_core.py"), timeout=3)
        self.assertIn(response.status_code, (400, 404))

    def test_unknown_path_404(self):
        response = requests.get(self.url("/api/missing"), timeout=3)
        self.assertEqual(response.status_code, 404)

    def test_heartbeat_updates(self):
        before = self.app.last_heartbeat
        time.sleep(0.02)
        requests.post(self.url("/api/heartbeat"), timeout=3)
        self.assertGreater(self.app.last_heartbeat, before)

    def test_shutdown_endpoint(self):
        response = requests.post(self.url("/api/shutdown"), timeout=3)
        self.assertEqual(response.status_code, 200)
        deadline = time.time() + 3
        while not self.app.stopped and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.app.stopped)

    def test_find_running_instance(self):
        self.assertEqual(
            find_running_instance("127.0.0.1", [self.port], timeout=1.0),
            f"http://127.0.0.1:{self.port}",
        )
        self.assertIsNone(find_running_instance("127.0.0.1", [1], timeout=0.2))

    def test_idle_shutdown_rule(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "printers.json"
            path.write_text('{"printers": []}', encoding="utf-8")
            app = FarmApp(config_path=path, start_monitor=False)
            app.last_heartbeat = 100
            self.assertFalse(app.should_shutdown(200))
            self.assertTrue(app.should_shutdown(100 + app.idle_timeout + 1))

    def test_broken_config_quarantined(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "printers.json"
            path.write_text("{", encoding="utf-8")
            app = FarmApp(config_path=path, start_monitor=False)
            self.assertEqual(app.config.printers, [])
            self.assertTrue(Path(str(path) + ".broken").exists())
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
