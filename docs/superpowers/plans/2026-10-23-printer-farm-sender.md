# Printer Farm Sender Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Windows Tkinter app that uploads one or more `.gcode` files to many Creality Moonraker printers in parallel, with per-printer progress/speed, an overall printer progress bar, cancellation, and a persistent printer list.

**Architecture:** `farm_core.py` holds all non-UI logic: config seeding/loading, printer validation, Moonraker probes/uploads, and a thread-pool `BatchSender` that emits dataclass events. `print_farm_sender.py` is a thin Tkinter shell that consumes those events from a `queue.Queue` every 100 ms, so no worker thread touches widgets.

**Tech Stack:** Python 3.13, Tkinter/ttk, `requests` 2.34.2, `unittest`, `http.server` for fake Moonraker in tests.

**Spec:** `docs/superpowers/specs/2026-10-23-printer-farm-sender-design.md`

## Global Constraints

- Platform: Windows, Python 3.13.7, Tkinter available, `requests` already installed.
- Only standard library + `requests`; do not add dependencies.
- Upload only. Never call a print-start endpoint.
- All UI labels and messages are in Russian.
- Tests must never contact real printers; use `127.0.0.1` fake servers only.
- `printers.json` lives next to the app; write it as UTF-8 with `ensure_ascii=False`.
- Do not modify anything under `C:\Users\i.derkach\Documents\Projects\Crealityprint\CrealityPrint`.
- The workspace is not a git repository and commits were not requested. Do not run `git add`/`git commit`; finish each task by running its tests and reporting the result.
- Keep all code free of comments unless the user asks for them.
- Moonraker endpoints: `GET /server/info`, `POST /server/files/upload` with multipart `root=gcodes` and `file=<basename>`.

---

### Task 1: Config, printer model, seeding, file validation

**Files:**
- Create: `farm_core.py`
- Create: `tests/test_farm_core.py`

**Interfaces:**
- Produces: `DEFAULT_PORT = 7125`, `CONFIG_PATH`, `CREALITY_HOME`, `INFO_PATH`, `UPLOAD_PATH`.
- Produces: `ConfigError`, `Printer(name: str, host: str, port: int = 7125)`, `Printer.key -> "host:port"`, `Printer.base_url -> "http://host:port"`, `Printer.to_dict()`, `Printer.from_dict(data)`.
- Produces: `AppConfig(printers: list[Printer], last_dir: str, window: dict)`.
- Produces: `find_device_info(base: Path) -> Path | None`, `seed_printers(base: Path) -> list[Printer]`.
- Produces: `load_config(path=CONFIG_PATH, *, seed_base=CREALITY_HOME) -> AppConfig`, `save_config(config, path=CONFIG_PATH) -> None`.
- Produces: `duplicate_basenames(files: Iterable[Path]) -> list[str]`, `validate_files(files: Sequence[Path]) -> str | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_farm_core.py`:

```python
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
    Printer,
    duplicate_basenames,
    load_config,
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest discover -s tests -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'farm_core'`.

- [ ] **Step 3: Write the minimal implementation**

Create `farm_core.py`:

