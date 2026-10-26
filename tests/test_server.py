import json
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest import mock
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from farm_monitor import PrinterView, summarize
from farm_server import FarmApp, Journal, create_server, find_running_instance
from tests.fake_moonraker import FakeMoonraker, gcode_with_thumb


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
            with self.assertLogs("farm_server", level="ERROR"):
                app = FarmApp(config_path=path, start_monitor=False)
            self.assertEqual(app.config.printers, [])
            self.assertTrue(Path(str(path) + ".broken").exists())
            self.assertFalse(path.exists())


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"thumbnail" * 8


class StubMonitor:
    def __init__(self, view):
        self.view = view
        self.printers = []
        self.refresh_count = 0

    def snapshot(self):
        return {
            "printers": [asdict(self.view)],
            "summary": asdict(summarize([self.view])),
            "updated_at": 0.0,
        }

    def set_printers(self, printers):
        self.printers = list(printers)

    def refresh(self):
        self.refresh_count += 1

    def start(self):
        pass

    def stop(self):
        pass


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.moonraker = FakeMoonraker().start()
        self.addCleanup(self.moonraker.stop)
        self.key = f"127.0.0.1:{self.moonraker.port}"
        self.config_path = Path(self.tmp.name) / "printers.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "printers": [
                        {
                            "name": "Тест",
                            "host": "127.0.0.1",
                            "port": self.moonraker.port,
                            "mainsail_port": 4408,
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        view = PrinterView(
            key=self.key,
            name="Тест",
            host="127.0.0.1",
            port=self.moonraker.port,
            mainsail_url="http://127.0.0.1:4408",
            online=True,
            state="printing",
            file="деталь.gcode",
            progress=0.4,
            layer=51,
            layer_total=310,
            eta_seconds=120,
            nozzle=250.4,
            bed=74.2,
            filament="PETG",
            thumb_version="",
            error=None,
            last_seen=0.0,
        )
        self.monitor = StubMonitor(view)
        self.app = FarmApp(config_path=self.config_path, monitor=self.monitor, start_monitor=False)
        self.addCleanup(self.app.job.cancel)
        self.gcode = Path(self.tmp.name) / "part.gcode"
        self.gcode.write_bytes(gcode_with_thumb(PNG_BYTES))
        (Path(self.tmp.name) / "sub").mkdir()
        (Path(self.tmp.name) / "notes.txt").write_text("текст", encoding="utf-8")
        self.server = create_server(self.app, port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def saved_config(self):
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def test_fs_lists_drives_and_gcode(self):
        response = requests.get(self.url("/api/fs?path=" + quote(str(self.tmp.name))), timeout=3)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(any(entry["name"] == "part.gcode" for entry in payload["entries"]))
        self.assertTrue(all(entry["name"].endswith(".gcode") or entry["is_dir"] for entry in payload["entries"]))
        self.assertNotIn("notes.txt", [entry["name"] for entry in payload["entries"]])
        self.assertTrue(payload["entries"][0]["is_dir"])
        self.assertEqual(payload["entries"][0]["name"], "sub")
        self.assertEqual(payload["path"], str(self.tmp.name))

    def test_fs_lists_drives_when_path_empty(self):
        response = requests.get(self.url("/api/fs?path="), timeout=3)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["entries"])
        for entry in payload["entries"]:
            self.assertTrue(entry["is_dir"])
            self.assertTrue(entry["path"])

    def test_fs_permission_denied(self):
        with mock.patch("farm_server.os.scandir", side_effect=PermissionError):
            response = requests.get(self.url("/api/fs?path=" + quote(str(self.tmp.name))), timeout=3)
        self.assertEqual(response.status_code, 403)
        self.assertIn("доступ", response.json()["error"])

    def test_files_thumb(self):
        response = requests.get(self.url("/api/files/thumb?path=" + quote(str(self.gcode))), timeout=3)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "image/png")
        self.assertEqual(response.content, PNG_BYTES)

    def test_files_thumb_missing(self):
        other = Path(self.tmp.name) / "notes.txt"
        response = requests.get(self.url("/api/files/thumb?path=" + quote(str(other))), timeout=3)
        self.assertEqual(response.status_code, 404)

    def test_thumb_etag_and_304(self):
        self.monitor.view = replace(self.monitor.view, thumb_version="v1")
        self.app.thumb_cache.put((self.key, self.monitor.view.file), PNG_BYTES)
        url = self.url("/api/thumb/" + quote(self.key))
        response = requests.get(url, timeout=3)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "image/png")
        etag = response.headers["ETag"]
        self.assertEqual(etag, '"v1"')
        cached = requests.get(url, headers={"If-None-Match": etag}, timeout=3)
        self.assertEqual(cached.status_code, 304)
        self.assertEqual(cached.content, b"")
        missing = requests.get(self.url("/api/thumb/10.0.0.1:7125"), timeout=3)
        self.assertEqual(missing.status_code, 404)

    def test_thumb_without_cache_is_404(self):
        self.monitor.view = replace(self.monitor.view, thumb_version="")
        response = requests.get(self.url("/api/thumb/" + quote(self.key)), timeout=3)
        self.assertEqual(response.status_code, 404)

    def test_printers_crud(self):
        initial = requests.get(self.url("/api/printers"), timeout=3)
        self.assertEqual(initial.status_code, 200)
        self.assertEqual([item["key"] for item in initial.json()["printers"]], [self.key])
        response = requests.post(self.url("/api/printers"), json={"name": "New", "host": "10.0.0.5", "port": 7125}, timeout=3)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.saved_config()["printers"][-1]["host"], "10.0.0.5")
        self.assertIn("10.0.0.5:7125", [printer.key for printer in self.monitor.printers])
        response = requests.put(self.url("/api/printers/10.0.0.5:7125"), json={"name": "Renamed", "host": "10.0.0.5", "port": 7125}, timeout=3)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.saved_config()["printers"][-1]["name"], "Renamed")
        response = requests.delete(self.url("/api/printers/10.0.0.5:7125"), timeout=3)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([f"{item['host']}:{item['port']}" for item in self.saved_config()["printers"]], [self.key])
        self.assertEqual([printer.key for printer in self.monitor.printers], [self.key])

    def test_printers_reject_duplicate_key(self):
        response = requests.post(
            self.url("/api/printers"),
            json={"name": "Копия", "host": "127.0.0.1", "port": self.moonraker.port},
            timeout=3,
        )
        self.assertEqual(response.status_code, 409)

    def test_printers_validation(self):
        missing = requests.post(self.url("/api/printers"), json={"name": "Без адреса"}, timeout=3)
        self.assertEqual(missing.status_code, 400)
        bad_port = requests.post(
            self.url("/api/printers"),
            json={"name": "Порт", "host": "10.0.0.6", "port": 70000},
            timeout=3,
        )
        self.assertEqual(bad_port.status_code, 400)
        not_json = requests.post(
            self.url("/api/printers"),
            data=b"{}",
            headers={"Content-Type": "text/plain"},
            timeout=3,
        )
        self.assertEqual(not_json.status_code, 415)
        unknown = requests.put(
            self.url("/api/printers/10.9.9.9:7125"),
            json={"name": "Нет", "host": "10.9.9.9", "port": 7125},
            timeout=3,
        )
        self.assertEqual(unknown.status_code, 404)

    def test_pause_calls_moonraker(self):
        response = requests.post(self.url(f"/api/print/{self.key}/pause"), timeout=3)
        self.assertEqual(response.status_code, 200)
        self.assertIn("/printer/print/pause", self.moonraker.request_paths)
        self.assertEqual(self.monitor.refresh_count, 1)

    def test_print_unknown_printer_and_action(self):
        missing = requests.post(self.url("/api/print/10.0.0.1:7125/pause"), timeout=3)
        self.assertEqual(missing.status_code, 404)
        bad_action = requests.post(self.url(f"/api/print/{self.key}/explode"), timeout=3)
        self.assertEqual(bad_action.status_code, 400)

    def test_send_starts_job(self):
        response = requests.post(self.url("/api/send"), json={"printers": [self.key], "files": [str(self.gcode)]}, timeout=3)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.app.config.last_dir, str(self.gcode.parent))
        self.assertEqual(self.saved_config()["last_dir"], str(self.gcode.parent))

    def test_send_rejects_duplicate_basenames(self):
        first = Path(self.tmp.name) / "a" / "part.gcode"
        second = Path(self.tmp.name) / "b" / "PART.gcode"
        for path in (first, second):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"G1 X0\n")
        response = requests.post(
            self.url("/api/send"),
            json={"printers": [self.key], "files": [str(first), str(second)]},
            timeout=3,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Одинаковые имена", response.json()["error"])

    def test_send_rejects_unknown_printer_and_bad_body(self):
        unknown = requests.post(
            self.url("/api/send"),
            json={"printers": ["10.0.0.1:7125"], "files": [str(self.gcode)]},
            timeout=3,
        )
        self.assertEqual(unknown.status_code, 400)
        self.assertIn("Принтер не найден", unknown.json()["error"])
        not_json = requests.post(
            self.url("/api/send"),
            data=b"{}",
            headers={"Content-Type": "text/plain"},
            timeout=3,
        )
        self.assertEqual(not_json.status_code, 415)

    def test_job_cancel(self):
        response = requests.post(self.url("/api/job/cancel"), json={}, timeout=3)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_journal_since(self):
        self.app.journal.add("первое")
        self.app.journal.add("второе")
        last = self.app.journal.snapshot()[-1]["id"]
        empty = requests.get(self.url(f"/api/journal?since={last}"), timeout=3)
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["entries"], [])
        self.assertEqual(empty.json()["next"], last)
        self.app.journal.add("третье")
        response = requests.get(self.url(f"/api/journal?since={last}"), timeout=3)
        self.assertEqual([entry["message"] for entry in response.json()["entries"]], ["третье"])
        self.assertEqual(response.json()["next"], last + 1)


if __name__ == "__main__":
    unittest.main()
