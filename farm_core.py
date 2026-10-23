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
