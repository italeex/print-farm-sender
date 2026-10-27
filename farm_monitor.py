import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from urllib.parse import quote

import requests

from farm_core import AppConfig, Printer
from farm_errors import ErrorInfo, parse_error
from farm_net import detect_ui
from farm_parts import parse_parts_bytes
from farm_thumbs import MAX_THUMB_BYTES, ThumbCache, extract_thumbnail, load_thumbnail

LOGGER = logging.getLogger(__name__)

PRINT_STATE_MAP = {
    "printing": "printing",
    "paused": "paused",
    "error": "error",
}

INFO_PATH = "/printer/info"
OBJECTS_PATH = "/printer/objects/query?print_stats&virtual_sdcard&extruder&heater_bed&display_status"
LOG_PATH = "/server/files/logs/klippy.log"
LOG_RANGE = "bytes=-65536"
MAX_LOG_BYTES = 65536
GCODE_PATH = "/server/files/gcodes/"
GCODE_RANGE = f"bytes=0-{MAX_THUMB_BYTES - 1}"
GCODE_TIMEOUT = 5.0
INFO_TIMEOUT = 2.5
OBJECTS_TIMEOUT = 2.5
LOG_TIMEOUT = 3.0
MAX_WORKERS = 16
THUMB_WORKERS = 4
BACKOFF_BASE = 10.0
BACKOFF_CAP = 60.0
LOOP_STEP = 0.5
JOIN_TIMEOUT = 3.0
UI_KIND_TTL = 30.0
UI_KIND_TIMEOUT = 2.0


@dataclass(frozen=True)
class PrinterView:
    key: str
    name: str
    host: str
    port: int
    mainsail_url: str
    mainsail_port: int
    ui_kind: str
    online: bool
    state: str
    file: str
    progress: float
    layer: int
    layer_total: int
    eta_seconds: int | None
    finish_at: int | None
    nozzle: float
    bed: float
    filament: str
    thumb_version: str
    error: ErrorInfo | None
    last_seen: float
    parts: list = field(default_factory=list)


@dataclass(frozen=True)
class FarmSummary:
    printing: int
    paused: int
    idle: int
    error: int
    offline: int
    nearest_finish: int | None


def _as_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _finish_fields(state, print_stats, cur_print_data, metadata, progress, now):
    if state != "printing":
        return None, None
    end_time = _as_number(cur_print_data.get("end_time"))
    if end_time is not None and end_time > now:
        return int(end_time - now), int(end_time)
    duration = _as_number(print_stats.get("print_duration"))
    estimated = _as_number(metadata.get("estimated_time"))
    if estimated is not None and estimated > 0 and duration is not None and duration >= 0:
        eta = max(0, int(estimated - duration))
        return eta, int(now + eta)
    if duration is not None and duration > 0 and 0 < progress < 1:
        eta = int(duration * (1 - progress) / progress)
        return eta, int(now + eta)
    return None, None


def build_view(printer: Printer, *, info, status, now, error, thumb_version, ui_kind="", parts=None) -> PrinterView:
    online = info is not None
    print_stats = (status or {}).get("print_stats") or {}
    if not online:
        state = "offline"
    elif info.get("state") in ("error", "shutdown") or status is None:
        state = "error"
    else:
        state = PRINT_STATE_MAP.get(print_stats.get("state"), "idle")

    vdisk = (status or {}).get("virtual_sdcard") or {}
    cur_print_data = vdisk.get("cur_print_data") or {}
    metadata = cur_print_data.get("metadata") or {}
    progress = min(max(_as_float(vdisk.get("progress")), 0.0), 1.0)
    eta_seconds, finish_at = _finish_fields(state, print_stats, cur_print_data, metadata, progress, now)

    return PrinterView(
        key=printer.key,
        name=printer.name,
        host=printer.host,
        port=printer.port,
        mainsail_url=printer.mainsail_url,
        mainsail_port=printer.mainsail_port,
        ui_kind=ui_kind,
        online=online,
        state=state,
        file=print_stats.get("filename") or "",
        progress=progress,
        layer=_as_int(vdisk.get("layer")),
        layer_total=_as_int(vdisk.get("layer_count")),
        eta_seconds=eta_seconds,
        finish_at=finish_at,
        nozzle=_as_float(((status or {}).get("extruder") or {}).get("temperature")),
        bed=_as_float(((status or {}).get("heater_bed") or {}).get("temperature")),
        filament=metadata.get("filament_type") or "",
        thumb_version=thumb_version,
        error=error,
        last_seen=now if online else 0,
        parts=list(parts or []),
    )


