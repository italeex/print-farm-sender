from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import requests

FARM_DIR = "FARM"
FARM_PREFIX = "FARM_"
TIMEOUT = 10
UPLOAD_TIMEOUT = 600
NOZZLE_SCAN_BYTES = 2 * 1024 * 1024

NAME_PATTERN = re.compile(r"^N\d+\.gcode$")
NOZZLE_PATTERN = re.compile(r"nozzle[_ ]?diam(?:eter)?\s*[:=]\s*(\d+\.\d+)", re.IGNORECASE)


class FarmFileError(Exception):
    def __init__(self, message: str, status=None) -> None:
        super().__init__(message)
        self.status = status


def remote_name(number: int) -> str:
    return f"N{int(number)}.gcode"


def format_nozzle(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise FarmFileError(f"Некорректное сопло: {value}")
    if not 0.1 <= number <= 1.2:
        raise FarmFileError(f"Некорректное сопло: {value}")
    return f"{number:.1f}"


def physical_name(name: str, mode: str) -> str:
    if mode == "folder":
        return name
    if mode == "prefix":
        return FARM_PREFIX + name
    raise FarmFileError(f"Неизвестный режим: {mode}")


def detect_nozzle(path) -> float | None:
    name = Path(path).name
    match = re.search(r"-(\d+\.\d+)\.gcode$", name, re.IGNORECASE)
    if match is not None:
        try:
            return float(match.group(1))
        except (TypeError, ValueError):
            pass
    try:
        with open(path, "rb") as handle:
            chunk = handle.read(NOZZLE_SCAN_BYTES)
    except OSError:
        return None
    try:
        text = chunk.decode("utf-8", errors="ignore")
    except Exception:
        return None
    match = NOZZLE_PATTERN.search(text)
    if match is None:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def _check_name(name: str) -> None:
    if "/" in name or "\\" in name or ".." in name or not NAME_PATTERN.match(name):
        raise FarmFileError(f"Некорректное имя файла: {name}")


def _check_path(path: str) -> str:
    text = str(path or "").replace("\\", "/").strip()
    if not text.startswith(FARM_DIR + "/") or ".." in text.split("/"):
        raise FarmFileError(f"Некорректный путь файла: {path}")
    base = text.split("/")[-1]
    _check_name(base)
    return text


@contextmanager
def _client(session):
    if session is not None:
        yield session
    else:
        with requests.Session() as fresh:
            fresh.trust_env = False
            yield fresh


def _get(client, url: str):
    try:
        return client.get(url, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise FarmFileError(f"Не удалось получить список файлов: {exc}") from exc


def _directory_entries(client, printer, rel: str) -> tuple[list[dict], list[str]] | None:
    response = _get(client, f"{printer.base_url}/server/files/directory?path=gcodes/{quote(rel, safe='/')}")
    if response.status_code in (400, 404):
        return None
    if response.status_code >= 400:
        raise FarmFileError(
            f"Не удалось получить список файлов: HTTP {response.status_code}",
            status=response.status_code,
        )
    try:
        result = response.json()["result"]
        files = result.get("files", [])
        dirs = result.get("dirs", [])
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise FarmFileError(f"Некорректный ответ списка файлов: {exc}") from exc
    return files, [str(d.get("dirname") or d.get("name") or d.get("path") or "") for d in dirs if isinstance(d, dict)]


def list_farm(printer, mode: str, session=None) -> list[dict]:
    if mode not in ("folder", "prefix"):
        raise FarmFileError(f"Неизвестный режим: {mode}")
    with _client(session) as client:
        if mode == "folder":
            return _list_folder_recursive(client, printer)
        response = _get(client, f"{printer.base_url}/server/files/list?root=gcodes")
        if response.status_code >= 400:
            raise FarmFileError(
                f"Не удалось получить список файлов: HTTP {response.status_code}",
                status=response.status_code,
            )
        try:
            files = response.json()["result"]
        except (ValueError, KeyError, TypeError) as exc:
            raise FarmFileError(f"Некорректный ответ списка файлов: {exc}") from exc
        items = []
        for entry in files:
            if not isinstance(entry, dict):
                continue
            path = str(entry.get("path", ""))
            if not path.startswith(FARM_PREFIX) or "/" in path:
                continue
            items.append({"name": path[len(FARM_PREFIX):], "size": entry.get("size", 0), "modified": entry.get("modified", 0.0)})
        return items


def _list_folder_recursive(client, printer) -> list[dict]:
    first = _directory_entries(client, printer, FARM_DIR)
    if first is None:
        return []
    items: list[dict] = []
    queue = [FARM_DIR]
    seen = {FARM_DIR}
    pending = {FARM_DIR: first}
    while queue:
        rel = queue.pop(0)
        pair = pending.pop(rel, None)
        if pair is None:
            pair = _directory_entries(client, printer, rel)
            if pair is None:
                continue
        files, dirs = pair
        for entry in files:
            if not isinstance(entry, dict) or "filename" not in entry:
                continue
            filename = str(entry["filename"])
            if "/" in filename or "\\" in filename or not filename:
                continue
            if rel == FARM_DIR:
                relpath = f"{FARM_DIR}/{filename}"
            else:
                relpath = f"{rel}/{filename}"
            items.append(
                {
                    "name": filename,
                    "path": relpath,
                    "size": entry.get("size", 0),
                    "modified": entry.get("modified", 0.0),
                }
            )
        for dirname in dirs:
            if not dirname or "/" in dirname or "\\" in dirname or dirname in (".", ".."):
                continue
            child = f"{rel}/{dirname}"
            if child in seen:
                continue
            seen.add(child)
            queue.append(child)
    return items


def _post_upload(client, printer, file_path: Path, remote: str, folder: str | None):
    try:
        handle = open(file_path, "rb")
    except OSError as exc:
        raise FarmFileError(f"Файл недоступен: {exc}") from exc
    with handle:
        data = {"root": "gcodes"}
        if folder is not None:
            data["path"] = folder
        try:
            return client.post(
                f"{printer.base_url}/server/files/upload",
                data=data,
                files={"file": (remote, handle)},
                timeout=UPLOAD_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise FarmFileError(f"Не удалось загрузить файл {remote}: {exc}") from exc


def upload_to_farm(printer, file_path, name: str, mode: str, nozzle=None, session=None) -> str:
    if mode not in ("folder", "prefix"):
        raise FarmFileError(f"Неизвестный режим: {mode}")
    _check_name(name)
    with _client(session) as client:
        if mode == "prefix":
            response = _post_upload(client, printer, Path(file_path), physical_name(name, mode), None)
            if response.status_code == 201:
                return "prefix"
            raise FarmFileError(
                f"Не удалось загрузить файл {name}: HTTP {response.status_code}",
                status=response.status_code,
            )
        folder = f"{FARM_DIR}/{format_nozzle(nozzle if nozzle is not None else 0.4)}"
        response = _post_upload(client, printer, Path(file_path), name, folder)
        if response.status_code == 201:
            return "folder"
        raise FarmFileError(
            f"Не удалось загрузить файл {name}: HTTP {response.status_code}",
            status=response.status_code,
        )
    raise FarmFileError(f"Не удалось загрузить файл {name}: папка недоступна")


def delete_from_farm(printer, names, mode: str, session=None) -> list[str]:
    if mode not in ("folder", "prefix"):
        raise FarmFileError(f"Неизвестный режим: {mode}")
    if isinstance(names, str):
        clean = [names]
    else:
        clean = list(names)
    targets: list[str] = []
    for item in clean:
        text = str(item or "")
        if "/" in text or "\\" in text:
            targets.append(_check_path(text))
        elif mode == "prefix" and text.startswith(FARM_PREFIX):
            _check_name(text[len(FARM_PREFIX):])
            targets.append(text)
        else:
            _check_name(text)
            if mode == "folder":
                targets.append(f"{FARM_DIR}/{text}")
            else:
                targets.append(physical_name(text, mode))
    for target in targets:
        if mode == "folder" and not target.startswith(FARM_DIR + "/"):
            raise FarmFileError(f"Некорректный путь файла: {target}")
        if mode == "prefix" and not target.startswith(FARM_PREFIX):
            raise FarmFileError(f"Некорректный путь файла: {target}")
    deleted = []
    with _client(session) as client:
        for target in targets:
            try:
                response = client.delete(
                    f"{printer.base_url}/server/files/gcodes/{quote(target)}",
                    timeout=TIMEOUT,
                )
            except requests.RequestException as exc:
                raise FarmFileError(f"Не удалось удалить файл {target}: {exc}") from exc
            if response.status_code == 404:
                continue
            if response.status_code >= 400:
                raise FarmFileError(
                    f"Не удалось удалить файл {target}: HTTP {response.status_code}",
                    status=response.status_code,
                )
            if mode == "prefix" and target.startswith(FARM_PREFIX):
                deleted.append(target[len(FARM_PREFIX):])
            else:
                deleted.append(target.split("/")[-1])
    return deleted