```python
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

DEFAULT_PORT = 7125
INFO_PATH = "/server/info"
UPLOAD_PATH = "/server/files/upload"
APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "printers.json"
CREALITY_HOME = Path(os.environ.get("APPDATA", "")) / "Creality" / "Creality Print"


class ConfigError(Exception):
    pass


@dataclass
class Printer:
    name: str
    host: str
    port: int = DEFAULT_PORT

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def key(self) -> str:
        return f"{self.host}:{self.port}"

    def to_dict(self) -> dict:
        return {"name": self.name, "host": self.host, "port": self.port}

    @classmethod
    def from_dict(cls, data: dict) -> "Printer":
        try:
            port = int(data.get("port") or DEFAULT_PORT)
        except (TypeError, ValueError):
            port = DEFAULT_PORT
        if not 1 <= port <= 65535:
            port = DEFAULT_PORT
        return cls(
            name=str(data.get("name") or data.get("host") or "Printer"),
            host=str(data["host"]).strip(),
            port=port,
        )


@dataclass
class AppConfig:
    printers: list[Printer] = field(default_factory=list)
    last_dir: str = ""
    window: dict = field(default_factory=lambda: {"width": 1180, "height": 760})

    def to_dict(self) -> dict:
        return {
            "printers": [printer.to_dict() for printer in self.printers],
            "last_dir": self.last_dir,
            "window": self.window,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        raw_printers = data.get("printers")
        if not isinstance(raw_printers, list):
            raw_printers = []
        printers: list[Printer] = []
        seen: set[str] = set()
        for item in raw_printers:
            try:
                printer = Printer.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            if not printer.host or printer.key in seen:
                continue
            seen.add(printer.key)
            printers.append(printer)
        window = data.get("window")
        if not isinstance(window, dict):
            window = {"width": 1180, "height": 760}
        return cls(
            printers=printers,
            last_dir=str(data.get("last_dir") or ""),
            window=window,
        )


def find_device_info(base: Path = CREALITY_HOME) -> Path | None:
    if not base.is_dir():
        return None
    candidates = [path for path in base.glob("*/deviceInfo.json") if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def seed_printers(base: Path = CREALITY_HOME) -> list[Printer]:
    path = find_device_info(base)
    if path is None:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        return []
    printers: list[Printer] = []
    seen: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            continue
        items = group.get("list")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            address = str(item.get("address") or "").strip()
            if not address:
                continue
            try:
                port = int(item.get("moonrakerPort") or 0) or DEFAULT_PORT
            except (TypeError, ValueError):
                port = DEFAULT_PORT
            key = f"{address}:{port}"
            if key in seen:
                continue
            seen.add(key)
            model = str(item.get("model") or "Printer").strip() or "Printer"
            last_octet = address.rsplit(".", 1)[-1]
            printers.append(Printer(name=f"{model} {last_octet}", host=address, port=port))
    return printers


def load_config(path: Path = CONFIG_PATH, *, seed_base: Path = CREALITY_HOME) -> AppConfig:
    path = Path(path)
    if not path.exists():
        config = AppConfig(printers=seed_printers(seed_base))
        save_config(config, path)
        return config
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Не удалось прочитать {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: ожидался объект JSON")
    return AppConfig.from_dict(data)


def save_config(config: AppConfig, path: Path = CONFIG_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def duplicate_basenames(files: Iterable[Path]) -> list[str]:
    seen: set[str] = set()
    reported: set[str] = set()
    duplicates: list[str] = []
    for file_path in files:
        name = Path(file_path).name
        key = name.lower()
        if key in seen and key not in reported:
            reported.add(key)
            duplicates.append(name)
        seen.add(key)
    return duplicates


def validate_files(files: Sequence[Path]) -> str | None:
    if not files:
        return "Не выбраны файлы."
    for file_path in files:
        file_path = Path(file_path)
        if file_path.suffix.lower() != ".gcode":
            return f"Файл не .gcode: {file_path.name}"
        if not file_path.is_file():
            return f"Файл не найден: {file_path}"
        if file_path.stat().st_size == 0:
            return f"Пустой файл: {file_path.name}"
    duplicates = duplicate_basenames(files)
    if duplicates:
        return "Одинаковые имена файлов: " + ", ".join(duplicates)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest discover -s tests -v`
Expected: 7 tests PASS.

- [ ] **Step 5: Report**

Report the test count and that `farm_core.py` now seeds from `deviceInfo.json` when `printers.json` is absent.

---

### Task 2: Probes and Moonraker upload with progress/cancel

**Files:**
- Modify: `farm_core.py` (add imports and upload section)
- Modify: `tests/test_farm_core.py` (add fake server and upload tests)

**Interfaces:**
- Consumes: `Printer`, `INFO_PATH`, `UPLOAD_PATH` from Task 1.
- Produces: `UploadError(Exception)`, `UploadCancelled(Exception)`.
- Produces: `ProgressFile(path, total, on_progress, cancel_event)` with `read(size=-1)`, `tell()`, `seek()`, `fileno()`, `close()`, `__len__`, context-manager support. `on_progress(done: int, speed_bps: float)`.
- Produces: `probe_printer(printer, timeout=1.5) -> bool`, `probe_many(printers, timeout=1.5) -> dict[str, bool]`.
- Produces: `upload_file(printer, file_path, *, cancel_event, on_progress, session=None) -> dict`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_farm_core.py` before `if __name__ == "__main__":`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest discover -s tests -v`
Expected: FAIL with `ImportError: cannot import name 'ProgressFile'`.

- [ ] **Step 3: Write the minimal implementation**

Add to the top of `farm_core.py`, after the existing imports:

```python
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

import requests
```

Append to the bottom of `farm_core.py`:

