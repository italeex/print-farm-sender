from __future__ import annotations

import csv
import io
import json
import logging
import logging.handlers
import math
import os
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests

from farm_core import (
    APP_DIR,
    CONFIG_PATH,
    CONTROL_PATHS,
    DEFAULT_PORT,
    MAINSAIL_PORT,
    AppConfig,
    ConfigError,
    ControlError,
    Printer,
    load_config,
    print_action,
    save_config,
)
from farm_history import PERIODS, RETENTION_SECONDS, HistoryStore, _iso_day
from farm_jobs import JobError, JobManager
from farm_monitor import FarmMonitor
from farm_net import detect_ui, local_subnet, scan_subnet
from farm_plan import PLAN_SUFFIXES, parse_plan_file, part_key, split_code_name
from farm_thumbs import ThumbCache, thumbnail_from_file

LOGGER = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
PORT_RANGE = range(8765, 8776)
IDLE_TIMEOUT = 600.0
IDLE_CHECK_SECONDS = 5.0
LOG_MAX_BYTES = 1_048_576
LOG_BACKUPS = 3
MAX_BODY_BYTES = 1_048_576
GCODE_SUFFIX = ".gcode"
JSON_CONTENT_TYPE = "application/json; charset=utf-8"
NO_CACHE = "no-cache, no-store, must-revalidate"
IMAGE_CACHE = "no-cache"
BROWSE_TIMEOUT = 300.0
NAME_MAX_LEN = 60
HISTORY_LIMIT = 500
HISTORY_MAX_LIMIT = 5000
HISTORY_SEGMENT_LIMIT = 2000
DAY_SECONDS = 86400.0
APP_VERSION = "2.8.1"
CONFIG_LOCK = threading.RLock()
BROWSE_LOCK = threading.Lock()
SCAN_LOCK = threading.Lock()
DIALOG_SCRIPT = """
import json
import sys
import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
options = {
    "title": "Выберите gcode-файлы",
    "filetypes": [("G-code", "*.gcode")],
}
if len(sys.argv) > 2 and sys.argv[2] == "plan":
    options = {
        "title": "Выберите файл плана",
        "filetypes": [("План", "*.xlsx *.json"), ("Excel", "*.xlsx"), ("JSON", "*.json")],
    }
if len(sys.argv) > 1 and sys.argv[1]:
    options["initialdir"] = sys.argv[1]
paths = filedialog.askopenfilenames(parent=root, **options)
root.destroy()
sys.stdout.write(json.dumps(list(paths)))
"""
CACHE_HEADERS = {
    "Cache-Control": NO_CACHE,
    "X-Content-Type-Options": "nosniff",
}
MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}
STATIC_ROUTES = {"/", "/styles.css", "/app.js"}


def list_drives() -> list[str]:
    lister = getattr(os, "listdrives", None)
    if lister is not None:
        try:
            return list(lister())
        except OSError:
            return []
    if os.name == "nt":
        try:
            import ctypes

            mask = ctypes.windll.kernel32.GetLogicalDrives()
        except Exception:
            return []
        return [f"{chr(65 + index)}:\\" for index in range(26) if mask & (1 << index)]
    return ["/"]


class BrowseError(Exception):
    pass


def _existing_paths(items, suffixes) -> list[str]:
    result = []
    if not isinstance(items, (list, tuple)):
        return result
    for item in items:
        try:
            path = Path(str(item))
        except (TypeError, ValueError):
            continue
        if path.suffix.lower() not in suffixes:
            continue
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        result.append(str(path.resolve()))
    return result


def existing_gcode_paths(items) -> list[str]:
    return _existing_paths(items, (GCODE_SUFFIX,))


def existing_plan_paths(items) -> list[str]:
    return _existing_paths(items, PLAN_SUFFIXES)


def _plan_span(parsed) -> tuple[str, str]:
    days = [day for day in parsed.get("days") or [] if isinstance(day, str) and day]
    if not days:
        days = [parsed.get("week_start") or ""]
    days = [day for day in days if day]
    if not days:
        return "", ""
    try:
        first = date.fromisoformat(min(days))
        last = date.fromisoformat(max(days))
    except ValueError:
        return days[0], days[-1]
    return (
        (first - timedelta(days=first.weekday())).isoformat(),
        (last + timedelta(days=6 - last.weekday())).isoformat(),
    )