def summarize(views) -> FarmSummary:
    counts = {"printing": 0, "paused": 0, "idle": 0, "error": 0, "offline": 0}
    etas = []
    for view in views:
        counts[view.state] = counts.get(view.state, 0) + 1
        if view.state == "printing" and view.eta_seconds is not None:
            etas.append(view.eta_seconds)
    return FarmSummary(
        printing=counts["printing"],
        paused=counts["paused"],
        idle=counts["idle"],
        error=counts["error"],
        offline=counts["offline"],
        nearest_finish=min(etas) if etas else None,
    )


class FarmMonitor:
    def __init__(
        self, config: AppConfig, *, thumb_cache: ThumbCache, interval=None, clock=time.time, ui_detect=None
    ) -> None:
        self._thumb_cache = thumb_cache
        self._interval = float(interval if interval is not None else config.refresh_seconds)
        self._clock = clock
        self._detect_ui = ui_detect if ui_detect is not None else detect_ui
        self._printers = list(config.printers)
        self._views: dict[str, PrinterView] = {}
        self._next_due: dict[str, float] = {printer.key: 0.0 for printer in self._printers}
        self._failures: dict[str, int] = {}
        self._last_error: dict[str, ErrorInfo] = {}
        self._ui_kind: dict[str, str] = {}
        self._ui_checked: dict[str, float] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._pool = None
        self._thumb_pool = None
        self._thumb_futures = []
        self._thumb_pending = set()
        self._parts: dict[tuple[str, str], list] = {}
        self._local = threading.local()
        self._sessions = []
        self._updated_at = 0.0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="farm-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(JOIN_TIMEOUT)
            if thread.is_alive():
                return
            self._thread = None
        pool = self._pool
        self._pool = None
        thumb_pool = self._thumb_pool
        self._thumb_pool = None
        if pool is not None:
            pool.shutdown(wait=False)
        if thumb_pool is not None:
            thumb_pool.shutdown(wait=False)
        with self._lock:
            sessions = self._sessions
            self._sessions = []
        for session in sessions:
            try:
                session.close()
            except Exception:
                pass

    def set_printers(self, printers) -> None:
        printers = list(printers)
        keys = {printer.key for printer in printers}
        with self._lock:
            self._printers = printers
            for printer in printers:
                self._next_due[printer.key] = 0.0
            for mapping in (self._views, self._next_due, self._failures, self._last_error, self._ui_kind, self._ui_checked):
                for key in list(mapping):
                    if key not in keys:
                        del mapping[key]

    def refresh(self) -> None:
        with self._lock:
            for printer in self._printers:
                self._next_due[printer.key] = 0.0
        if not self.running:
            self.poll_once()

    def poll_once(self) -> None:
        now = self._clock()
        with self._lock:
            due = [printer for printer in self._printers if self._next_due.get(printer.key, 0.0) <= now]
            self._thumb_futures = [future for future in self._thumb_futures if not future.done()]
        if not due:
            return
        pool = self._ensure_pool()
        futures = [(printer, pool.submit(self._fetch, printer)) for printer in due]
        for printer, future in futures:
            try:
                result = future.result()
            except Exception:
                LOGGER.exception("Опрос принтера %s завершился ошибкой", printer.name)
                result = (None, None, "", None, [], "")
            filename = ""
            try:
                with self._lock:
                    filename = self._apply(printer, result)
            except Exception:
                LOGGER.exception("Не удалось применить результат опроса принтера %s", printer.name)
            if filename:
                self._submit_thumb(printer, filename)
        with self._lock:
            self._updated_at = self._clock()

    def snapshot(self) -> dict:
        with self._lock:
            views = []
            for printer in self._printers:
                view = self._views.get(printer.key)
                if view is None:
                    view = build_view(printer, info=None, status=None, now=0, error=None, thumb_version="")
                views.append(view)
            updated_at = self._updated_at
        return {
            "printers": [asdict(view) for view in views],
            "summary": asdict(summarize(views)),
            "updated_at": updated_at,
        }

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(LOOP_STEP)
            if self._stop.is_set():
                break
            try:
                self.poll_once()
            except Exception:
                LOGGER.exception("Сбой в цикле опроса принтеров")

    def _ensure_pool(self):
        with self._lock:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
            return self._pool

    def _ensure_thumb_pool(self):
        with self._lock:
            if self._thumb_pool is None:
                self._thumb_pool = ThreadPoolExecutor(max_workers=THUMB_WORKERS)
            return self._thumb_pool

    def _session(self):
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            self._local.session = session
            with self._lock:
                self._sessions.append(session)
        return session

    def _fetch(self, printer):
        session = self._session()
        try:
            info = self._fetch_info(session, printer)
        except Exception as exc:
            return None, None, str(exc), None, [], ""
        ui_kind = self._ui_kind_for(printer)
        try:
            status = self._fetch_objects(session, printer)
        except Exception as exc:
            state_name = str(info.get("state") or "")
            error_info = None
            if state_name in ("error", "shutdown"):
                error_info = self._collect_error(session, printer, state_name, str(info.get("state_message") or ""))
            return info, None, str(exc), error_info, [], ui_kind
        print_stats = status.get("print_stats") or {}
        state_name = str(info.get("state") or "")
        error_info = None
        if state_name in ("error", "shutdown") or print_stats.get("state") == "error":
            parse_state = state_name if state_name in ("error", "shutdown") else "error"
            error_info = self._collect_error(session, printer, parse_state, str(info.get("state_message") or ""))
        filename = str(print_stats.get("filename") or "")
        parts = self._parts_for(session, printer, filename) if filename else []
        if print_stats.get("state") not in ("printing", "paused"):
            parts = []
        return info, status, "", error_info, parts, ui_kind

    def _parts_for(self, session, printer, filename) -> list:
        cache_key = (printer.key, filename)
        with self._lock:
            cached = self._parts.get(cache_key)
        if cached is not None and self._thumb_cache.has(cache_key):
            return cached
        try:
            data, ok = self._fetch_head(session, printer, filename)
        except Exception:
            return []
        if not ok:
            with self._lock:
                return self._parts.get(cache_key) or []
        thumb = extract_thumbnail(data)
        if thumb is not None:
            self._thumb_cache.put(cache_key, thumb)
        else:
            self._thumb_cache.put_negative(cache_key)
        parts = [{"name": part.name, "count": part.count} for part in parse_parts_bytes(data)]
        with self._lock:
            self._parts[cache_key] = parts
        return parts

    def _fetch_head(self, session, printer, filename):
        response = session.get(
            printer.base_url + GCODE_PATH + quote(filename),
            headers={"Range": GCODE_RANGE},
            timeout=GCODE_TIMEOUT,
            stream=True,
        )
        try:
            if response.status_code not in (200, 206):
                return b"", False
            return response.raw.read(MAX_THUMB_BYTES), True
        finally:
            response.close()

    def _ui_kind_for(self, printer) -> str:
        now = self._clock()
        with self._lock:
            checked = self._ui_checked.get(printer.key)
            if checked is not None and now - checked <= UI_KIND_TTL:
                return self._ui_kind.get(printer.key, "unknown")
        kind = "unknown"
        try:
            detected = self._detect_ui(printer.host, printer.mainsail_port, timeout=UI_KIND_TIMEOUT)
            if detected in ("fluidd", "mainsail"):
                kind = detected
        except Exception:
            kind = "unknown"
        with self._lock:
            self._ui_kind[printer.key] = kind
            self._ui_checked[printer.key] = now
        return kind

    def _collect_error(self, session, printer, state_name, state_message):
        try:
            log_tail = self._fetch_log_tail(session, printer)
            return parse_error(state_message, state=state_name, log_tail=log_tail)
        except Exception:
            return parse_error(state_message, state=state_name)

    def _fetch_info(self, session, printer):
        payload = self._get_json(session, printer.base_url + INFO_PATH, INFO_TIMEOUT)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ValueError("ожидался объект result")
        return result

    def _fetch_objects(self, session, printer):
        payload = self._get_json(session, printer.base_url + OBJECTS_PATH, OBJECTS_TIMEOUT)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ValueError("ожидался объект result")
        status = result.get("status")
        if not isinstance(status, dict):
            raise ValueError("ожидался объект status")
        return status

    def _fetch_log_tail(self, session, printer):
        response = session.get(
            printer.base_url + LOG_PATH,
            headers={"Range": LOG_RANGE},
            timeout=LOG_TIMEOUT,
            stream=True,
        )
        try:
            response.raise_for_status()
            return response.raw.read(MAX_LOG_BYTES).decode("utf-8", "replace")
        finally:
            response.close()

    def _get_json(self, session, url, timeout):
        response = session.get(url, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("ожидался JSON-объект")
        return payload

    def _submit_thumb(self, printer, filename) -> None:
        cache_key = (printer.key, filename)
        with self._lock:
            if cache_key in self._thumb_pending or self._thumb_cache.has(cache_key):
                return
            self._thumb_pending.add(cache_key)
        try:
            future = self._ensure_thumb_pool().submit(self._load_thumb, printer, filename)
        except Exception:
            with self._lock:
                self._thumb_pending.discard(cache_key)
            return
        with self._lock:
            self._thumb_futures.append(future)

    def _load_thumb(self, printer, filename) -> None:
        cache_key = (printer.key, filename)
        try:
            session = self._session()
            data, received = load_thumbnail(session, printer.base_url, filename)
            if data:
                self._thumb_cache.put(cache_key, data)
            elif received:
                self._thumb_cache.put_negative(cache_key)
            version = self._thumb_cache.version(cache_key)
            with self._lock:
                view = self._views.get(printer.key)
                if view is not None and view.file == filename and view.thumb_version != version:
                    self._views[printer.key] = replace(view, thumb_version=version)
        finally:
            with self._lock:
                self._thumb_pending.discard(cache_key)

    def _apply(self, printer, result) -> str:
        info, status, reason, error_info, parts, ui_kind = result
        key = printer.key
        if key not in {item.key for item in self._printers}:
            return ""
        now = self._clock()
        previous = self._views.get(key)
        if info is None:
            if reason:
                LOGGER.warning("Принтер %s недоступен: %s", printer.name, reason)
            failures = self._failures.get(key, 0) + 1
            self._failures[key] = failures
            self._next_due[key] = now + min(BACKOFF_CAP, BACKOFF_BASE * 2 ** (failures - 1))
            view = build_view(printer, info=None, status=None, now=now, error=self._last_error.get(key), thumb_version="")
            if previous is not None:
                view = replace(view, last_seen=previous.last_seen, thumb_version=previous.thumb_version, ui_kind=previous.ui_kind, parts=previous.parts)
            self._views[key] = view
            return ""
        self._failures[key] = 0
        self._next_due[key] = now + self._interval
        if status is None:
            if reason:
                LOGGER.warning("Принтер %s ответил без статуса: %s", printer.name, reason)
            error = error_info if error_info is not None else self._last_error.get(key)
            if error is None:
                error = ErrorInfo(
                    code="",
                    label="Klipper не отвечает",
                    hint="Проверьте, запущен ли Klipper, и откройте Mainsail.",
                    detail=reason,
                )
            if previous is not None:
                view = replace(previous, state="error", online=True, error=error, last_seen=now, ui_kind=ui_kind or previous.ui_kind)
            else:
                view = build_view(printer, info=info, status=None, now=now, error=error, thumb_version="", ui_kind=ui_kind)
            self._last_error[key] = error
            self._views[key] = view
            return ""
        filename = str((status.get("print_stats") or {}).get("filename") or "")
        thumb_version = self._thumb_cache.version((key, filename)) if filename else ""
        error = error_info if error_info is not None else self._last_error.get(key)
        view = build_view(printer, info=info, status=status, now=now, error=error, thumb_version=thumb_version, ui_kind=ui_kind, parts=parts)
        if view.state in ("printing", "idle"):
            self._last_error.pop(key, None)
            if error is not None:
                view = replace(view, error=None)
        elif error is not None:
            self._last_error[key] = error
        self._views[key] = view
        return filename