```python
class UploadError(Exception):
    pass


class UploadCancelled(Exception):
    pass


class ProgressFile:
    def __init__(
        self,
        path: Path,
        total: int,
        on_progress: Callable[[int, float], None],
        cancel_event: threading.Event,
    ) -> None:
        self._handle = open(path, "rb")
        self._total = total
        self._on_progress = on_progress
        self._cancel_event = cancel_event
        self._sent = 0
        self._last_time = time.monotonic()
        self._last_bytes = 0

    def read(self, size: int = -1) -> bytes:
        if self._cancel_event.is_set() and self._sent < self._total:
            raise UploadCancelled()
        chunk = self._handle.read(size)
        self._sent += len(chunk)
        now = time.monotonic()
        if now - self._last_time >= 0.2 or not chunk:
            elapsed = now - self._last_time
            speed = (self._sent - self._last_bytes) / elapsed if elapsed > 0 else 0.0
            self._last_time = now
            self._last_bytes = self._sent
            self._on_progress(self._sent, speed)
        return chunk

    def tell(self) -> int:
        return self._sent

    def seek(self, offset: int, whence: int = 0) -> int:
        position = self._handle.seek(offset, whence)
        self._sent = self._handle.tell()
        return position

    def fileno(self) -> int:
        return self._handle.fileno()

    def close(self) -> None:
        self._handle.close()

    def __len__(self) -> int:
        return self._total

    def __enter__(self) -> "ProgressFile":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def probe_printer(printer: Printer, timeout: float = 1.5) -> bool:
    try:
        response = requests.get(f"{printer.base_url}{INFO_PATH}", timeout=timeout)
        return response.status_code == 200
    except requests.RequestException:
        return False


def probe_many(printers: Sequence[Printer], timeout: float = 1.5) -> dict[str, bool]:
    if not printers:
        return {}
    with ThreadPoolExecutor(max_workers=len(printers)) as pool:
        future_map = {pool.submit(probe_printer, printer, timeout): printer for printer in printers}
        return {future_map[future].key: future.result() for future in as_completed(future_map)}


def upload_file(
    printer: Printer,
    file_path: Path,
    *,
    cancel_event: threading.Event,
    on_progress: Callable[[int, float], None],
    session: requests.Session | None = None,
) -> dict:
    file_path = Path(file_path)
    own_session = session is None
    session = session or requests.Session()
    try:
        total = file_path.stat().st_size
        with ProgressFile(file_path, total, on_progress, cancel_event) as progress_file:
            response = session.post(
                f"{printer.base_url}{UPLOAD_PATH}",
                data={"root": "gcodes"},
                files={"file": (file_path.name, progress_file)},
                timeout=(3, 60),
            )
        if response.status_code != 201:
            raise UploadError(f"HTTP {response.status_code}: {response.text[:200]}")
        try:
            return response.json()
        except ValueError:
            return {}
    except UploadCancelled:
        raise
    except requests.RequestException as exc:
        if cancel_event.is_set():
            raise UploadCancelled() from exc
        raise UploadError(f"ошибка связи: {exc}") from exc
    except OSError as exc:
        raise UploadError(f"файл недоступен: {exc}") from exc
    finally:
        if own_session:
            session.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest discover -s tests -v`
Expected: 14 tests PASS.

- [ ] **Step 5: Manually verify against the fake server's raw multipart shape**

Run:

```powershell
python -c "import json, threading, tempfile; from pathlib import Path; import sys; sys.path.insert(0, 'tests'); from test_farm_core import FakeMoonraker; import farm_core; t=tempfile.TemporaryDirectory(); p=Path(t.name)/'x.gcode'; p.write_text('G1 X0'); s=FakeMoonraker().start(); r=farm_core.upload_file(farm_core.Printer('x','127.0.0.1',s.port), p, cancel_event=threading.Event(), on_progress=lambda d,v: None); print(r); print(s.requests[0]['content_type']); s.stop(); t.cleanup()"
```

Expected: `{'result': {'ok': True}}` and a `multipart/form-data; boundary=...` content type.

- [ ] **Step 6: Report**

Report the test count and that upload sends `root=gcodes` plus the file basename.

---

### Task 3: Parallel multi-file batch sender with events

**Files:**
- Modify: `farm_core.py` (add event dataclasses and `BatchSender`)
- Modify: `tests/test_farm_core.py` (add batch tests)

**Interfaces:**
- Consumes: `Printer`, `upload_file`, `UploadError`, `UploadCancelled`.
- Produces: frozen dataclasses `PrinterStatusEvent(printer_key, text)`, `FileProgressEvent(printer_key, file_name, file_index, file_total, bytes_done, bytes_total, speed_bps)`, `FileResultEvent(printer_key, file_name, ok, message)`, `OverallEvent(printers_done, printers_total, files_processed, files_total, aggregate_speed)`, `FinishedEvent(ok, failed, cancelled)`.
- Produces: `BatchSender(printers, files, emit, cancel_event=None)` with `start()`, `join(timeout=None)`, `cancel()`, `running` property, `files_total` property.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_farm_core.py` before `if __name__ == "__main__":`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest discover -s tests -v`
Expected: FAIL with `ImportError: cannot import name 'BatchSender'`.

- [ ] **Step 3: Write the minimal implementation**

Append to the bottom of `farm_core.py`:

```python
@dataclass(frozen=True)
class PrinterStatusEvent:
    printer_key: str
    text: str


@dataclass(frozen=True)
class FileProgressEvent:
    printer_key: str
    file_name: str
    file_index: int
    file_total: int
    bytes_done: int
    bytes_total: int
    speed_bps: float


@dataclass(frozen=True)
class FileResultEvent:
    printer_key: str
    file_name: str
    ok: bool
    message: str


@dataclass(frozen=True)
class OverallEvent:
    printers_done: int
    printers_total: int
    files_processed: int
    files_total: int
    aggregate_speed: float


@dataclass(frozen=True)
class FinishedEvent:
    ok: int
    failed: int
    cancelled: bool


class BatchSender:
    def __init__(
        self,
        printers: Sequence[Printer],
        files: Sequence[Path],
        emit: Callable[[object], None],
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.printers = list(printers)
        self.files = [Path(file_path) for file_path in files]
        self.emit = emit
        self.cancel_event = cancel_event or threading.Event()
        self._lock = threading.Lock()
        self._printers_done = 0
        self._files_processed = 0
        self._ok = 0
        self._failed = 0
        self._speeds: dict[str, float] = {printer.key: 0.0 for printer in self.printers}
        self._thread: threading.Thread | None = None

    @property
    def files_total(self) -> int:
        return len(self.files) * len(self.printers)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="batch-sender", daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def cancel(self) -> None:
        self.cancel_event.set()
        self._emit_overall()

    def _run(self) -> None:
        with ThreadPoolExecutor(max_workers=max(1, len(self.printers))) as pool:
            futures = [pool.submit(self._run_printer, printer) for printer in self.printers]
            for future in as_completed(futures):
                future.result()
        self._emit_overall()
        with self._lock:
            event = FinishedEvent(
                ok=self._ok,
                failed=self._failed,
                cancelled=self.cancel_event.is_set(),
            )
        self.emit(event)

    def _run_printer(self, printer: Printer) -> None:
        session = requests.Session()
        status = "готово"
        try:
            for index, file_path in enumerate(self.files, start=1):
                if self.cancel_event.is_set():
                    status = "отменено"
                    break
                self.emit(PrinterStatusEvent(printer.key, f"отправка {index}/{len(self.files)}"))
                try:
                    upload_file(
                        printer,
                        file_path,
                        cancel_event=self.cancel_event,
                        on_progress=lambda done, speed, current=printer, path=file_path, position=index: self._on_progress(
                            current, path, position, done, speed
                        ),
                        session=session,
                    )
                except UploadCancelled:
                    with self._lock:
                        self._speeds[printer.key] = 0.0
                    self.emit(FileResultEvent(printer.key, file_path.name, False, "отменено"))
                    status = "отменено"
                    break
                except UploadError as exc:
                    self._record(printer, ok=False, failed=True)
                    self.emit(FileResultEvent(printer.key, file_path.name, False, str(exc)))
                    status = f"ошибка: {exc}"[:40]
                    break
                else:
                    self._record(printer, ok=True, failed=False)
                    self.emit(FileResultEvent(printer.key, file_path.name, True, "OK"))
        finally:
            session.close()
            with self._lock:
                self._printers_done += 1
                self._speeds[printer.key] = 0.0
            self.emit(PrinterStatusEvent(printer.key, status))
            self._emit_overall()

    def _record(self, printer: Printer, *, ok: bool, failed: bool) -> None:
        with self._lock:
            self._files_processed += 1
            if ok:
                self._ok += 1
            if failed:
                self._failed += 1
            self._speeds[printer.key] = 0.0
        self._emit_overall()

    def _on_progress(
        self,
        printer: Printer,
        file_path: Path,
        index: int,
        done: int,
        speed: float,
    ) -> None:
        total = file_path.stat().st_size
        with self._lock:
            self._speeds[printer.key] = speed
        self.emit(
            FileProgressEvent(
                printer_key=printer.key,
                file_name=file_path.name,
                file_index=index,
                file_total=len(self.files),
                bytes_done=done,
                bytes_total=total,
                speed_bps=speed,
            )
        )
        self._emit_overall()

    def _emit_overall(self) -> None:
        with self._lock:
            event = OverallEvent(
                printers_done=self._printers_done,
                printers_total=len(self.printers),
                files_processed=self._files_processed,
                files_total=self.files_total,
                aggregate_speed=sum(self._speeds.values()),
            )
        self.emit(event)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest discover -s tests -v`
Expected: 16 tests PASS.

- [ ] **Step 5: Report**

Report the test count and that a failing printer does not stop the others.

---

### Task 4: Tkinter UI

**Files:**
- Create: `print_farm_sender.py`

**Interfaces:**
- Consumes: everything public in `farm_core.py`.
- Produces: `FarmApp(tk.Tk)`, `PrinterRow`, `ScrollFrame`, `PrinterManager(tk.Toplevel)`, `PrinterEditDialog(tk.Toplevel)`, `main()`.
- Queue event types: `PrinterStatusEvent`, `FileProgressEvent`, `FileResultEvent`, `OverallEvent`, `FinishedEvent`, and the tuple `("probe", result_dict)`.

