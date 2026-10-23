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
        self._closing = False
        self.online: dict[str, bool] = {}
        self._build_ui()
        self._render_printers()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._drain_events)
        self._probe_all()

    def _load_config(self) -> AppConfig:
        try:
            return load_config(farm_core.CONFIG_PATH)
        except (ConfigError, OSError) as exc:
            self._preserve_broken_config()
            messagebox.showwarning(
                "Конфигурация", f"{exc}\n\nЗапуск с пустым списком."
            )
            return AppConfig()

    def _preserve_broken_config(self) -> None:
        path = Path(farm_core.CONFIG_PATH)
        try:
            path.replace(path.with_name(path.name + ".broken"))
        except OSError:
            pass

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
        for printer in printers:
            row = self.rows.get(printer.key)
            if row is not None:
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
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        try:
            while True:
                try:
                    event = self.event_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle_event(event)
                except Exception as exc:
                    self._log(f"Ошибка обработки события: {exc}")
        finally:
            try:
                if self.winfo_exists():
                    self.after(100, self._drain_events)
            except tk.TclError:
                pass

    def _handle_event(self, event):
        if isinstance(event, tuple) and event and event[0] == "probe":
            self._apply_probe(event[1])
        elif isinstance(event, PrinterStatusEvent):
            row = self.rows.get(event.printer_key)
            if row is not None:
                row.set_status(event.text)
                terminal = (
                    event.text == "готово"
                    or event.text == "отменено"
                    or event.text.startswith("ошибка")
                )
                if terminal:
                    row.bar.configure(value=100 if event.text == "готово" else 0)
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
        if not self._closing:
            self.send_button.configure(state="normal")
            self.cancel_button.configure(state="disabled")
        prefix = "Отменено" if event.cancelled else "Готово"
        text = f"{prefix}. Успешно: {event.ok}, ошибок: {event.failed}"
        self._log(text)
        self.sender = None
        self.cancel_event = None
        if not self._closing:
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
        if self._closing:
            return
        if self.sender is not None:
            if not messagebox.askyesno("Выход", "Идёт отправка. Отменить и выйти?"):
                return
            self.sender.cancel()
            self.sender.join(2.0)
            if self.sender is not None and self.sender.running:
                self._closing = True
                self._log("Останавливаем...")
                self.send_button.configure(state="disabled")
                self.cancel_button.configure(state="disabled")
                self.after(200, self._poll_close)
                return
        self._finish_close()

    def _poll_close(self):
        if self.sender is not None and self.sender.running:
            self.after(200, self._poll_close)
            return
        self._finish_close()

    def _finish_close(self):
        self._closing = True
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
