import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import farm_core
from farm_core import (
    AppConfig,
    ConfigError,
    ControlError,
    Printer,
    duplicate_basenames,
    load_config,
    print_action,
    save_config,
    seed_printers,
    validate_files,
)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config_path = self.root / "printers.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_printer_roundtrip(self):
        printer = Printer(name="F008 100", host="192.168.7.100", port=7125)
        self.assertEqual(Printer.from_dict(printer.to_dict()), printer)
        self.assertEqual(printer.key, "192.168.7.100:7125")
        self.assertEqual(printer.base_url, "http://192.168.7.100:7125")

    def test_config_roundtrip_preserves_cyrillic(self):
        config = AppConfig(printers=[Printer("Принтер 1", "10.0.0.1")], last_dir="C:\\gcode")
        save_config(config, self.config_path)
        loaded = load_config(self.config_path)
        self.assertEqual(loaded.printers, config.printers)
        self.assertEqual(loaded.last_dir, config.last_dir)

    def test_load_config_seeds_when_missing(self):
        device_dir = self.root / "7.3"
        device_dir.mkdir()
        (device_dir / "deviceInfo.json").write_text(
            json.dumps(
                {
                    "groups": [
                        {
                            "list": [
                                {"address": "192.168.7.100", "model": "F008", "moonrakerPort": 0},
                                {"address": "192.168.7.100", "model": "F008", "moonrakerPort": 0},
                                {"address": "", "model": "F008"},
                                {"address": "192.168.7.103", "model": "F008", "moonrakerPort": 7125},
                            ]
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        config = load_config(self.config_path, seed_base=self.root)
        self.assertEqual(
            [p.key for p in config.printers],
            ["192.168.7.100:7125", "192.168.7.103:7125"],
        )
        self.assertEqual(config.printers[0].name, "F008 100")
        self.assertTrue(self.config_path.exists())

    def test_load_config_empty_seed_when_no_device_info(self):
        config = load_config(self.config_path, seed_base=self.root / "missing")
        self.assertEqual(config.printers, [])
        self.assertTrue(self.config_path.exists())

    def test_broken_config_raises(self):
        self.config_path.write_text("{", encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_config(self.config_path)

    def test_load_config_skips_malformed_printers(self):
        self.config_path.write_text(
            json.dumps(
                {
                    "printers": [
                        None,
                        "192.168.1.5",
                        {"host": "192.168.1.6", "name": "ok"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        config = load_config(self.config_path)
        self.assertEqual([printer.host for printer in config.printers], ["192.168.1.6"])
        self.assertEqual(config.printers[0].name, "ok")

    def test_load_config_cp1251_raises(self):
        self.config_path.write_bytes(
            '{"printers": [{"host": "10.0.0.1"}], "last_dir": "C:\\\\папка"}'.encode(
                "cp1251"
            )
        )
        with self.assertRaises(ConfigError):
            load_config(self.config_path)

    def test_duplicate_basenames_case_insensitive(self):
        files = [Path("a/part.gcode"), Path("b/PART.gcode"), Path("c/other.gcode")]
        self.assertEqual(duplicate_basenames(files), ["PART.gcode"])

    def test_validate_files(self):
        good = self.root / "ok.gcode"
        good.write_text("G1 X0", encoding="utf-8")
        empty = self.root / "empty.gcode"
        empty.write_text("", encoding="utf-8")
        wrong = self.root / "note.txt"
        wrong.write_text("x", encoding="utf-8")
        self.assertIsNone(validate_files([good]))
        self.assertIn("Не выбраны", validate_files([]))
        self.assertIn(".gcode", validate_files([wrong]))
        self.assertIn("Пустой", validate_files([empty]))
        self.assertIn("не найден", validate_files([self.root / "missing.gcode"]))


import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from farm_core import (
    ProgressFile,
    UploadCancelled,
    UploadError,
    probe_many,
    probe_printer,
    upload_file,
)


class FakeMoonraker:
    def __init__(self, status=201, delay=0.0):
        self.status = status
        self.delay = delay
        self.control_status = 200
        self.requests = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == "/server/info":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"result": {}}')
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                if self.path in (
                    "/printer/print/pause",
                    "/printer/print/resume",
                    "/printer/print/cancel",
                ):
                    length = int(self.headers.get("Content-Length", "0"))
                    if length:
                        self.rfile.read(length)
                    with outer._lock:
                        outer.requests.append(
                            {
                                "path": self.path,
                                "content_type": self.headers.get("Content-Type", ""),
                                "body": b"",
                            }
                        )
                    self.send_response(outer.control_status)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"result": {"ok": true}}')
                    return
                if self.path != "/server/files/upload":
                    self.send_response(404)
                    self.end_headers()
                    return
                if outer.delay:
                    time.sleep(outer.delay)
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                with outer._lock:
                    outer.requests.append(
                        {
                            "path": self.path,
                            "content_type": self.headers.get("Content-Type", ""),
                            "body": body,
                        }
                    )
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

    def post_count(self):
        with self._lock:
            return len(self.requests)


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "part.gcode"
        self.path.write_text("G1 X1\n", encoding="utf-8")
        self.server = FakeMoonraker().start()
        self.addCleanup(self.server.stop)
        self.printer = Printer("test", "127.0.0.1", self.server.port)

    def tearDown(self):
        self.tmp.cleanup()

    def test_upload_multipart_and_response(self):
        events = []
        result = upload_file(
            self.printer,
            self.path,
            cancel_event=threading.Event(),
            on_progress=lambda done, speed: events.append((done, speed)),
        )
        self.assertEqual(result, {"result": {"ok": True}})
        self.assertEqual(self.server.post_count(), 1)
        body = self.server.requests[0]["body"]
        self.assertIn(b'name="root"', body)
        self.assertIn(b"gcodes", body)
        self.assertIn(b'name="file"; filename="part.gcode"', body)
        self.assertIn(b"G1 X1", body)
        self.assertEqual(events[-1][0], self.path.stat().st_size)

    def test_upload_reports_progress_and_speed(self):
        events = []
        upload_file(
            self.printer,
            self.path,
            cancel_event=threading.Event(),
            on_progress=lambda done, speed: events.append((done, speed)),
        )
        self.assertEqual(events[-1][0], self.path.stat().st_size)
        self.assertGreater(events[-1][1], 0)

    def test_upload_large_payload_exact(self):
        payload = bytes(range(256)) * (128 * 1024 // 256)
        path = self.root / "big.gcode"
        path.write_bytes(payload)
        self.assertGreater(len(payload), farm_core.UPLOAD_CHUNK_SIZE)
        result = upload_file(
            self.printer,
            path,
            cancel_event=threading.Event(),
            on_progress=lambda done, speed: None,
        )
        self.assertEqual(result, {"result": {"ok": True}})
        self.assertEqual(self.server.post_count(), 1)
        request = self.server.requests[0]
        body = request["body"]
        boundary = request["content_type"].split("boundary=", 1)[1].strip('"')
        self.assertTrue(body.endswith(b"--" + boundary.encode() + b"--\r\n"))
        marker = b'filename="big.gcode"'
        header_end = body.index(marker) + len(marker)
        content_start = body.index(b"\r\n\r\n", header_end) + 4
        content_end = body.index(b"\r\n--" + boundary.encode(), content_start)
        self.assertEqual(body[content_start:content_end], payload)

    def test_upload_non_ascii_filename(self):
        name = "деталь 1.gcode"
        path = self.root / name
        path.write_text("G1 X2\n", encoding="utf-8")
        result = upload_file(
            self.printer,
            path,
            cancel_event=threading.Event(),
            on_progress=lambda done, speed: None,
        )
        self.assertEqual(result, {"result": {"ok": True}})
        body = self.server.requests[0]["body"]
        expected = ('filename="' + name + '"').encode("utf-8")
        self.assertIn(expected, body)
        self.assertNotIn(b"filename*=", body)
        header_end = body.index(expected) + len(expected)
        content_start = body.index(b"\r\n\r\n", header_end) + 4
        self.assertTrue(body[content_start:].startswith(path.read_bytes()))

    def test_upload_http_error(self):
        server = FakeMoonraker(status=500).start()
        self.addCleanup(server.stop)
        printer = Printer("bad", "127.0.0.1", server.port)
        with self.assertRaises(UploadError) as caught:
            upload_file(
                printer,
                self.path,
                cancel_event=threading.Event(),
                on_progress=lambda done, speed: None,
            )
        self.assertIn("HTTP 500", str(caught.exception))

    def test_upload_cancel_before_read(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(UploadCancelled):
            upload_file(
                self.printer,
                self.path,
                cancel_event=cancel,
                on_progress=lambda done, speed: None,
            )


class ProgressFileTests(unittest.TestCase):
    def test_progress_file_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "big.gcode"
            path.write_bytes(b"x" * 1024 * 1024)
            cancel = threading.Event()
            progress_file = ProgressFile(
                path,
                path.stat().st_size,
                on_progress=lambda done, speed: cancel.set(),
                cancel_event=cancel,
            )
            with self.assertRaises(UploadCancelled):
                while progress_file.read(8192):
                    pass
            progress_file.close()


class ProbeTests(unittest.TestCase):
    def test_probe_online_offline(self):
        server = FakeMoonraker().start()
        self.addCleanup(server.stop)
        self.assertTrue(
            probe_printer(Printer("online", "127.0.0.1", server.port), timeout=0.5)
        )
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        closed.close()
        self.assertFalse(probe_printer(Printer("offline", "127.0.0.1", port), timeout=0.2))

    def test_probe_many(self):
        server = FakeMoonraker().start()
        self.addCleanup(server.stop)
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        closed_port = closed.getsockname()[1]
        closed.close()
        printers = [
            Printer("online", "127.0.0.1", server.port),
            Printer("offline", "127.0.0.1", closed_port),
        ]
        result = probe_many(printers, timeout=0.2)
        self.assertTrue(result[printers[0].key])
        self.assertFalse(result[printers[1].key])


from farm_core import (
    BatchSender,
    FileProgressEvent,
    FileResultEvent,
    FinishedEvent,
    OverallEvent,
)


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.files = []
        for name in ("a.gcode", "b.gcode"):
            path = self.root / name
            path.write_text("G1 X0\n" * 100, encoding="utf-8")
            self.files.append(path)

    def tearDown(self):
        self.tmp.cleanup()

    def run_batch(self, printers, cancel=None):
        events = []
        sender = BatchSender(printers, self.files, events.append, cancel)
        sender.start()
        sender.join(10)
        self.assertFalse(sender.running)
        return events

    def test_two_printers_two_files(self):
        servers = [FakeMoonraker().start() for _ in range(2)]
        for server in servers:
            self.addCleanup(server.stop)
        printers = [
            Printer(f"p{index}", "127.0.0.1", server.port)
            for index, server in enumerate(servers)
        ]
        events = self.run_batch(printers)
        results = [event for event in events if isinstance(event, FileResultEvent)]
        self.assertEqual(len(results), 4)
        self.assertTrue(all(event.ok for event in results))
        self.assertEqual([server.post_count() for server in servers], [2, 2])
        finished = [event for event in events if isinstance(event, FinishedEvent)][-1]
        self.assertEqual((finished.ok, finished.failed, finished.cancelled), (4, 0, False))
        overall = [event for event in events if isinstance(event, OverallEvent)][-1]
        self.assertEqual((overall.printers_done, overall.printers_total), (2, 2))
        self.assertEqual((overall.files_processed, overall.files_total), (4, 4))
        self.assertTrue(any(isinstance(event, FileProgressEvent) for event in events))

    def test_failure_isolation(self):
        bad = FakeMoonraker(status=500).start()
        good = FakeMoonraker().start()
        self.addCleanup(bad.stop)
        self.addCleanup(good.stop)
        printers = [
            Printer("bad", "127.0.0.1", bad.port),
            Printer("good", "127.0.0.1", good.port),
        ]
        events = self.run_batch(printers)
        results = [event for event in events if isinstance(event, FileResultEvent)]
        self.assertEqual(len(results), 3)
        self.assertEqual(sum(1 for event in results if event.ok), 2)
        self.assertEqual(sum(1 for event in results if not event.ok), 1)
        self.assertEqual(bad.post_count(), 1)
        self.assertEqual(good.post_count(), 2)
        finished = [event for event in events if isinstance(event, FinishedEvent)][-1]
        self.assertEqual((finished.ok, finished.failed), (2, 1))
        overall = [event for event in events if isinstance(event, OverallEvent)][-1]
        self.assertEqual((overall.printers_done, overall.files_processed), (2, 3))

    def test_cancel_before_start(self):
        server = FakeMoonraker().start()
        self.addCleanup(server.stop)
        cancel = threading.Event()
        cancel.set()
        events = self.run_batch([Printer("p", "127.0.0.1", server.port)], cancel)
        self.assertEqual(server.post_count(), 0)
        finished = [event for event in events if isinstance(event, FinishedEvent)][-1]
        self.assertTrue(finished.cancelled)
        self.assertEqual((finished.ok, finished.failed), (0, 0))

    def test_worker_crash_still_finishes(self):
        server = FakeMoonraker().start()
        self.addCleanup(server.stop)
        printers = [
            Printer("good", "127.0.0.1", server.port),
            Printer("bad", "127.0.0.1", server.port),
        ]

        class CrashingSender(BatchSender):
            def _run_printer(self, printer):
                if printer.name == "bad":
                    raise RuntimeError("boom")
                super()._run_printer(printer)

        events = []
        sender = CrashingSender(printers, self.files, events.append)
        sender.start()
        sender.join(10)
        self.assertFalse(sender.running)
        self.assertEqual(server.post_count(), 2)
        finished = [event for event in events if isinstance(event, FinishedEvent)][-1]
        self.assertEqual((finished.ok, finished.failed, finished.cancelled), (2, 0, False))

    def test_emit_failure_still_finishes(self):
        server = FakeMoonraker().start()
        self.addCleanup(server.stop)
        events = []

        def emit(event):
            if isinstance(event, OverallEvent):
                raise RuntimeError("ui closed")
            events.append(event)

        sender = BatchSender([Printer("p", "127.0.0.1", server.port)], self.files, emit)
        sender.start()
        sender.join(10)
        self.assertFalse(sender.running)
        finished = [event for event in events if isinstance(event, FinishedEvent)][-1]
        self.assertFalse(finished.cancelled)


class DashboardConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "printers.json"

    def test_old_config_defaults_mainsail_and_refresh(self):
        self.path.write_text('{"printers": [{"host": "10.0.0.1"}]}', encoding="utf-8")
        config = load_config(self.path)
        self.assertEqual(config.printers[0].mainsail_port, 4408)
        self.assertEqual(config.printers[0].mainsail_url, "http://10.0.0.1:4408")
        self.assertEqual(config.refresh_seconds, 5)

    def test_mainsail_port_validation_and_clamp(self):
        self.path.write_text(
            '{"printers": [{"host": "10.0.0.1", "mainsail_port": 0}], "refresh_seconds": 999}',
            encoding="utf-8",
        )
        config = load_config(self.path)
        self.assertEqual(config.printers[0].mainsail_port, 4408)
        self.assertEqual(config.refresh_seconds, 30)
        saved = json.loads(json.dumps(config.to_dict(), ensure_ascii=False))
        self.assertEqual(saved["printers"][0]["mainsail_port"], 4408)
        self.assertEqual(saved["refresh_seconds"], 30)


class PrintControlTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeMoonraker().start()
        self.addCleanup(self.server.stop)
        self.printer = Printer("test", "127.0.0.1", self.server.port)

    def test_pause_posts_to_moonraker(self):
        print_action(self.printer, "pause")
        self.assertEqual(self.server.requests[-1]["path"], "/printer/print/pause")

    def test_unknown_action_rejected(self):
        with self.assertRaises(ControlError):
            print_action(self.printer, "start")

    def test_control_error_contains_russian_text(self):
        self.server.control_status = 500
        with self.assertRaises(ControlError) as caught:
            print_action(self.printer, "cancel")
        self.assertIn("отменить", str(caught.exception).lower())


if __name__ == "__main__":
    unittest.main()