- [ ] **Step 1: Write the failing smoke test**

Run: `python -c "import print_farm_sender"`
Expected: FAIL with `ModuleNotFoundError: No module named 'print_farm_sender'`.

- [ ] **Step 2: Write the implementation**

Create `print_farm_sender.py`:

```python
from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import farm_core
from farm_core import (
    AppConfig,
    BatchSender,
    ConfigError,
    FileProgressEvent,
    FileResultEvent,
    FinishedEvent,
    OverallEvent,
    Printer,
    PrinterStatusEvent,
    load_config,
    probe_many,
    save_config,
    validate_files,
)

STATUS_COLORS = {
    "готово": "#1a7f37",
    "online": "#1a7f37",
    "ошибка": "#c62828",
    "offline": "#c62828",
    "отменено": "#8a6d00",
}


class ScrollFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas)
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _on_inner_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")


class PrinterRow:
    def __init__(self, parent, printer: Printer):
        self.printer = printer
        self.var = tk.BooleanVar(value=True)
        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(2, weight=1)
        ttk.Checkbutton(self.frame, variable=self.var).grid(row=0, column=0, padx=(4, 6))
        ttk.Label(self.frame, text=printer.name, width=22).grid(row=0, column=1, sticky="w")
        ttk.Label(self.frame, text=f"{printer.host}:{printer.port}", width=20).grid(
            row=0, column=2, sticky="w"
        )
        self.status = ttk.Label(self.frame, text="не проверен", width=18)
        self.status.grid(row=0, column=3, sticky="w")
        self.bar = ttk.Progressbar(self.frame, length=180, maximum=100)
        self.bar.grid(row=0, column=4, padx=6)
        self.speed = ttk.Label(self.frame, text="", width=12, anchor="e")
        self.speed.grid(row=0, column=5, padx=(0, 8))

    def set_status(self, text: str) -> None:
        color = STATUS_COLORS.get(text)
        if color is None and text.startswith("ошибка"):
            color = STATUS_COLORS["ошибка"]
        self.status.configure(text=text, foreground=color or "#444444")


class PrinterEditDialog(tk.Toplevel):
    def __init__(self, master, printer: Printer | None):
        super().__init__(master)
        self.title("Изменить принтер" if printer else "Добавить принтер")
        self.resizable(False, False)
        self.transient(master)
        self.result: Printer | None = None
        self.name_var = tk.StringVar(value=printer.name if printer else "")
        self.host_var = tk.StringVar(value=printer.host if printer else "")
        self.port_var = tk.StringVar(
            value=str(printer.port if printer else farm_core.DEFAULT_PORT)
        )
        form = ttk.Frame(self, padding=10)
        form.pack(fill="both", expand=True)
        form.columnconfigure(1, weight=1)
        rows = [("Имя", self.name_var), ("Адрес", self.host_var), ("Порт", self.port_var)]
        for row, (label, variable) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=4)
            entry = ttk.Entry(form, textvariable=variable, width=32)
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            if row == 0:
                entry.focus_set()
        buttons = ttk.Frame(form)
        buttons.grid(row=3, column=0, columnspan=2, pady=(10, 0), sticky="e")
        ttk.Button(buttons, text="OK", command=self._ok).pack(side="left")
        ttk.Button(buttons, text="Отмена", command=self.destroy).pack(side="left", padx=(6, 0))
        self.grab_set()
        self.wait_window()

    def _ok(self):
        host = self.host_var.get().strip()
        name = self.name_var.get().strip()
        try:
            port = int(self.port_var.get().strip() or farm_core.DEFAULT_PORT)
        except ValueError:
            messagebox.showwarning("Принтер", "Порт должен быть числом.", parent=self)
            return
        if not host:
            messagebox.showwarning("Принтер", "Укажите адрес.", parent=self)
            return
        if not name:
            name = f"{host}:{port}"
        if not 1 <= port <= 65535:
            messagebox.showwarning("Принтер", "Порт должен быть от 1 до 65535.", parent=self)
            return
        self.result = Printer(name=name, host=host, port=port)
        self.destroy()


class PrinterManager(tk.Toplevel):
    def __init__(self, master, config_data: AppConfig, on_saved):
        super().__init__(master)
        self.title("Принтеры")
        self.geometry("760x420")
        self.transient(master)
        self.config_data = config_data
        self.on_saved = on_saved
        self.tree = ttk.Treeview(
            self, columns=("name", "host", "port"), show="headings", selectmode="browse"
        )
        for column, title, width in [
            ("name", "Имя", 260),
            ("host", "Адрес", 220),
            ("port", "Порт", 100),
        ]:
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=8, pady=8)
        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(buttons, text="Добавить", command=self._add).pack(side="left")
        ttk.Button(buttons, text="Изменить", command=self._edit).pack(side="left", padx=6)
        ttk.Button(buttons, text="Удалить", command=self._delete).pack(side="left")
        ttk.Button(buttons, text="Закрыть", command=self.destroy).pack(side="right")
        self._refresh()

    def _refresh(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for index, printer in enumerate(self.config_data.printers):
            self.tree.insert(
                "", "end", iid=str(index), values=(printer.name, printer.host, printer.port)
            )

    def _selected_index(self):
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def _add(self):
        printer = PrinterEditDialog(self, None).result
        if printer is None:
            return
        if printer.key in {existing.key for existing in self.config_data.printers}:
            messagebox.showwarning("Принтеры", "Такой адрес уже есть.", parent=self)
            return
        self.config_data.printers.append(printer)
        self._refresh()
        self.on_saved()

    def _edit(self):
        index = self._selected_index()
        if index is None:
            return
        printer = PrinterEditDialog(self, self.config_data.printers[index]).result
        if printer is None:
            return
        others = {
            existing.key
            for position, existing in enumerate(self.config_data.printers)
            if position != index
        }
        if printer.key in others:
            messagebox.showwarning("Принтеры", "Такой адрес уже есть.", parent=self)
            return
        self.config_data.printers[index] = printer
        self._refresh()
        self.on_saved()

    def _delete(self):
        index = self._selected_index()
        if index is None:
            return
        del self.config_data.printers[index]
        self._refresh()
        self.on_saved()


class FarmApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Printer Farm Sender")
        self.minsize(900, 560)
        self.config_data = self._load_config()
        try:
            width = int(self.config_data.window.get("width", 1180))
            height = int(self.config_data.window.get("height", 760))
        except (TypeError, ValueError):
            width, height = 1180, 760
        self.geometry(f"{width}x{height}")
        self.files: list[Path] = []
        self.rows: dict[str, PrinterRow] = {}
        self.event_queue: queue.Queue = queue.Queue()
        self.sender: BatchSender | None = None
        self.cancel_event: threading.Event | None = None
        self.online: dict[str, bool] = {}
        self._build_ui()
        self._render_printers()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._drain_events)
        self._probe_all()

    def _load_config(self) -> AppConfig:
        try:
            return load_config(farm_core.CONFIG_PATH)
        except ConfigError as exc:
            messagebox.showwarning(
                "Конфигурация", f"{exc}\n\nЗапуск с пустым списком."
            )
            return AppConfig()

    def _build_ui(self):
        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)

        top = ttk.Frame(root)
        top.pack(fill="x")
        self.files_label = ttk.Label(top, text="Файлы не выбраны")
        self.files_label.pack(side="left")
        ttk.Button(top, text="Выбрать файлы...", command=self._choose_files).pack(
            side="left", padx=(12, 4)
        )
        ttk.Button(top, text="Очистить", command=self._clear_files).pack(side="left")
        ttk.Button(top, text="Принтеры...", command=self._open_manager).pack(side="right")
        ttk.Button(top, text="Проверить связь", command=self._probe_all).pack(
            side="right", padx=6
        )
        ttk.Button(top, text="Выбрать все", command=lambda: self._set_all_checks(True)).pack(
            side="right"
        )
        ttk.Button(top, text="Выбрать онлайн", command=self._select_online).pack(
            side="right", padx=6
        )

        overall = ttk.Frame(root)
        overall.pack(fill="x", pady=(10, 6))
        self.overall_bar = ttk.Progressbar(overall, maximum=1, value=0)
        self.overall_bar.pack(fill="x")
        self.overall_label = ttk.Label(
            overall, text="Принтеры: 0/0 · Файлы: 0/0 · 0.0 МБ/с"
        )
        self.overall_label.pack(anchor="w", pady=(2, 0))

        header = ttk.Frame(root)
        header.pack(fill="x", pady=(6, 2))
        for text, width in [
            ("", 4),
            ("Имя", 22),
            ("Адрес", 20),
            ("Статус", 18),
            ("Прогресс", 24),
            ("Скорость", 12),
        ]:
            ttk.Label(header, text=text, width=width).pack(side="left", padx=2)

        self.list_frame = ScrollFrame(root)
        self.list_frame.pack(fill="both", expand=True)

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=(8, 0))
        self.send_button = ttk.Button(actions, text="Отправить", command=self._start_send)
        self.send_button.pack(side="left")
        self.cancel_button = ttk.Button(
            actions, text="Отмена", command=self._cancel_send, state="disabled"
        )
        self.cancel_button.pack(side="left", padx=8)

        log_frame = ttk.Frame(root)
        log_frame.pack(fill="x", pady=(8, 0))
        self.log = tk.Text(log_frame, height=6, state="disabled", wrap="word")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

    def _render_printers(self):
        for child in self.list_frame.inner.winfo_children():
            child.destroy()
        self.rows.clear()
        for printer in self.config_data.printers:
            row = PrinterRow(self.list_frame.inner, printer)
            row.frame.pack(fill="x", expand=True)
            self.rows[printer.key] = row
        self.overall_bar.configure(maximum=max(1, len(self.config_data.printers)), value=0)
        self._update_overall_label(0, len(self.config_data.printers), 0, 0, 0.0)

    def _selected_printers(self) -> list[Printer]:
        return [
            printer
            for printer in self.config_data.printers
            if self.rows[printer.key].var.get()
        ]

    def _choose_files(self):
        if self.sender is not None:
            messagebox.showwarning("Файлы", "Идёт отправка. Дождитесь завершения.")
            return
        initial = self.config_data.last_dir or (str(self.files[0].parent) if self.files else "")
        kwargs = {"initialdir": initial} if initial else {}
        paths = filedialog.askopenfilenames(
            title="Выберите gcode файлы",
            filetypes=[("G-code", "*.gcode"), ("Все файлы", "*.*")],
            **kwargs,
        )
        if not paths:
            return
        self.files = [Path(path) for path in paths]
        self.config_data.last_dir = str(self.files[0].parent)
        self._update_files_label()
        self._save_config()

    def _clear_files(self):
        if self.sender is not None:
            return
        self.files = []
        self._update_files_label()

    def _update_files_label(self):
        if not self.files:
            self.files_label.configure(text="Файлы не выбраны")
            return
        names = ", ".join(file_path.name for file_path in self.files[:3])
        suffix = f" и ещё {len(self.files) - 3}" if len(self.files) > 3 else ""
        self.files_label.configure(text=f"Файлов: {len(self.files)} — {names}{suffix}")

    def _set_all_checks(self, value: bool):
        for row in self.rows.values():
            row.var.set(value)

    def _select_online(self):
        for key, row in self.rows.items():
            row.var.set(self.online.get(key, False))

    def _probe_all(self):
        printers = self.config_data.printers
        if not printers:
            return
        for row in self.rows.values():
            row.set_status("проверка...")
        threading.Thread(
            target=lambda: self.event_queue.put(("probe", probe_many(printers))),
            daemon=True,
        ).start()

    def _apply_probe(self, result: dict[str, bool]):
        self.online = result
        for key, online in result.items():
            row = self.rows.get(key)
            if row is not None:
                row.set_status("online" if online else "offline")
        self._log(f"Проверка связи: online {sum(result.values())} из {len(result)}")

    def _start_send(self):
        if self.sender is not None:
            return
        printers = self._selected_printers()
        if not printers:
            messagebox.showwarning("Отправка", "Не выбраны принтеры.")
            return
        error = validate_files(self.files)
        if error:
            messagebox.showwarning("Отправка", error)
            return
        for row in self.rows.values():
            row.bar.configure(value=0)
            row.speed.configure(text="")
            row.set_status("ожидание")
        self.overall_bar.configure(maximum=len(printers), value=0)
        self._update_overall_label(
            0, len(printers), 0, len(self.files) * len(printers), 0.0
        )
        self.send_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.cancel_event = threading.Event()
        self.sender = BatchSender(printers, self.files, self.event_queue.put, self.cancel_event)
        self.sender.start()
        self._log(f"Старт: {len(self.files)} файл(ов) × {len(printers)} принтер(ов)")

    def _cancel_send(self):
        if self.sender is not None and self.cancel_event is not None:
            self.cancel_event.set()
            self.cancel_button.configure(state="disabled")
            self._log("Отмена...")

    def _drain_events(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                event = self.event_queue.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _handle_event(self, event):
        if isinstance(event, tuple) and event and event[0] == "probe":
            self._apply_probe(event[1])
        elif isinstance(event, PrinterStatusEvent):
            row = self.rows.get(event.printer_key)
            if row is not None:
                row.set_status(event.text)
                if event.text in ("готово", "ошибка", "отменено") or event.text.startswith("ошибка"):
                    row.bar.configure(value=0)
                    row.speed.configure(text="")
            self._log(f"{self._printer_name(event.printer_key)}: {event.text}")
        elif isinstance(event, FileProgressEvent):
            row = self.rows.get(event.printer_key)
            if row is not None:
                percent = (
                    100.0 * event.bytes_done / event.bytes_total
                    if event.bytes_total
                    else 100.0
                )
                row.bar.configure(value=percent)
                row.speed.configure(text=f"{event.speed_bps / 1048576:.1f} МБ/с")
        elif isinstance(event, FileResultEvent):
            result = "OK" if event.ok else event.message
            self._log(
                f"{self._printer_name(event.printer_key)} · {event.file_name}: {result}"
            )
        elif isinstance(event, OverallEvent):
            self.overall_bar.configure(
                maximum=max(1, event.printers_total), value=event.printers_done
            )
            self._update_overall_label(
                event.printers_done,
                event.printers_total,
                event.files_processed,
                event.files_total,
                event.aggregate_speed,
            )
        elif isinstance(event, FinishedEvent):
            self._finish_send(event)

    def _finish_send(self, event: FinishedEvent):
        self.send_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        prefix = "Отменено" if event.cancelled else "Готово"
        text = f"{prefix}. Успешно: {event.ok}, ошибок: {event.failed}"
        self._log(text)
        self.sender = None
        self.cancel_event = None
        messagebox.showinfo("Отправка", text)

    def _update_overall_label(
        self,
        printers_done: int,
        printers_total: int,
        files_processed: int,
        files_total: int,
        speed: float,
    ):
        self.overall_label.configure(
            text=(
                f"Принтеры: {printers_done}/{printers_total} · "
                f"Файлы: {files_processed}/{files_total} · "
                f"{speed / 1048576:.1f} МБ/с"
            )
        )

    def _printer_name(self, key: str) -> str:
        for printer in self.config_data.printers:
            if printer.key == key:
                return printer.name
        return key

    def _log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _open_manager(self):
        if self.sender is not None:
            messagebox.showwarning("Принтеры", "Идёт отправка. Дождитесь завершения.")
            return
        PrinterManager(self, self.config_data, self._after_manage)

    def _after_manage(self):
        self._save_config()
        self._render_printers()

    def _save_config(self):
        try:
            save_config(self.config_data)
        except OSError as exc:
            messagebox.showwarning("Конфигурация", f"Не удалось сохранить: {exc}")

    def _on_close(self):
        if self.sender is not None:
            if not messagebox.askyesno("Выход", "Идёт отправка. Отменить и выйти?"):
                return
            self.sender.cancel()
            self.sender.join(2.0)
        self.config_data.window["width"] = self.winfo_width()
        self.config_data.window["height"] = self.winfo_height()
        self._save_config()
        self.destroy()


def main():
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    app = FarmApp()
    app.mainloop()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the import check**

Run: `python -c "import print_farm_sender; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Run the full test suite**

