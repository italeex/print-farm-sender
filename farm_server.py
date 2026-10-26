from __future__ import annotations

import json
import logging
import logging.handlers
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from farm_core import APP_DIR, CONFIG_PATH, AppConfig, ConfigError, load_config
from farm_jobs import JobManager
from farm_monitor import FarmMonitor
from farm_thumbs import ThumbCache

LOGGER = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
PORT_RANGE = range(8765, 8776)
IDLE_TIMEOUT = 600.0
IDLE_CHECK_SECONDS = 5.0
LOG_MAX_BYTES = 1_048_576
LOG_BACKUPS = 3
JSON_CONTENT_TYPE = "application/json; charset=utf-8"
NO_CACHE = "no-cache, no-store, must-revalidate"
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
    ) -> None:
        self.config_path = Path(config_path)
        self.idle_timeout = float(idle_timeout)
        self.journal = Journal()
        self.thumb_cache = ThumbCache()
        self.config_error = ""
        self._stopped = False
        self._shutdown_lock = threading.Lock()
        self.server = None
        self.config = self._load_config()
        if monitor is not None:
            self.monitor = monitor
        else:
            self.monitor = FarmMonitor(self.config, thumb_cache=self.thumb_cache)
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
            "error": self.config_error,
        }
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


class FarmHandler(BaseHTTPRequestHandler):
    app = None
    server_version = "PrinterFarm/1.0"

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path == "/api/state":
            self._send_json(self.app.state_snapshot())
            return
        if path == "/api/journal":
            self._send_json({"entries": self.app.journal.snapshot()})
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
        path = unquote(urlparse(self.path).path)
        if not self._origin_allowed():
            self._send_error(403, "Запрос с чужого источника отклонён")
            return
        if path == "/api/heartbeat":
            self.app.last_heartbeat = time.time()
            self._send_json({"ok": True})
            return
        if path == "/api/shutdown":
            self._send_json({"ok": True})
            threading.Thread(target=self.app.shutdown, name="farm-shutdown", daemon=True).start()
            return
        self._send_error(404, "Неизвестный адрес")

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        port = self.server.server_address[1]
        allowed = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
        return origin in allowed

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
        LOGGER.info("Панель уже запущена: %s", existing)
        webbrowser.open(existing)
        return 0
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
