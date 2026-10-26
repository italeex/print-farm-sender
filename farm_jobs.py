from __future__ import annotations

import copy
import logging
import threading
from pathlib import Path
from typing import Callable, Sequence

from farm_core import (
    BatchSender,
    FileProgressEvent,
    FileResultEvent,
    FinishedEvent,
    OverallEvent,
    Printer,
    PrinterStatusEvent,
    validate_files,
)

LOGGER = logging.getLogger(__name__)


class JobError(Exception):
    pass


def _initial_state(printers: Sequence[Printer], files: Sequence[Path]) -> dict:
    return {
        "running": False,
        "printers_total": len(printers),
        "printers_done": 0,
        "files_total": len(files) * len(printers),
        "files_processed": 0,
        "aggregate_speed": 0.0,
        "per_printer": {
            printer.key: {
                "status": "ожидание",
                "file": "",
                "progress": 0.0,
                "speed": 0.0,
                "last_error": "",
            }
            for printer in printers
        },
        "finished": False,
        "ok": 0,
        "failed": 0,
        "cancelled": False,
        "cancelling": False,
    }


class JobManager:
    def __init__(self, on_event: Callable[[object], None] | None = None) -> None:
        self.on_event = on_event
        self._lock = threading.Lock()
        self._sender: BatchSender | None = None
        self._cancel_event = threading.Event()
        self._generation = 0
        self._state = _initial_state([], [])

    @property
    def running(self) -> bool:
        with self._lock:
            return self._state["running"]

    def start(self, printers: Sequence[Printer], files: Sequence[Path]) -> None:
        printers = list(printers)
        files = [Path(file_path) for file_path in files]
        error = validate_files(files)
        if error:
            raise JobError(error)
        if not printers:
            raise JobError("Не выбраны принтеры.")
        with self._lock:
            if self._state["running"]:
                raise JobError("Отправка уже идёт.")
            cancel_event = threading.Event()
            self._generation += 1
            generation = self._generation
            sender = BatchSender(
                printers,
                files,
                lambda event: self._on_event(event, generation),
                cancel_event,
            )
            self._sender = sender
            self._cancel_event = cancel_event
            self._state = _initial_state(printers, files)
            self._state["running"] = True
        try:
            sender.start()
        except Exception:
            with self._lock:
                self._state["running"] = False
                self._sender = None
            raise

    def cancel(self) -> None:
        with self._lock:
            if not self._state["running"]:
                return
            self._state["cancelling"] = True
            sender = self._sender
        if sender is not None:
            sender.cancel()

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._state)

    def _on_event(self, event: object, generation: int | None = None) -> None:
        with self._lock:
            if generation is not None and generation != self._generation:
                return
            if isinstance(event, PrinterStatusEvent):
                row = self._state["per_printer"].get(event.printer_key)
                if row is not None:
                    row["status"] = event.text
                    if event.text == "готово":
                        row["progress"] = 100.0
                        row["speed"] = 0.0
                    elif event.text == "отменено" or event.text.startswith("ошибка"):
                        row["progress"] = 0.0
                        row["speed"] = 0.0
            elif isinstance(event, FileProgressEvent):
                row = self._state["per_printer"].get(event.printer_key)
                if row is not None:
                    percent = (
                        100.0 * event.bytes_done / event.bytes_total
                        if event.bytes_total
                        else 100.0
                    )
                    row["file"] = event.file_name
                    row["progress"] = round(percent, 1)
                    row["speed"] = event.speed_bps
            elif isinstance(event, FileResultEvent):
                row = self._state["per_printer"].get(event.printer_key)
                if row is not None and not event.ok:
                    row["last_error"] = event.message
            elif isinstance(event, OverallEvent):
                self._state["printers_done"] = event.printers_done
                self._state["printers_total"] = event.printers_total
                self._state["files_processed"] = event.files_processed
                self._state["files_total"] = event.files_total
                self._state["aggregate_speed"] = event.aggregate_speed
            elif isinstance(event, FinishedEvent):
                self._state["running"] = False
                self._state["finished"] = True
                self._state["ok"] = event.ok
                self._state["failed"] = event.failed
                self._state["cancelled"] = event.cancelled or self._state["cancelling"]
                self._state["aggregate_speed"] = 0.0
                self._state["printers_done"] = self._state["printers_total"]
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:
                LOGGER.exception("Ошибка обработчика события задачи")