def _native_browse_files(initial_dir: str = "", kind: str = "gcode") -> list[str]:
    try:
        completed = subprocess.run(
            [sys.executable, "-c", DIALOG_SCRIPT, str(initial_dir or ""), str(kind)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=BROWSE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BrowseError("Не удалось открыть диалог выбора файлов") from exc
    if completed.returncode != 0:
        raise BrowseError("Диалог выбора файлов завершился ошибкой")
    try:
        raw_paths = json.loads(completed.stdout or "[]")
    except ValueError as exc:
        raise BrowseError("Диалог выбора файлов вернул некорректный ответ") from exc
    if not isinstance(raw_paths, list):
        raise BrowseError("Диалог выбора файлов вернул некорректный ответ")
    return [str(item) for item in raw_paths]


def _native_browse_plan_files(initial_dir: str = "") -> list[str]:
    return _native_browse_files(initial_dir, "plan")


class Journal:
    def __init__(self, maxlen: int = 200) -> None:
        self._items: deque[dict] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._seq = 0

    def add(self, message: str, level: str = "info") -> None:
        with self._lock:
            self._seq += 1
            self._items.append(
                {
                    "id": self._seq,
                    "time": time.time(),
                    "level": level,
                    "message": str(message),
                }
            )

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [dict(item) for item in self._items]

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


class FarmApp:
    def __init__(
        self,
        config_path: Path = CONFIG_PATH,
        *,
        monitor: FarmMonitor | None = None,
        job: JobManager | None = None,
        start_monitor: bool = False,
        idle_timeout: float = IDLE_TIMEOUT,
        browse_files=None,
        browse_plan_files=None,
        scan_func=None,
        ui_detect_func=None,
        history=None,
    ) -> None:
        self.config_path = Path(config_path)
        self.idle_timeout = float(idle_timeout)
        self.started_at = time.time()
        self.browse_files = browse_files if browse_files is not None else _native_browse_files
        self.browse_plan_files = (
            browse_plan_files if browse_plan_files is not None else _native_browse_plan_files
        )
        self.scan_func = scan_func if scan_func is not None else scan_subnet
        self.ui_detect_func = ui_detect_func if ui_detect_func is not None else detect_ui
        self.journal = Journal()
        self.thumb_cache = ThumbCache()
        self.history = history if history is not None else HistoryStore()
        self.config_error = ""
        self._stopped = False
        self._shutdown_lock = threading.Lock()
        self.server = None
        self.config = self._load_config()
        if monitor is not None:
            self.monitor = monitor
        else:
            self.monitor = FarmMonitor(
                self.config,
                thumb_cache=self.thumb_cache,
                ui_detect=self.ui_detect_func,
                history=self.history,
            )
        self.job = job if job is not None else JobManager(on_event=self.record_event)
        self.last_heartbeat = time.time()
        if start_monitor:
            self.monitor.start()

    def _load_config(self) -> AppConfig:
        try:
            return load_config(self.config_path)
        except (ConfigError, OSError) as exc:
            self.config_error = str(exc)
            LOGGER.error("Конфигурация не загружена: %s", exc)
            self.journal.add(f"Конфигурация не загружена: {exc}", "error")
            self._quarantine_config()
            return AppConfig()

    def _quarantine_config(self) -> None:
        broken = Path(str(self.config_path) + ".broken")
        try:
            if self.config_path.exists():
                self.config_path.replace(broken)
                self.journal.add(f"Повреждённый конфиг сохранён как {broken.name}", "warning")
        except OSError as exc:
            LOGGER.warning("Не удалось переименовать конфиг: %s", exc)

    def record_event(self, event: object) -> None:
        text = getattr(event, "text", None) or getattr(event, "message", None)
        if text:
            self.journal.add(str(text))

    def attach_server(self, server) -> None:
        self.server = server

    def state_snapshot(self) -> dict:
        snapshot = self.monitor.snapshot()
        snapshot["job"] = self.job.snapshot()
        snapshot["config"] = {
            "path": str(self.config_path),
            "printers": len(self.config.printers),
            "refresh_seconds": self.config.refresh_seconds,
            "last_dir": self.config.last_dir,
            "error": self.config_error,
        }
        snapshot["version"] = APP_VERSION
        snapshot["started_at"] = self.started_at
        return snapshot

    def should_shutdown(self, now: float) -> bool:
        return not self.job.running and now - self.last_heartbeat > self.idle_timeout

    def shutdown(self) -> None:
        with self._shutdown_lock:
            if self._stopped:
                return
            self._stopped = True
        self.journal.add("Панель останавливается")
        try:
            self.monitor.stop()
        except Exception:
            LOGGER.exception("Не удалось остановить монитор")
        try:
            self.job.cancel()
        except Exception:
            LOGGER.exception("Не удалось отменить задачу")
        server = self.server
        if server is not None:
            server.shutdown()
        try:
            self.history.close()
        except Exception:
            LOGGER.exception("Не удалось закрыть историю")


class FarmHandler(BaseHTTPRequestHandler):
    app = None
    server_version = "PrinterFarm/1.0"
    _body = b""
    _body_error = None

    def do_GET(self) -> None:
        if not self._guard_host():
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        if path == "/api/state":
            self._send_json(self.app.state_snapshot())
            return
        if path == "/api/journal":
            self._api_journal(query)
            return
        if path == "/api/printers":
            self._api_printer_list()
            return
        if path == "/api/fs":
            self._api_fs(query)
            return
        if path == "/api/files/thumb":
            self._api_file_thumb(query)
            return
        if path.startswith("/api/thumb/"):
            self._api_thumb(path[len("/api/thumb/"):])
            return
        if path == "/api/plan":
            self._send_json(self.app.history.plan(live_parts=self._live_parts()))
            return
        if path == "/api/plan/calendar":
            self._api_plan_calendar(query)
            return
        if path == "/api/qc":
            self._api_qc(query)
            return
        if path == "/api/qc.csv":
            self._api_qc_csv(query)
            return
        if path.startswith("/api/history/"):
            self._api_history(path[len("/api/history/"):], query)
            return
        if path in STATIC_ROUTES:
            target = self._resolve_static(path)
            if target is None:
                self._send_error(404, "Файл не найден")
                return
            self._send_file(target)
            return
        self._send_error(404, "Неизвестный адрес")

    def do_POST(self) -> None:
        if not self._guard_host():
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if not self._guard_origin():
            return
        self._buffer_body()
        if path == "/api/heartbeat":
            self.app.last_heartbeat = time.time()
            self._send_json({"ok": True})
            return
        if path == "/api/shutdown":
            self._send_json({"ok": True})
            threading.Thread(target=self.app.shutdown, name="farm-shutdown", daemon=True).start()
            return
        if path == "/api/send":
            self._api_send()
            return
        if path == "/api/fs/browse":
            self._api_fs_browse()
            return
        if path == "/api/job/cancel":
            self.app.job.cancel()
            self._send_json({"ok": True})
            return
        if path == "/api/printers/rename":
            self._api_printer_rename()
            return
        if path == "/api/printers/import":
            self._api_printer_import()
            return
        if path == "/api/scan":
            self._api_scan()
            return
        if path == "/api/printers":
            self._api_printer_create()
            return
        if path == "/api/plan/import":
            self._api_plan_import()
            return
        if path == "/api/plan":
            self._api_plan()
            return
        if path.startswith("/api/print/"):
            self._api_print(path[len("/api/print/"):])
            return
        self._send_error(404, "Неизвестный адрес")

    def do_PUT(self) -> None:
        if not self._guard_host():
            return
        path = unquote(urlparse(self.path).path)
        if not self._guard_origin():
            return
        self._buffer_body()
        if path.startswith("/api/printers/"):
            self._api_printer_update(path[len("/api/printers/"):])
            return
        self._send_error(404, "Неизвестный адрес")

    def do_DELETE(self) -> None:
        if not self._guard_host():
            return
        path = unquote(urlparse(self.path).path)
        if not self._guard_origin():
            return
        self._buffer_body()
        if path.startswith("/api/printers/"):
            self._api_printer_delete(path[len("/api/printers/"):])
            return
        self._send_error(404, "Неизвестный адрес")

    def _guard_host(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if host in allowed:
            return True
        self._send_error(403, "Недопустимый заголовок Host")
        return False

    def _guard_origin(self) -> bool:
        if self._origin_allowed():
            return True
        self._send_error(403, "Запрос с чужого источника отклонён")
        return False

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        port = self.server.server_address[1]
        allowed = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
        return origin in allowed

    def _api_journal(self, query) -> None:
        since = self._query_int(query, "since")
        entries = [entry for entry in self.app.journal.snapshot() if entry["id"] > since]
        last = entries[-1]["id"] if entries else since
        self._send_json({"entries": entries, "next": last})

    def _api_history(self, raw_key: str, query) -> None:
        key = raw_key.strip("/")
        printer = next((item for item in self.app.config.printers if item.key == key), None)
        if printer is None:
            self._send_error(404, "Принтер не найден")
            return
        now = time.time()
        since = self._query_float(query, "since", now - DAY_SECONDS)
        until = self._query_float(query, "until", 0.0)
        if not math.isfinite(since) or since < 0 or not math.isfinite(until) or until < 0:
            self._send_error(400, "since и until должны быть числами не меньше нуля")
            return
        since = max(since, now - RETENTION_SECONDS)
        until_value = until if until > 0 else None
        limit = self._query_int(query, "limit")
        if limit <= 0:
            limit = HISTORY_LIMIT
        history = self.app.history
        segments = history.segments(key, since, until_value)
        if len(segments) > HISTORY_SEGMENT_LIMIT:
            segments = segments[-HISTORY_SEGMENT_LIMIT:]
        state = ""
        for item in self.app.monitor.snapshot().get("printers", []):
            if str(item.get("key") or "") == key:
                state = str(item.get("state") or "")
                break
        self._send_json(
            {
                "printer": {"key": key, "name": printer.name, "state": state},
                "events": history.events(key, since, until_value, min(limit, HISTORY_MAX_LIMIT)),
                "segments": segments,
                "last_completion": history.last_completion(key),
            }
        )

    def _api_plan(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        period = payload.get("period")
        if not isinstance(period, str) or period not in PERIODS:
            self._send_error(400, "Период должен быть day, week или month")
            return
        parts_target = None
        if payload.get("parts_target") is not None:
            parts_target = self._target_int(payload.get("parts_target"))
            if parts_target is None:
                self._send_error(400, "Число деталей должно быть целым и не меньше нуля")
                return
        hours_target = None
        if payload.get("hours_target") is not None:
            hours_target = self._target_float(payload.get("hours_target"))
            if hours_target is None:
                self._send_error(400, "Целевые часы должны быть числом не меньше нуля")
                return
        self.app.history.set_goal(period, parts_target, hours_target)
        self._send_json(self.app.history.plan(live_parts=self._live_parts()))

    def _live_parts(self) -> list[dict]:
        parts: dict[str, dict] = {}
        for view in self.app.monitor.snapshot().get("printers") or []:
            if str(view.get("state") or "") not in ("printing", "paused"):
                continue
            for item in view.get("parts") or []:
                if not isinstance(item, dict):
                    continue
                key = part_key(item.get("name") or "")
                if not key:
                    continue
                try:
                    count = int(item.get("count") or 0)
                except (TypeError, ValueError):
                    continue
                if count <= 0:
                    continue
                entry = parts.setdefault(key, {"key": key, "code": "", "name": "", "count": 0})
                if not entry["name"]:
                    code, name = split_code_name(item.get("name") or "")
                    entry["code"] = code
                    entry["name"] = name[:-4] if name.lower().endswith(".stl") else name
                entry["count"] += count
        return [parts[key] for key in sorted(parts)]

    def _api_plan_calendar(self, query) -> None:
        month = self._query_text(query, "month").strip()
        try:
            payload = self.app.history.calendar(month or None, live_parts=self._live_parts())
        except ValueError as exc:
            self._send_error(400, str(exc) or "Некорректный месяц")
            return
        self._send_json(payload)

    def _api_qc(self, query) -> None:
        day = self._qc_day(query)
        if day is None:
            return
        self._send_json(self.app.history.qc(day))

    def _api_qc_csv(self, query) -> None:
        day = self._qc_day(query)
        if day is None:
            return
        report = self.app.history.qc(day)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Позиция", "Количество"])
        for item in report.get("items") or []:
            writer.writerow([item.get("name") or item.get("code") or "", item.get("count") or 0])
        self._send_csv(buf.getvalue())

    def _qc_day(self, query) -> str | None:
        raw = self._query_text(query, "date")
        day = _iso_day(raw) if raw else date.today().isoformat()
        if not day:
            self._send_error(400, "Неверная дата")
            return None
        return day

    def _api_fs(self, query) -> None:
        raw = self._query_text(query, "path")
        if not raw:
            entries = [
                {"name": drive, "path": drive, "is_dir": True, "size": 0, "modified": 0.0}
                for drive in list_drives()
            ]
            self._send_json({"path": "", "parent": None, "entries": entries})
            return
        target = Path(raw)
        entries = []
        try:
            with os.scandir(target) as scanner:
                for item in scanner:
                    try:
                        if item.is_dir():
                            entries.append(
                                {
                                    "name": item.name,
                                    "path": item.path,
                                    "is_dir": True,
                                    "size": 0,
                                    "modified": 0.0,
                                }
                            )
                            continue
                        if not item.name.lower().endswith(GCODE_SUFFIX):
                            continue
                        stat = item.stat()
                    except OSError:
                        continue
                    entries.append(
                        {
                            "name": item.name,
                            "path": item.path,
                            "is_dir": False,
                            "size": stat.st_size,
                            "modified": stat.st_mtime,
                        }
                    )
        except PermissionError:
            self._send_error(403, "Нет доступа к папке")
            return
        except ValueError:
            self._send_error(400, "Некорректный путь")
            return
        except OSError:
            self._send_error(404, "Папка не найдена")
            return
        entries.sort(key=lambda entry: (not entry["is_dir"], entry["name"].lower()))
        parent = target.parent
        self._send_json(
            {
                "path": str(target),
                "parent": "" if parent == target else str(parent),
                "entries": entries,
            }
        )

    def _api_plan_import(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        raw_path = payload.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            self._send_error(400, "Не указан путь к файлу плана")
            return
        target = Path(raw_path.strip())
        if not target.is_file():
            self._send_error(400, f"Файл не найден: {target}")
            return
        try:
            parsed = parse_plan_file(target)
        except (OSError, ValueError) as exc:
            self._send_error(400, str(exc) or "Не удалось прочитать файл плана")
            return
        result = self.app.history.import_plan(parsed["items"], *_plan_span(parsed))
        if not result["imported"]:
            self._send_error(400, "В файле плана нет данных")
            return
        days = list(parsed["days"])
        first = days[0] if days else parsed["week_start"]
        last = days[-1] if days else parsed["week_start"]
        self.app.journal.add(f"План импортирован: деталей {result['imported']}, дней {len(days)}")
        self._send_json(
            {
                "imported": result["imported"],
                "days": len(days),
                "days_with_items": result["days"],
                "week_start": parsed["week_start"],
                "start_date": first,
                "end_date": last,
                "items": parsed["items"],
            }
        )

    def _api_fs_browse(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        initial_dir = payload.get("initial_dir", "")
        if not isinstance(initial_dir, str):
            self._send_error(400, "Поле initial_dir должно быть строкой")
            return
        kind = payload.get("kind")
        if kind in (None, ""):
            kind = "gcode"
        if not isinstance(kind, str) or kind not in ("gcode", "plan"):
            self._send_error(400, "Поле kind должно быть gcode или plan")
            return
        if kind == "plan":
            browse = getattr(self.app, "browse_plan_files", None)
            fallback = _native_browse_plan_files
        else:
            browse = getattr(self.app, "browse_files", None)
            fallback = _native_browse_files
        if not callable(browse):
            browse = fallback
        if not BROWSE_LOCK.acquire(blocking=False):
            self._send_error(409, "Диалог уже открыт")
            return
        try:
            raw = browse(initial_dir.strip())
        except Exception as exc:
            LOGGER.warning("Диалог выбора файлов недоступен: %s", exc)
            self._send_error(501, "Не удалось открыть диалог выбора файлов")
            return
        finally:
            BROWSE_LOCK.release()
        if not isinstance(raw, (list, tuple)):
            raw = []
        paths = existing_plan_paths(raw) if kind == "plan" else existing_gcode_paths(raw)
        self._send_json(
            {
                "paths": paths,
                "rejected": len(raw) - len(paths),
                "cancelled": not raw,
            }
        )

    def _api_scan(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        raw_subnet = payload.get("subnet")
        if raw_subnet is None:
            raw_subnet = ""
        if not isinstance(raw_subnet, str):
            self._send_error(400, "Поле subnet должно быть строкой")
            return
        moonraker_port = self._parse_port(payload.get("moonraker_port"), DEFAULT_PORT)
        if moonraker_port is None:
            return
        ui_port = self._parse_port(payload.get("ui_port"), MAINSAIL_PORT)
        if ui_port is None:
            return
        if not SCAN_LOCK.acquire(blocking=False):
            self._send_error(409, "Сканирование уже идёт")
            return
        try:
            subnet = raw_subnet.strip() or local_subnet()
            result = self.app.scan_func(subnet=subnet, moonraker_port=moonraker_port, ui_port=ui_port)
        except (ValueError, OSError) as exc:
            self._send_error(400, str(exc) or "Не удалось сканировать подсеть")
            return
        except Exception:
            LOGGER.exception("Сканирование подсети завершилось ошибкой")
            self._send_error(500, "Сканирование не удалось")
            return
        finally:
            SCAN_LOCK.release()
        self._send_json(result)

    def _api_printer_import(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        raw_printers = payload.get("printers")
        if not isinstance(raw_printers, list) or not raw_printers:
            self._send_error(400, "Ожидался непустой список printers")
            return
        parsed: list[Printer] = []
        seen: set[str] = set()
        for item in raw_printers:
            if not isinstance(item, dict):
                self._send_error(400, "Некорректная запись принтера")
                return
            try:
                printer = Printer.from_dict(item)
            except (KeyError, TypeError, ValueError):
                self._send_error(400, "У принтера не указан адрес")
                return
            if not printer.host:
                self._send_error(400, "У принтера не указан адрес")
                return
            if len(printer.name) > NAME_MAX_LEN:
                self._send_error(400, f"Имя принтера не должно быть длиннее {NAME_MAX_LEN} символов")
                return
            if printer.key in seen:
                continue
            seen.add(printer.key)
            parsed.append(printer)
        with CONFIG_LOCK:
            previous = list(self.app.config.printers)
            known = {printer.key for printer in previous}
            added = [printer for printer in parsed if printer.key not in known]
            skipped = len(raw_printers) - len(added)
            if not added:
                self._send_json({"added": 0, "skipped": skipped})
                return
            self.app.config.printers = previous + added
            if not self._persist_printers(previous):
                return
        self.app.journal.add(f"Импортировано принтеров: {len(added)}")
        self._send_json({"added": len(added), "skipped": skipped})

    def _api_file_thumb(self, query) -> None:
        raw = self._query_text(query, "path")
        if not raw:
            self._send_error(400, "Не указан путь к файлу")
            return
        data = thumbnail_from_file(Path(raw))
        if not data:
            self._send_error(404, "Превью не найдено")
            return
        self._send_image(data)

    def _api_thumb(self, raw_key: str) -> None:
        key = raw_key.strip("/")
        views = self.app.monitor.snapshot().get("printers") or []
        view = next((item for item in views if item.get("key") == key), None)
        if view is None:
            self._send_error(404, "Принтер не найден")
            return
        cache_key = (key, str(view.get("file") or ""))
        data = self.app.thumb_cache.get(cache_key, lambda: None)
        if not data:
            self._send_error(404, "Превью не найдено")
            return
        version = str(view.get("thumb_version") or "")
        etag = f'"{version}"' if version else ""
        if etag and self._etag_matches(etag):
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", IMAGE_CACHE)
            self.end_headers()
            return
        self._send_image(data, etag)

    def _api_print(self, rest: str) -> None:
        key, _, action = rest.rpartition("/")
        if not key or not action:
            self._send_error(404, "Неизвестный адрес")
            return
        if action not in CONTROL_PATHS:
            self._send_error(400, f"Неизвестное действие: {action}")
            return
        printer = next((item for item in self.app.config.printers if item.key == key), None)
        if printer is None:
            self._send_error(404, "Принтер не найден")
            return
        views = self.app.monitor.snapshot().get("printers") or []
        view = next((item for item in views if item.get("key") == key), None) or {}
        if not view.get("online"):
            self._send_error(409, "Принтер не в сети")
            return
        current = str(view.get("state") or "")
        if action == "pause" and current != "printing":
            self._send_error(409, "Пауза доступна только во время печати")
            return
        if action == "resume" and current != "paused":
            self._send_error(409, "Продолжение доступно только с паузы")
            return
        try:
            print_action(printer, action)
        except ControlError as exc:
            self._send_error(502, str(exc))
            return
        try:
            self.app.monitor.refresh()
        except Exception:
            LOGGER.exception("Не удалось обновить состояние после действия")
        self._send_json({"ok": True, "action": action})

    def _api_send(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        raw_printers = payload.get("printers")
        raw_files = payload.get("files")
        if not isinstance(raw_printers, list) or not isinstance(raw_files, list):
            self._send_error(400, "Ожидались списки printers и files")
            return
        keys = list(dict.fromkeys(str(item) for item in raw_printers))
        known = {printer.key: printer for printer in self.app.config.printers}
        missing = [key for key in keys if key not in known]
        if missing:
            self._send_error(400, "Принтер не найден: " + ", ".join(missing))
            return
        printers = [known[key] for key in keys]
        if not printers:
            self._send_error(400, "Не выбраны принтеры.")
            return
        paths = [Path(str(item)) for item in raw_files]
        try:
            self.app.job.start(printers, paths)
        except JobError as exc:
            self._send_error(400, str(exc))
            return
        if paths:
            with CONFIG_LOCK:
                self.app.config.last_dir = str(paths[0].parent)
                try:
                    save_config(self.app.config, self.app.config_path)
                except OSError as exc:
                    self.app.journal.add(f"Не удалось сохранить каталог: {exc}", "warning")
        self.app.journal.add(f"Отправка запущена: файлов {len(paths)}, принтеров {len(printers)}")
        self._send_json({"ok": True, "files": len(paths), "printers": len(printers)}, status=202)

    def _api_printer_list(self) -> None:
        self._send_json({"printers": [self._printer_payload(item) for item in self.app.config.printers]})

    def _api_printer_create(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        printer = self._parse_printer(payload)
        if printer is None:
            return
        with CONFIG_LOCK:
            printers = list(self.app.config.printers)
            if any(item.key == printer.key for item in printers):
                self._send_error(409, "Принтер с таким адресом уже добавлен")
                return
            self.app.config.printers = printers + [printer]
            if not self._persist_printers(printers):
                return
        self.app.journal.add(f"Принтер добавлен: {printer.name}")
        self._send_json({"ok": True, "printer": self._printer_payload(printer)}, status=201)

    def _api_printer_update(self, raw_key: str) -> None:
        key = raw_key.strip("/")
        payload = self._read_json()
        if payload is None:
            return
        with CONFIG_LOCK:
            printers = list(self.app.config.printers)
            index = next((position for position, item in enumerate(printers) if item.key == key), None)
            if index is None:
                self._send_error(404, "Принтер не найден")
                return
            printer = self._parse_printer(payload, printers[index])
            if printer is None:
                return
            if any(item.key == printer.key for position, item in enumerate(printers) if position != index):
                self._send_error(409, "Принтер с таким адресом уже добавлен")
                return
            updated = list(printers)
            updated[index] = printer
            self.app.config.printers = updated
            if not self._persist_printers(printers):
                return
        self.app.journal.add(f"Принтер сохранён: {printer.name}")
        self._send_json({"ok": True, "printer": self._printer_payload(printer)})

    def _api_printer_rename(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        raw_updates = payload.get("updates")
        if not isinstance(raw_updates, list) or not raw_updates:
            self._send_error(400, "Ожидался непустой список updates")
            return
        errors: list[str] = []
        applied: dict[str, str] = {}
        with CONFIG_LOCK:
            printers = list(self.app.config.printers)
            known = {item.key for item in printers}
            for item in raw_updates:
                if not isinstance(item, dict):
                    errors.append("Некорректная запись")
                    continue
                key = str(item.get("key") or "").strip()
                name = str(item.get("name") or "").strip()
                if not key:
                    errors.append("Не указан ключ принтера")
                    continue
                if key not in known:
                    errors.append(f"Принтер не найден: {key}")
                    continue
                if key in applied:
                    errors.append(f"Повторное обновление: {key}")
                    continue
                if not name:
                    errors.append(f"Пустое имя: {key}")
                    continue
                if len(name) > NAME_MAX_LEN:
                    errors.append(f"Имя длиннее {NAME_MAX_LEN} символов: {key}")
                    continue
                applied[key] = name
            if applied:
                updated = [
                    Printer(
                        name=applied[item.key],
                        host=item.host,
                        port=item.port,
                        mainsail_port=item.mainsail_port,
                    )
                    if item.key in applied
                    else item
                    for item in printers
                ]
                self.app.config.printers = updated
                if not self._persist_printers(printers):
                    return
        if applied:
            self.app.journal.add(f"Переименовано принтеров: {len(applied)}")
        self._send_json({"ok": True, "renamed": len(applied), "errors": errors})

    def _api_printer_delete(self, raw_key: str) -> None:
        key = raw_key.strip("/")
        with CONFIG_LOCK:
            printers = list(self.app.config.printers)
            index = next((position for position, item in enumerate(printers) if item.key == key), None)
            if index is None:
                self._send_error(404, "Принтер не найден")
                return
            self.app.config.printers = [item for position, item in enumerate(printers) if position != index]
            if not self._persist_printers(printers):
                return
        self.app.journal.add(f"Принтер удалён: {key}")
        self._send_json({"ok": True})

    def _parse_printer(self, payload: dict, base: Printer | None = None) -> Printer | None:
        host = str(payload.get("host") or "").strip()
        if base is not None and not host:
            host = base.host
        if not host:
            self._send_error(400, "Укажите адрес принтера")
            return None
        port = self._parse_port(payload.get("port"), base.port if base is not None else DEFAULT_PORT)
        if port is None:
            return None
        mainsail_port = self._parse_port(
            payload.get("mainsail_port"),
            base.mainsail_port if base is not None else MAINSAIL_PORT,
        )
        if mainsail_port is None:
            return None
        raw_name = payload.get("name")
        if raw_name is None and base is not None:
            name = base.name
        else:
            name = str(raw_name or "").strip()
        if not name:
            if base is not None:
                self._send_error(400, "Имя принтера не может быть пустым")
                return None
            name = host
        if len(name) > NAME_MAX_LEN:
            self._send_error(400, f"Имя принтера не должно быть длиннее {NAME_MAX_LEN} символов")
            return None
        return Printer(name=name, host=host, port=port, mainsail_port=mainsail_port)

    def _parse_port(self, raw, fallback: int) -> int | None:
        if raw is None or raw == "":
            return fallback
        try:
            value = int(raw)
        except (TypeError, ValueError):
            self._send_error(400, "Порт должен быть числом")
            return None
        if not 1 <= value <= 65535:
            self._send_error(400, "Порт должен быть от 1 до 65535")
            return None
        return value

    def _persist_printers(self, previous) -> bool:
        try:
            save_config(self.app.config, self.app.config_path)
        except OSError as exc:
            self.app.config.printers = previous
            self._send_error(500, f"Не удалось сохранить конфигурацию: {exc}")
            return False
        self.app.monitor.set_printers(self.app.config.printers)
        return True

    @staticmethod
    def _printer_payload(printer: Printer) -> dict:
        payload = printer.to_dict()
        payload["key"] = printer.key
        return payload

    def _buffer_body(self) -> None:
        self._body = b""
        self._body_error = None
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            self._body_error = (400, "Некорректная длина тела запроса")
            return
        if length < 0:
            self._body_error = (400, "Некорректная длина тела запроса")
            return
        if length > MAX_BODY_BYTES:
            self.rfile.read(MAX_BODY_BYTES)
            self._body_error = (413, "Тело запроса больше 1 МБ")
            self.close_connection = True
            return
        if length:
            self._body = self.rfile.read(length)

    def _read_json(self) -> dict | None:
        if self._body_error is not None:
            status, message = self._body_error
            self._send_error(status, message)
            return None
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            self._send_error(415, "Ожидался Content-Type application/json")
            return None
        if not self._body:
            return {}
        try:
            payload = json.loads(self._body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._send_error(400, "Некорректный JSON в запросе")
            return None
        if not isinstance(payload, dict):
            self._send_error(400, "Ожидался JSON-объект")
            return None
        return payload

    @staticmethod
    def _query_text(query, name: str) -> str:
        return (query.get(name) or [""])[0].strip()

    def _query_int(self, query, name: str) -> int:
        raw = self._query_text(query, name)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _query_float(query, name: str, fallback: float) -> float:
        raw = (query.get(name) or [""])[0].strip()
        if not raw:
            return fallback
        try:
            return float(raw)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _target_int(raw):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        if not math.isfinite(raw) or raw < 0 or int(raw) != raw:
            return None
        return int(raw)

    @staticmethod
    def _target_float(raw):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        if not math.isfinite(raw) or raw < 0:
            return None
        return float(raw)

    def _etag_matches(self, etag: str) -> bool:
        header = (self.headers.get("If-None-Match") or "").strip()
        if not header:
            return False
        if header == "*":
            return True
        return etag in [token.strip() for token in header.split(",")]


    def _resolve_static(self, path: str) -> Path | None:
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        try:
            target = (WEB_DIR / relative).resolve()
        except (OSError, ValueError):
            return None
        if not target.is_relative_to(WEB_DIR.resolve()):
            return None
        if not target.is_file():
            return None
        return target

    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", JSON_CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        for name, value in CACHE_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str) -> None:
        self._send_json({"ok": False, "error": message}, status=status)

    def _send_file(self, path: Path) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self._send_error(404, "Файл не найден")
            return
        content_type = MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in CACHE_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_csv(self, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in CACHE_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_image(self, data: bytes, etag: str = "") -> None:
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", IMAGE_CACHE)
        self.send_header("X-Content-Type-Options", "nosniff")
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args) -> None:
        LOGGER.debug("%s - %s", self.address_string(), format % args)


def create_server(app, host: str = "127.0.0.1", port: int = 0):
    handler = type("BoundHandler", (FarmHandler,), {"app": app})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    if hasattr(app, "attach_server"):
        app.attach_server(server)
    return server


def find_running_instance(host: str, ports, timeout: float = 0.5) -> str | None:
    for port in ports:
        url = f"http://{host}:{port}"
        try:
            response = requests.get(f"{url}/api/state", timeout=timeout)
        except Exception:
            continue
        if response.status_code == 200:
            return url
    return None


def read_instance_state(url: str, timeout: float = 1.0) -> dict | None:
    try:
        response = requests.get(f"{url}/api/state", timeout=timeout)
    except Exception:
        return None
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def stop_instance(url: str, timeout: float = 2.0) -> None:
    try:
        requests.post(f"{url}/api/shutdown", timeout=timeout)
    except Exception:
        pass


def configure_logging(path: Path) -> None:
    path = Path(path)
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    for handler in logger.handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            if Path(handler.baseFilename) == path.resolve():
                return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)


def main() -> int:
    configure_logging(APP_DIR / "farm.log")
    existing = find_running_instance("127.0.0.1", PORT_RANGE)
    if existing:
        state = read_instance_state(existing)
        if state and state.get("version") == APP_VERSION:
            LOGGER.info("Панель уже запущена: %s", existing)
            webbrowser.open(existing)
            return 0
        LOGGER.warning("Найден устаревший экземпляр %s — перезапускаем", existing)
        stop_instance(existing)
        for _ in range(50):
            if find_running_instance("127.0.0.1", PORT_RANGE, timeout=0.1) is None:
                break
            time.sleep(0.1)
    app = FarmApp(start_monitor=True)
    server = None
    for port in PORT_RANGE:
        try:
            server = create_server(app, port=port)
        except OSError:
            continue
        break
    if server is None:
        LOGGER.error("Не удалось занять порт для панели")
        return 1
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    app.monitor.start()
    LOGGER.info("Панель запущена: %s", url)
    webbrowser.open(url)

    def watch_idle() -> None:
        while True:
            time.sleep(IDLE_CHECK_SECONDS)
            if app.should_shutdown(time.time()):
                LOGGER.info("Панель остановлена по бездействию")
                app.shutdown()
                return

    threading.Thread(target=watch_idle, name="farm-idle", daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())