Run: `python -m unittest discover -s tests -v`
Expected: 17 tests PASS.

- [ ] **Step 5: Manual UI verification**

Run: `python print_farm_sender.py`

Checklist:
- Window opens, printer rows are visible and scrollable.
- On a machine with no `printers.json`, the list is seeded from the newest `deviceInfo.json`.
- On startup, the online probe runs automatically and rows turn `online`/`offline`.
- "Проверить связь" turns reachable printers green `online` and unreachable red `offline`.
- "Выбрать файлы..." accepts multiple `.gcode` files; the label shows the count.
- Selecting two files and three printers, then "Отправить", fills the overall bar to `3/3`, shows per-printer speed, and finishes with `Успешно: 6, ошибок: 0` for reachable printers.
- "Отмена" during a large upload stops it and re-enables "Отправить".
- "Принтеры..." can add, edit, and delete entries; changes persist after restart.

- [ ] **Step 6: Report**

Report the import check, test count, and which manual checklist items were verified.

---

### Task 5: Launcher and final verification

**Files:**
- Create: `Запустить.bat`

**Interfaces:**
- Consumes: `print_farm_sender.py`.
- Produces: double-click launcher that runs without a console window.

- [ ] **Step 1: Create the launcher**

Create `Запустить.bat` with this exact content:

```bat
@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw print_farm_sender.py
) else (
  python print_farm_sender.py
)
```

- [ ] **Step 2: Verify syntax and tests**

Run:

```powershell
python -m py_compile farm_core.py print_farm_sender.py
python -m unittest discover -s tests -v
```

Expected: `py_compile` exits 0, 17 tests PASS.

- [ ] **Step 3: Verify seeding against the real Creality Print installation**

Run:

```powershell
python -c "import farm_core; path = farm_core.find_device_info(); printers = farm_core.seed_printers(); print(path); print(len(printers)); print(printers[:3])"
```

Expected: the newest `deviceInfo.json` path, then the count of unique non-empty addresses in it (25 for `7.3`, 26 for `7.0`), then the first three seeded printers.

- [ ] **Step 4: Verify the launcher starts the app**

Run: `& ".\Запустить.bat"`
Expected: the Tkinter window opens without a console window. Close it manually.

- [ ] **Step 5: Final report**

Report:
- `python -m unittest discover -s tests -v` result.
- `py_compile` result.
- Seed count.
- That the launcher opened the window.
- Any manual checklist items the user still needs to verify on real printers.
