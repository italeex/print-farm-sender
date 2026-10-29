import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from calendar import monthrange
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from farm_monitor import PrinterView, summarize
from farm_history import HistoryStore
from farm_server import APP_VERSION, FarmApp, Journal, create_server, find_running_instance
from tests.fake_moonraker import FakeMoonraker, gcode_with_thumb
from tests.test_plan import write_plan_xlsx


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

    def test_state_includes_version(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "printers.json"
            path.write_text('{"printers": []}', encoding="utf-8")
            app = FarmApp(
                config_path=path,
                start_monitor=False,
                history=HistoryStore(Path(folder) / "farm_history.db"),
            )
            self.assertIs(app.monitor._history, app.history)
            snapshot = app.state_snapshot()
            app.shutdown()
        self.assertEqual(snapshot["version"], APP_VERSION)
        self.assertIsInstance(snapshot["started_at"], float)

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

    def test_host_header_rejected(self):
        response = requests.get(self.url("/api/state"), headers={"Host": "evil.example"}, timeout=3)
        self.assertEqual(response.status_code, 403)

    def test_host_localhost_allowed(self):
        response = requests.get(self.url("/api/state"), headers={"Host": f"localhost:{self.port}"}, timeout=3)
        self.assertEqual(response.status_code, 200)

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
            app = FarmApp(
                config_path=path,
                start_monitor=False,
                history=HistoryStore(Path(folder) / "farm_history.db"),
            )
            app.last_heartbeat = 100
            self.assertFalse(app.should_shutdown(200))
            self.assertTrue(app.should_shutdown(100 + app.idle_timeout + 1))
            app.shutdown()

    def test_broken_config_quarantined(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "printers.json"
            path.write_text("{", encoding="utf-8")
            with self.assertLogs("farm_server", level="ERROR"):
                app = FarmApp(
                    config_path=path,
                    start_monitor=False,
                    history=HistoryStore(Path(folder) / "farm_history.db"),
                )
            self.assertEqual(app.config.printers, [])
            self.assertTrue(Path(str(path) + ".broken").exists())
            self.assertFalse(path.exists())
            app.shutdown()

    def test_broken_config_journaled_when_rename_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "printers.json"
            path.write_text("{", encoding="utf-8")
            with mock.patch("pathlib.Path.replace", side_effect=OSError("locked")):
                with self.assertLogs("farm_server", level="ERROR"):
                    app = FarmApp(
                        config_path=path,
                        start_monitor=False,
                        history=HistoryStore(Path(folder) / "farm_history.db"),
                    )
            messages = [entry["message"] for entry in app.journal.snapshot()]
            self.assertTrue(any("Конфигурация не загружена" in message for message in messages))
            app.shutdown()


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
        self.history = HistoryStore(Path(self.tmp.name) / "farm_history.db")
        self.addCleanup(self.history.close)
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
            mainsail_port=4408,
            ui_kind="mainsail",
            online=True,
            state="printing",
            file="деталь.gcode",
            progress=0.4,
            layer=51,
            layer_total=310,
            eta_seconds=120,
            finish_at=1000,
            nozzle=250.4,
            bed=74.2,
            filament="PETG",
            thumb_version="",
            error=None,
            last_seen=0.0,
        )
        self.monitor = StubMonitor(view)
        self.browse_result = []
        self.browse_calls = []

        def browse_stub(initial_dir):
            self.browse_calls.append(initial_dir)
            return self.browse_result

        self.app = FarmApp(
            config_path=self.config_path,
            monitor=self.monitor,
            start_monitor=False,
            browse_files=browse_stub,
            history=self.history,
        )
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

    def printer_url(self, key):
        return self.url("/api/printers/" + quote(key, safe=""))

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

    def test_fs_rejects_nul_path(self):
        response = requests.get(self.url("/api/fs?path=%00"), timeout=3)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Некорректный путь", response.json()["error"])

    def test_fs_browse_returns_paths(self):
        self.browse_result = [str(self.gcode)]
        response = requests.post(
            self.url("/api/fs/browse"),
            json={"initial_dir": self.tmp.name},
            timeout=3,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["paths"], [str(self.gcode.resolve())])
        self.assertEqual(self.browse_calls, [self.tmp.name])

    def test_fs_browse_filters_non_gcode(self):
        missing = Path(self.tmp.name) / "missing.gcode"
        self.browse_result = [
            str(self.gcode),
            str(Path(self.tmp.name) / "notes.txt"),
            str(missing),
        ]
        response = requests.post(
            self.url("/api/fs/browse"),
            json={"initial_dir": self.tmp.name},
            timeout=3,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["paths"], [str(self.gcode.resolve())])
        self.assertEqual(response.json()["rejected"], 2)
        self.assertFalse(response.json()["cancelled"])

    def test_fs_browse_reports_cancelled(self):
        self.browse_result = []
        response = requests.post(
            self.url("/api/fs/browse"),
            json={"initial_dir": self.tmp.name},
            timeout=3,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["paths"], [])
        self.assertTrue(response.json()["cancelled"])

    def test_fs_browse_rejects_bad_body(self):
        bad_json = requests.post(
            self.url("/api/fs/browse"),
            data=b"{",
            headers={"Content-Type": "application/json"},
            timeout=3,
        )
        self.assertEqual(bad_json.status_code, 400)
        bad_type = requests.post(
            self.url("/api/fs/browse"),
            json={"initial_dir": 5},
            timeout=3,
        )
        self.assertEqual(bad_type.status_code, 400)

    def test_fs_browse_reports_dialog_error(self):
        def broken(initial_dir):
            raise RuntimeError("tk недоступен")

        self.app.browse_files = broken
        with self.assertLogs("farm_server", level="WARNING"):
            response = requests.post(
                self.url("/api/fs/browse"),
                json={"initial_dir": self.tmp.name},
                timeout=3,
            )
        self.assertEqual(response.status_code, 501)
        self.assertIn("диалог", response.json()["error"])
        self.assertNotIn("tk недоступен", response.json()["error"])

    def test_fs_browse_single_flight(self):
        started = threading.Event()
        release = threading.Event()
        responses = {}

        def blocking(initial_dir):
            started.set()
            release.wait(10)
            return []

        self.addCleanup(release.set)
        self.app.browse_files = blocking

        def first_call():
            responses["first"] = requests.post(
                self.url("/api/fs/browse"),
                json={"initial_dir": self.tmp.name},
                timeout=10,
            )

        thread = threading.Thread(target=first_call, daemon=True)
        thread.start()
        self.assertTrue(started.wait(5))
        second = requests.post(
            self.url("/api/fs/browse"),
            json={"initial_dir": self.tmp.name},
            timeout=3,
        )
        self.assertEqual(second.status_code, 409)
        self.assertIn("Диалог уже открыт", second.json()["error"])
        release.set()
        thread.join(10)
        self.assertEqual(responses["first"].status_code, 200)

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

    def test_printer_update_name_only_keeps_fields(self):
        created = requests.post(
            self.url("/api/printers"),
            json={"name": "M", "host": "10.0.0.8", "port": 7125, "mainsail_port": 5000},
            timeout=3,
        )
        self.assertEqual(created.status_code, 201)
        response = requests.put(self.printer_url("10.0.0.8:7125"), json={"name": "M2"}, timeout=3)
        self.assertEqual(response.status_code, 200)
        entry = next(item for item in self.saved_config()["printers"] if item["host"] == "10.0.0.8")
        self.assertEqual(entry["name"], "M2")
        self.assertEqual(entry["port"], 7125)
        self.assertEqual(entry["mainsail_port"], 5000)
        stored = next(printer for printer in self.monitor.printers if printer.key == "10.0.0.8:7125")
        self.assertEqual(stored.mainsail_port, 5000)
        self.assertEqual(stored.name, "M2")

    def test_printer_name_length_limited(self):
        too_long = "я" * 61
        create = requests.post(
            self.url("/api/printers"),
            json={"name": too_long, "host": "10.0.0.9", "port": 7125},
            timeout=3,
        )
        self.assertEqual(create.status_code, 400)
        self.assertIn("длиннее", create.json()["error"])
        update = requests.put(self.printer_url(self.key), json={"name": too_long}, timeout=3)
        self.assertEqual(update.status_code, 400)
        self.assertIn("длиннее", update.json()["error"])

    def test_scan_default_subnet(self):
        calls = []

        def scan_stub(*, subnet, moonraker_port, ui_port):
            calls.append((subnet, moonraker_port, ui_port))
            return {
                "subnet": subnet,
                "scanned": 254,
                "found": [
                    {
                        "host": "192.168.7.158",
                        "port": moonraker_port,
                        "name": "K1",
                        "ui_kind": "fluidd",
                        "ui_port": ui_port,
                    }
                ],
            }

        self.app.scan_func = scan_stub
        response = requests.post(self.url("/api/scan"), json={}, timeout=5)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["scanned"], 254)
        self.assertEqual(payload["found"][0]["ui_kind"], "fluidd")
        subnet, moonraker_port, ui_port = calls[0]
        self.assertTrue(subnet.endswith("/24"))
        self.assertEqual((moonraker_port, ui_port), (7125, 4408))

    def test_scan_accepts_subnet_and_ports(self):
        calls = []

        def scan_stub(*, subnet, moonraker_port, ui_port):
            calls.append((subnet, moonraker_port, ui_port))
            return {"subnet": subnet, "scanned": 4, "found": []}

        self.app.scan_func = scan_stub
        response = requests.post(
            self.url("/api/scan"),
            json={"subnet": "192.168.7.0/24", "moonraker_port": 7000, "ui_port": 5000},
            timeout=5,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, [("192.168.7.0/24", 7000, 5000)])
        self.assertEqual(response.json(), {"subnet": "192.168.7.0/24", "scanned": 4, "found": []})

    def test_scan_errors_return_400(self):
        def bad_subnet(*, subnet, moonraker_port, ui_port):
            raise ValueError("Некорректная подсеть: 8.8.8.0/24")

        self.app.scan_func = bad_subnet
        response = requests.post(self.url("/api/scan"), json={"subnet": "8.8.8.0/24"}, timeout=5)
        self.assertEqual(response.status_code, 400)
        self.assertIn("подсеть", response.json()["error"].lower())

        def broken(*, subnet, moonraker_port, ui_port):
            raise OSError("сеть недоступна")

        self.app.scan_func = broken
        failed = requests.post(self.url("/api/scan"), json={}, timeout=5)
        self.assertEqual(failed.status_code, 400)
        self.assertIn("сеть", failed.json()["error"])

    def test_scan_validates_body(self):
        not_string = requests.post(self.url("/api/scan"), json={"subnet": 5}, timeout=5)
        self.assertEqual(not_string.status_code, 400)
        self.assertIn("строкой", not_string.json()["error"])
        bad_port = requests.post(self.url("/api/scan"), json={"ui_port": 0}, timeout=5)
        self.assertEqual(bad_port.status_code, 400)
        self.assertIn("Порт", bad_port.json()["error"])

    def test_scan_single_flight(self):
        started = threading.Event()
        release = threading.Event()
        responses = {}

        def blocking(*, subnet, moonraker_port, ui_port):
            started.set()
            release.wait(10)
            return {"subnet": subnet, "scanned": 1, "found": []}

        self.addCleanup(release.set)
        self.app.scan_func = blocking

        def first_call():
            responses["first"] = requests.post(self.url("/api/scan"), json={}, timeout=10)

        thread = threading.Thread(target=first_call, daemon=True)
        thread.start()
        self.assertTrue(started.wait(5))
        second = requests.post(self.url("/api/scan"), json={}, timeout=3)
        self.assertEqual(second.status_code, 409)
        self.assertIn("Сканирование уже идёт", second.json()["error"])
        release.set()
        thread.join(10)
        self.assertEqual(responses["first"].status_code, 200)

    def test_import_printers(self):
        response = requests.post(
            self.url("/api/printers/import"),
            json={
                "printers": [
                    {"name": "Тест", "host": "127.0.0.1", "port": self.moonraker.port},
                    {"name": "Новый", "host": "10.0.0.30", "port": 7125, "mainsail_port": 4408},
                    {"name": "Дубль", "host": "10.0.0.30", "port": 7125},
                ]
            },
            timeout=3,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["added"], 1)
        self.assertEqual(payload["skipped"], 2)
        hosts = [item["host"] for item in self.saved_config()["printers"]]
        self.assertEqual(hosts.count("10.0.0.30"), 1)
        self.assertIn("10.0.0.30:7125", [printer.key for printer in self.monitor.printers])
        self.assertIn("10.0.0.30:7125", [printer.key for printer in self.app.config.printers])

    def test_import_validates_payload(self):
        empty = requests.post(self.url("/api/printers/import"), json={"printers": []}, timeout=3)
        self.assertEqual(empty.status_code, 400)
        missing_host = requests.post(
            self.url("/api/printers/import"),
            json={"printers": [{"name": "Без адреса"}]},
            timeout=3,
        )
        self.assertEqual(missing_host.status_code, 400)
        self.assertIn("адрес", missing_host.json()["error"])
        too_long = requests.post(
            self.url("/api/printers/import"),
            json={"printers": [{"name": "я" * 61, "host": "10.0.0.31"}]},
            timeout=3,
        )
        self.assertEqual(too_long.status_code, 400)
        self.assertIn("длиннее", too_long.json()["error"])
        bad_list = requests.post(
            self.url("/api/printers/import"),
            json={"printers": "нет"},
            timeout=3,
        )
        self.assertEqual(bad_list.status_code, 400)

    def test_bulk_rename_persists(self):
        created = requests.post(
            self.url("/api/printers"),
            json={"name": "Второй", "host": "10.0.0.10", "port": 7125},
            timeout=3,
        )
        self.assertEqual(created.status_code, 201)
        response = requests.post(
            self.url("/api/printers/rename"),
            json={
                "updates": [
                    {"key": self.key, "name": "Первый"},
                    {"key": "10.0.0.10:7125", "name": "Второй новый"},
                ]
            },
            timeout=3,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["renamed"], 2)
        self.assertEqual(payload["errors"], [])
        names = {
            f"{item['host']}:{item['port']}": item["name"]
            for item in self.saved_config()["printers"]
        }
        self.assertEqual(names[self.key], "Первый")
        self.assertEqual(names["10.0.0.10:7125"], "Второй новый")
        monitor_names = {printer.key: printer.name for printer in self.monitor.printers}
        self.assertEqual(monitor_names[self.key], "Первый")

    def test_bulk_rename_reports_errors(self):
        created = requests.post(
            self.url("/api/printers"),
            json={"name": "Второй", "host": "10.0.0.12", "port": 7125},
            timeout=3,
        )
        self.assertEqual(created.status_code, 201)
        response = requests.post(
            self.url("/api/printers/rename"),
            json={
                "updates": [
                    {"key": "10.9.9.9:7125", "name": "Нет"},
                    {"key": "10.0.0.12:7125", "name": "   "},
                    {"key": self.key, "name": "Новое"},
                ]
            },
            timeout=3,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["renamed"], 1)
        self.assertEqual(len(payload["errors"]), 2)
        self.assertTrue(any("10.9.9.9:7125" in error for error in payload["errors"]))
        self.assertTrue(any("Пустое имя" in error for error in payload["errors"]))
        names = {printer.key: printer.name for printer in self.app.config.printers}
        self.assertEqual(names[self.key], "Новое")
        self.assertEqual(names["10.0.0.12:7125"], "Второй")

    def test_concurrent_printer_updates_persist(self):
        created = requests.post(
            self.url("/api/printers"),
            json={"name": "Второй", "host": "10.0.0.11", "port": 7125},
            timeout=3,
        )
        self.assertEqual(created.status_code, 201)
        barrier = threading.Barrier(2)
        results = {}

        def rename(key, name):
            barrier.wait(5)
            response = requests.put(self.printer_url(key), json={"name": name}, timeout=5)
            results[key] = response.status_code

        threads = [
            threading.Thread(target=rename, args=(self.key, "Первый новый")),
            threading.Thread(target=rename, args=("10.0.0.11:7125", "Второй новый")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(results[self.key], 200)
        self.assertEqual(results["10.0.0.11:7125"], 200)
        names = {
            f"{item['host']}:{item['port']}": item["name"]
            for item in self.saved_config()["printers"]
        }
        self.assertEqual(names[self.key], "Первый новый")
        self.assertEqual(names["10.0.0.11:7125"], "Второй новый")

    def test_web_uses_bulk_rename_endpoint(self):
        source = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(encoding="utf-8")
        self.assertEqual(source.count('"/api/printers/rename"'), 1)
        self.assertIn("BROWSE_TIMEOUT_MS = 300000", source)
        self.assertNotIn('"Переименовано принтеров: "', source)

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

    def test_pause_requires_printing_state(self):
        self.monitor.view = replace(self.monitor.view, state="idle")
        response = requests.post(self.url(f"/api/print/{self.key}/pause"), timeout=3)
        self.assertEqual(response.status_code, 409)
        self.assertIn("Пауза", response.json()["error"])
        self.assertNotIn("/printer/print/pause", self.moonraker.request_paths)

    def test_resume_requires_paused_state(self):
        response = requests.post(self.url(f"/api/print/{self.key}/resume"), timeout=3)
        self.assertEqual(response.status_code, 409)
        self.assertIn("Продолжение", response.json()["error"])
        self.assertNotIn("/printer/print/resume", self.moonraker.request_paths)

    def test_cancel_allowed_when_idle_online(self):
        self.monitor.view = replace(self.monitor.view, state="idle")
        response = requests.post(self.url(f"/api/print/{self.key}/cancel"), timeout=3)
        self.assertEqual(response.status_code, 200)
        self.assertIn("/printer/print/cancel", self.moonraker.request_paths)

    def test_control_rejected_when_offline(self):
        self.monitor.view = replace(self.monitor.view, state="offline", online=False)
        response = requests.post(self.url(f"/api/print/{self.key}/cancel"), timeout=3)
        self.assertEqual(response.status_code, 409)
        self.assertIn("не в сети", response.json()["error"])

    def test_state_config_includes_last_dir(self):
        self.app.config.last_dir = self.tmp.name
        payload = requests.get(self.url("/api/state"), timeout=3).json()
        self.assertEqual(payload["config"]["last_dir"], self.tmp.name)

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

    def history_url(self, query=""):
        return self.url("/api/history/" + quote(self.key, safe="") + query)

    def record_cycle(self, now):
        printing = replace(
            self.monitor.view,
            state="printing",
            file="деталь.gcode",
            parts=[{"name": "Корпус", "count": 35}],
            progress=1.0,
        )
        idle = replace(printing, state="idle", parts=[])
        self.history.record(printing, None, now - 60)
        self.history.record(idle, printing, now)
        return printing, idle

    def test_history_shape(self):
        now = time.time()
        self.record_cycle(now)
        response = requests.get(self.history_url("?since=0"), timeout=3)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["printer"]["key"], self.key)
        self.assertEqual(payload["printer"]["name"], "Тест")
        self.assertEqual(payload["printer"]["state"], self.monitor.view.state)
        self.assertEqual([event["event"] for event in payload["events"]], ["state", "complete"])
        self.assertEqual(payload["last_completion"]["parts"], 35)
        self.assertEqual(payload["segments"][-1]["state"], "idle")
        self.assertEqual(payload["segments"][-1]["file"], "деталь.gcode")

    def test_history_default_window_hides_old_events(self):
        now = time.time()
        self.record_cycle(now - 2 * 86400)
        payload = requests.get(self.history_url(), timeout=3).json()
        self.assertEqual(payload["events"], [])
        self.assertIsNotNone(payload["last_completion"])

    def test_history_limit(self):
        now = time.time()
        view = replace(self.monitor.view, file="деталь.gcode")
        for index in range(4):
            state = "printing" if index % 2 == 0 else "paused"
            previous = replace(view, state="paused" if state == "printing" else "printing") if index else None
            self.history.record(replace(view, state=state), previous, now - 30 + index * 5)
        payload = requests.get(self.history_url("?since=0&limit=2"), timeout=3).json()
        self.assertEqual(len(payload["events"]), 2)

    def test_history_unknown_printer_404(self):
        response = requests.get(self.url("/api/history/10.0.0.1:7125"), timeout=3)
        self.assertEqual(response.status_code, 404)
        self.assertIn("не найден", response.json()["error"])

    def test_history_rejects_bad_bounds(self):
        for query in ("?since=nan", "?since=inf", "?since=-5", "?until=nan", "?until=-1"):
            response = requests.get(self.history_url(query), timeout=3)
            self.assertEqual(response.status_code, 400, query)
            self.assertIn("since", response.json()["error"])

    def test_plan_defaults(self):
        response = requests.get(self.url("/api/plan"), timeout=3)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(sorted(payload), ["day", "month", "week"])
        for period in ("day", "week", "month"):
            self.assertEqual(payload[period]["parts_target"], 0)
            self.assertEqual(payload[period]["hours_target"], 0.0)

    def test_plan_upsert(self):
        response = requests.post(
            self.url("/api/plan"),
            json={"period": "week", "parts_target": 100, "hours_target": 20.5},
            timeout=3,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["week"]["parts_target"], 100)
        self.assertEqual(response.json()["week"]["hours_target"], 20.5)
        again = requests.post(
            self.url("/api/plan"),
            json={"period": "week", "parts_target": 50, "hours_target": 10},
            timeout=3,
        )
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["week"]["parts_target"], 50)
        fetched = requests.get(self.url("/api/plan"), timeout=3).json()
        self.assertEqual(fetched["week"]["parts_target"], 50)
        self.assertEqual(fetched["day"]["parts_target"], 0)

    def test_plan_counts_completed_parts(self):
        now = time.time()
        self.record_cycle(now)
        requests.post(
            self.url("/api/plan"),
            json={"period": "day", "parts_target": 70, "hours_target": 0},
            timeout=3,
        )
        payload = requests.get(self.url("/api/plan"), timeout=3).json()
        self.assertEqual(payload["day"]["parts_done"], 35)
        self.assertEqual(payload["day"]["parts_percent"], 50.0)

    def test_plan_live_parts_printing(self):
        today = datetime.fromtimestamp(time.time()).date().isoformat()
        self.history.import_plan(
            [{"date": today, "key": "ГР3.01.42000.01", "code": "МВСЛ.ГР3.01.42000.01", "name": "Крышка", "target": 20}]
        )
        self.monitor.view = replace(
            self.monitor.view,
            state="printing",
            parts=[{"name": "МВСЛ.ГР3.01.42000.01_Крышка.stl", "count": 16}],
        )
        day = requests.get(self.url("/api/plan"), timeout=3).json()["day"]
        self.assertEqual(day["parts_done"], 0)
        self.assertEqual(day["parts_in_progress"], 16)
        item = day["items"][0]
        self.assertEqual(item["key"], "ГР3.01.42000.01")
        self.assertEqual(item["target"], 20)
        self.assertEqual(item["actual"], 0)
        self.assertEqual(item["in_progress"], 16)

    def test_plan_live_parts_paused(self):
        self.monitor.view = replace(
            self.monitor.view,
            state="paused",
            parts=[{"name": "МВСЛ.ГР3.01.42000.01_Крышка.stl", "count": 16}],
        )
        day = requests.get(self.url("/api/plan"), timeout=3).json()["day"]
        self.assertEqual(day["parts_in_progress"], 16)
        item = day["items"][0]
        self.assertEqual(item["key"], "ГР3.01.42000.01")
        self.assertEqual(item["name"], "Крышка")
        self.assertEqual(item["target"], 0)
        self.assertEqual(item["actual"], 0)
        self.assertEqual(item["in_progress"], 16)

    def test_plan_live_parts_merged_across_printers(self):
        first = replace(
            self.monitor.view,
            state="printing",
            parts=[{"name": "МВСЛ.ГР3.01.42000.01_Крышка.stl", "count": 10}],
        )
        second = replace(first, key="10.0.0.9:7125", name="Второй", parts=[{"name": "МВСЛ.ГР3.01.42000.01_Крышка.stl", "count": 6}])
        idle = replace(first, key="10.0.0.10:7125", name="Третий", state="idle")
        views = [first, second, idle]

        class Views:
            def snapshot(self):
                return {"printers": [asdict(view) for view in views]}

        self.app.monitor = Views()
        day = requests.get(self.url("/api/plan"), timeout=3).json()["day"]
        self.assertEqual(day["parts_in_progress"], 16)
        self.assertEqual(day["items"][0]["in_progress"], 16)

    def test_plan_completed_moves_to_actual_when_idle(self):
        now = time.time()
        self.record_cycle(now)
        self.monitor.view = replace(self.monitor.view, state="idle", parts=[])
        payload = requests.get(self.url("/api/plan"), timeout=3).json()
        self.assertEqual(payload["day"]["parts_done"], 35)
        self.assertEqual(payload["day"]["parts_in_progress"], 0)
        self.monitor.view = replace(
            self.monitor.view,
            state="printing",
            parts=[{"name": "Корпус", "count": 16}],
        )
        busy = requests.get(self.url("/api/plan"), timeout=3).json()["day"]
        self.assertEqual(busy["parts_done"], 35)
        self.assertEqual(busy["parts_in_progress"], 16)
        self.assertEqual(busy["items"][0]["actual"], 35)
        self.assertEqual(busy["items"][0]["in_progress"], 16)

    def test_plan_calendar_live_parts(self):
        self.monitor.view = replace(
            self.monitor.view,
            state="printing",
            parts=[{"name": "МВСЛ.ГР3.01.42000.01_Крышка.stl", "count": 16}],
        )
        payload = requests.get(self.url("/api/plan/calendar"), timeout=3).json()
        today = datetime.fromtimestamp(time.time()).date().isoformat()
        day = next(item for item in payload["days"] if item["date"] == today)
        self.assertEqual(day["parts_in_progress"], 16)
        self.assertEqual(day["actual"], 0)
        self.assertEqual(day["items"][0]["in_progress"], 16)
        self.assertEqual(day["items"][0]["target"], 0)
        week = next(item for item in payload["weeks"] if item["start"] <= today <= item["end"])
        self.assertEqual(week["parts_in_progress"], 16)
        self.assertEqual(week["items"][0]["in_progress"], 16)
        others = [item for item in payload["weeks"] if not item["start"] <= today <= item["end"]]
        self.assertTrue(all(item["parts_in_progress"] == 0 for item in others))

    def test_plan_json_exposes_parts_in_progress(self):
        payload = requests.get(self.url("/api/plan"), timeout=3).json()
        for period in ("day", "week", "month"):
            self.assertIn("parts_in_progress", payload[period])
            self.assertNotIn("in_progress", payload[period])
        payload = requests.get(self.url("/api/plan/calendar"), timeout=3).json()
        for day in payload["days"]:
            self.assertIn("parts_in_progress", day)
            self.assertNotIn("in_progress", day)
        for week in payload["weeks"]:
            self.assertIn("parts_in_progress", week)
            self.assertNotIn("in_progress", week)

    def test_plan_validation(self):
        bad_period = requests.post(
            self.url("/api/plan"),
            json={"period": "year", "parts_target": 1, "hours_target": 1},
            timeout=3,
        )
        self.assertEqual(bad_period.status_code, 400)
        self.assertIn("Период", bad_period.json()["error"])
        bad_parts = requests.post(
            self.url("/api/plan"),
            json={"period": "day", "parts_target": -1, "hours_target": 1},
            timeout=3,
        )
        self.assertEqual(bad_parts.status_code, 400)
        self.assertIn("деталей", bad_parts.json()["error"])
        bad_hours = requests.post(
            self.url("/api/plan"),
            json={"period": "day", "parts_target": 1, "hours_target": -0.5},
            timeout=3,
        )
        self.assertEqual(bad_hours.status_code, 400)
        self.assertIn("часы", bad_hours.json()["error"])
        missing = requests.post(self.url("/api/plan"), json={}, timeout=3)
        self.assertEqual(missing.status_code, 400)
        not_json = requests.post(
            self.url("/api/plan"),
            data=b"{}",
            headers={"Content-Type": "text/plain"},
            timeout=3,
        )
        self.assertEqual(not_json.status_code, 415)


    def test_plan_import_validates_body(self):
        empty = requests.post(self.url("/api/plan/import"), json={}, timeout=3)
        self.assertEqual(empty.status_code, 400)
        self.assertIn("путь", empty.json()["error"])
        not_string = requests.post(self.url("/api/plan/import"), json={"path": 5}, timeout=3)
        self.assertEqual(not_string.status_code, 400)
        missing = requests.post(
            self.url("/api/plan/import"),
            json={"path": str(Path(self.tmp.name) / "нет.xlsx")},
            timeout=3,
        )
        self.assertEqual(missing.status_code, 400)
        self.assertIn("не найден", missing.json()["error"])
        other = Path(self.tmp.name) / "plan.csv"
        other.write_text("дата;деталь", encoding="utf-8")
        bad = requests.post(self.url("/api/plan/import"), json={"path": str(other)}, timeout=3)
        self.assertEqual(bad.status_code, 400)

    def test_plan_import_replaces_week(self):
        today = datetime.fromtimestamp(time.time()).date().isoformat()
        path = Path(self.tmp.name) / "План график.xlsx"
        write_plan_xlsx(path, [today], [("МВСЛ.ГР3.01.42000.01 Крышка (3D)", (12,))])
        response = requests.post(self.url("/api/plan/import"), json={"path": str(path)}, timeout=5)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["imported"], 1)
        self.assertEqual(payload["days"], 1)
        self.assertEqual(payload["days_with_items"], 1)
        self.assertEqual(payload["week_start"], today)
        self.assertEqual(payload["start_date"], today)
        self.assertEqual(payload["end_date"], today)
        self.assertEqual(payload["items"][0]["key"], "ГР3.01.42000.01")
        plan = requests.get(self.url("/api/plan"), timeout=3).json()
        self.assertEqual(plan["day"]["parts_target"], 12)
        self.assertEqual(plan["day"]["items"][0]["target"], 12)
        self.assertEqual(plan["day"]["items"][0]["code"], "МВСЛ.ГР3.01.42000.01")
        self.assertEqual(plan["week"]["parts_target"], 12)
        self.assertEqual(plan["week"]["start_date"], plan["week"]["week_start"])
        write_plan_xlsx(path, [today], [("МВСЛ.ГР3.01.42000.01 Крышка (3D)", (5,))])
        again = requests.post(self.url("/api/plan/import"), json={"path": str(path)}, timeout=5)
        self.assertEqual(again.status_code, 200)
        plan = requests.get(self.url("/api/plan"), timeout=3).json()
        self.assertEqual(plan["day"]["parts_target"], 5)

    def test_plan_import_replaces_whole_week(self):
        monday = datetime.fromtimestamp(time.time()).date()
        monday -= timedelta(days=monday.weekday())
        days = [(monday + timedelta(days=offset)).isoformat() for offset in range(6)]
        path = Path(self.tmp.name) / "План график.xlsx"
        write_plan_xlsx(path, days, [("МВСЛ.ГР3.01.42000.01 Крышка (3D)", (3, 3, 3, 3, 3, 3))])
        first = requests.post(self.url("/api/plan/import"), json={"path": str(path)}, timeout=5)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["days"], 6)
        write_plan_xlsx(path, [days[0]], [("МВСЛ.ГР3.01.42000.01 Крышка (3D)", (9,))])
        second = requests.post(self.url("/api/plan/import"), json={"path": str(path)}, timeout=5)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["days"], 1)
        rows = self.history._conn.execute(
            "SELECT date, target FROM plan_items ORDER BY date"
        ).fetchall()
        self.assertEqual([(row["date"], row["target"]) for row in rows], [(days[0], 9)])

    def test_plan_import_counts_date_columns(self):
        today = datetime.fromtimestamp(time.time()).date()
        days = [today.isoformat(), (today + timedelta(days=1)).isoformat()]
        path = Path(self.tmp.name) / "План график.xlsx"
        write_plan_xlsx(path, days, [("МВСЛ.ГР3.01.42000.01 Крышка (3D)", (5, None))])
        response = requests.post(self.url("/api/plan/import"), json={"path": str(path)}, timeout=5)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["days"], 2)
        self.assertEqual(payload["days_with_items"], 1)
        self.assertEqual(payload["start_date"], days[0])
        self.assertEqual(payload["end_date"], days[1])

    def test_plan_save_keeps_parts_target(self):
        initial = requests.post(
            self.url("/api/plan"),
            json={"period": "week", "parts_target": 100, "hours_target": 5},
            timeout=3,
        )
        self.assertEqual(initial.status_code, 200)
        response = requests.post(
            self.url("/api/plan"),
            json={"period": "week", "hours_target": 7.5},
            timeout=3,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["week"]["parts_target"], 100)
        self.assertEqual(response.json()["week"]["hours_target"], 7.5)
        fetched = requests.get(self.url("/api/plan"), timeout=3).json()
        self.assertEqual(fetched["week"]["parts_target"], 100)

    def test_plan_calendar(self):
        now = time.time()
        today = datetime.fromtimestamp(now).date()
        self.record_cycle(now)
        self.history.import_plan(
            [
                {
                    "date": today.isoformat(),
                    "key": "КОРПУС",
                    "code": "МВСЛ.КОРПУС",
                    "name": "Корпус",
                    "target": 70,
                }
            ]
        )
        response = requests.get(self.url("/api/plan/calendar"), timeout=3)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["month"], today.isoformat()[:7])
        self.assertEqual(payload["month_start"], today.replace(day=1).isoformat())
        self.assertEqual(payload["month_end"], today.replace(day=monthrange(today.year, today.month)[1]).isoformat())
        self.assertEqual(payload["today"], today.isoformat())
        self.assertEqual(len(payload["days"]), monthrange(today.year, today.month)[1])
        day = next(item for item in payload["days"] if item["date"] == today.isoformat())
        self.assertEqual(day["target"], 70)
        self.assertEqual(day["actual"], 35)
        self.assertEqual(day["items"][0]["key"], "КОРПУС")
        self.assertEqual(day["items"][0]["actual"], 35)
        self.assertEqual(day["items"][0]["percent"], 50.0)
        self.assertGreaterEqual(len(payload["weeks"]), 4)
        for week in payload["weeks"]:
            self.assertEqual(len(week["days"]), 7)
            self.assertRegex(week["start"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertEqual(week["days"][0]["date"], week["start"])

    def test_plan_calendar_rejects_bad_month(self):
        for month in ("2024-13", "2024-00", "2024-3", "24-03", "март"):
            response = requests.get(
                self.url("/api/plan/calendar?month=" + quote(month, safe="")), timeout=3
            )
            self.assertEqual(response.status_code, 400, month)
            self.assertIn("формате", response.json()["error"])

    def test_plan_calendar_rejects_month_overflow(self):
        response = requests.get(
            self.url("/api/plan/calendar?month=" + quote("9999-12", safe="")), timeout=3
        )
        self.assertEqual(response.status_code, 400)
        error = response.json()["error"]
        self.assertIn("9999-12", error)
        self.assertNotIn("формате", error)

    def test_fs_browse_plan_kind(self):
        plan = Path(self.tmp.name) / "plan.xlsx"
        plan.write_bytes(b"PK")
        calls = []

        def plan_stub(initial_dir):
            calls.append(initial_dir)
            return [str(plan), str(self.gcode), str(Path(self.tmp.name) / "notes.txt")]

        self.app.browse_plan_files = plan_stub
        response = requests.post(
            self.url("/api/fs/browse"),
            json={"initial_dir": self.tmp.name, "kind": "plan"},
            timeout=3,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["paths"], [str(plan.resolve())])
        self.assertEqual(response.json()["rejected"], 2)
        self.assertEqual(calls, [self.tmp.name])
        self.assertEqual(self.browse_calls, [])
        bad = requests.post(self.url("/api/fs/browse"), json={"kind": "video"}, timeout=3)
        self.assertEqual(bad.status_code, 400)
        self.assertIn("kind", bad.json()["error"])


    def complete_at(self, stamp, items):
        moment = datetime.strptime(stamp, "%Y-%m-%d %H:%M").timestamp()
        printing = replace(
            self.monitor.view,
            state="printing",
            file="деталь.gcode",
            parts=list(items),
            progress=1.0,
        )
        idle = replace(printing, state="idle", parts=[])
        self.history.record(printing, None, moment - 60)
        self.history.record(idle, printing, moment)

    def test_api_qc_returns_items(self):
        self.complete_at("2026-10-29 08:00", [{"name": "МВСЛ.ГР3.01.42000.01 Крышка", "count": 4}])
        response = requests.get(self.url("/api/qc?date=2026-10-29"), timeout=3)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["date"], "2026-10-29")
        self.assertEqual(payload["total"], 4)
        self.assertEqual(payload["positions"], 1)

    def test_api_qc_defaults_to_today(self):
        today = datetime.fromtimestamp(time.time()).date().isoformat()
        self.complete_at(today + " 08:00", [{"name": "Корпус", "count": 35}])
        response = requests.get(self.url("/api/qc"), timeout=3)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["date"], today)
        self.assertEqual(payload["total"], 35)

    def test_api_qc_rejects_bad_date(self):
        response = requests.get(self.url("/api/qc?date=март"), timeout=3)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Неверная дата", response.json()["error"])

    def test_api_qc_accepts_padded_date(self):
        self.complete_at("2026-10-29 08:00", [{"name": "МВСЛ.ГР3.01.42000.01 Крышка", "count": 4}])
        response = requests.get(self.url("/api/qc?date=%202026-10-29%20"), timeout=3)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["date"], "2026-10-29")
        self.assertEqual(response.json()["total"], 4)

    def test_api_qc_csv(self):
        self.complete_at("2026-10-29 08:00", [{"name": "МВСЛ.ГР3.01.42000.01 Крышка", "count": 4}])
        response = requests.get(self.url("/api/qc.csv?date=2026-10-29"), timeout=3)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["Content-Type"].startswith("text/csv"))
        self.assertIn("utf-8", response.headers["Content-Type"])
        lines = response.text.splitlines()
        self.assertEqual(lines[0], "Позиция,Количество")
        self.assertTrue(any("Крышка" in line and "4" in line for line in lines[1:]))

    def test_api_qc_csv_uses_position_text(self):
        self.complete_at("2026-10-29 08:00", [{"name": "МВСЛ.ГР3.01.42000.01 Крышка", "count": 4}])
        payload = requests.get(self.url("/api/qc?date=2026-10-29"), timeout=3).json()
        key = payload["items"][0]["part_key"]
        conn = sqlite3.connect(str(Path(self.tmp.name) / "farm_history.db"))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS parts_catalog (part_key TEXT PRIMARY KEY, number INTEGER)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO parts_catalog (part_key, number) VALUES (?, ?)",
                (key, 7),
            )
            conn.commit()
        finally:
            conn.close()
        response = requests.get(self.url("/api/qc.csv?date=2026-10-29"), timeout=3)
        self.assertEqual(response.status_code, 200)
        lines = response.text.splitlines()
        self.assertEqual(lines[0], "Позиция,Количество")
        self.assertTrue(
            any("Номер 7" in line and "Крышка" in line and "4" in line for line in lines[1:]),
            lines,
        )

    def test_api_catalog_get_lists_items(self):
        self.history.import_plan(
            [{"date": "2026-10-29", "key": "a", "code": "A", "name": "A", "target": 2}]
        )
        self.complete_at("2026-10-29 08:00", [{"name": "МВСЛ.ГР3.01.42000.01 Крышка", "count": 4}])
        response = requests.get(self.url("/api/catalog"), timeout=3)
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        by_key = {item["part_key"]: item for item in items}
        self.assertEqual(by_key["a"]["number"], 1)
        self.assertEqual(by_key["ГР3.01.42000.01"]["code"], "МВСЛ.ГР3.01.42000.01")
        self.assertEqual(by_key["ГР3.01.42000.01"]["number"], 2)

    def test_api_catalog_post_sets_number(self):
        self.complete_at("2026-10-29 08:00", [{"name": "МВСЛ.ГР3.01.42000.01 Крышка", "count": 4}])
        key = requests.get(self.url("/api/qc?date=2026-10-29"), timeout=3).json()["items"][0]["part_key"]
        response = requests.post(self.url("/api/catalog"), json={"part_key": key, "number": 5}, timeout=3)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["item"]["number"], 5)
        items = requests.get(self.url("/api/catalog"), timeout=3).json()["items"]
        self.assertEqual([item for item in items if item["part_key"] == key][0]["number"], 5)

    def test_api_catalog_post_sets_reference_file(self):
        response = requests.post(
            self.url("/api/catalog"),
            json={"part_key": "a", "file_path": str(self.gcode)},
            timeout=3,
        )
        self.assertEqual(response.status_code, 200)
        item = response.json()["item"]
        self.assertEqual(item["file_path"], str(self.gcode))
        self.assertGreater(item["file_size"], 0)

    def test_api_catalog_post_rejects_bad_number(self):
        for number in (0, -2, 2.5, "7"):
            response = requests.post(
                self.url("/api/catalog"), json={"part_key": "a", "number": number}, timeout=3
            )
            self.assertEqual(response.status_code, 400, number)
            self.assertIn("Номер", response.json()["error"])

    def test_api_catalog_post_rejects_missing_file(self):
        missing = str(Path(self.tmp.name) / "нет.gcode")
        response = requests.post(
            self.url("/api/catalog"), json={"part_key": "a", "file_path": missing}, timeout=3
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Файл не найден", response.json()["error"])

    def test_api_catalog_post_rejects_empty_key(self):
        response = requests.post(self.url("/api/catalog"), json={"part_key": "  "}, timeout=3)
        self.assertEqual(response.status_code, 400)
        self.assertIn("part_key", response.json()["error"])

    def test_api_qc_csv_rejects_bad_date(self):
        response = requests.get(self.url("/api/qc.csv?date=нет"), timeout=3)
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
