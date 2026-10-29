from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import requests

FARM_DIR = "FARM"
FARM_PREFIX = "FARM_"
TIMEOUT = 10

NAME_PATTERN = re.compile(r"^N\d+\.gcode$")


class FarmFileError(Exception):
    def __init__(self, message: str, status=None) -> None:
        super().__init__(message)
        self.status = status


def remote_name(number: int) -> str:
    return f"N{int(number)}.gcode"


def physical_name(name: str, mode: str) -> str:
    if mode == "folder":
        return name
    if mode == "prefix":
        return FARM_PREFIX + name
    raise FarmFileError(f"Неизвестный режим: {mode}")


def _check_name(name: str) -> None:
    if "/" in name or "\\" in name or ".." in name or not NAME_PATTERN.match(name):
        raise FarmFileError(f"Некорректное имя файла: {name}")


@contextmanager
def _client(session):
    if session is not None:
        yield session
    else:
        with requests.Session() as fresh:
            yield fresh


def _get(client, url: str):
    try:
        return client.get(url, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise FarmFileError(f"Не удалось получить список файлов: {exc}") from exc


def list_farm(printer, mode: str, session=None) -> list[dict]:
    if mode not in ("folder", "prefix"):
        raise FarmFileError(f"Неизвестный режим: {mode}")
    with _client(session) as client:
        if mode == "folder":
            response = _get(client, f"{printer.base_url}/server/files/directory?path=gcodes/{FARM_DIR}")
            if response.status_code in (400, 404):
                return []
            if response.status_code >= 400:
                raise FarmFileError(
                    f"Не удалось получить список файлов: HTTP {response.status_code}",
                    status=response.status_code,
                )
            try:
                files = response.json()["result"]["files"]
            except (ValueError, KeyError, TypeError) as exc:
                raise FarmFileError(f"Некорректный ответ списка файлов: {exc}") from exc
            return [
                {"name": entry["filename"], "size": entry.get("size", 0), "modified": entry.get("modified", 0.0)}
                for entry in files
                if isinstance(entry, dict) and "filename" in entry
            ]
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


def _post_upload(client, printer, file_path: Path, remote: str, folder: bool):
    try:
        handle = open(file_path, "rb")
    except OSError as exc:
        raise FarmFileError(f"Файл недоступен: {exc}") from exc
    with handle:
        data = {"root": "gcodes"}
        if folder:
            data["path"] = FARM_DIR
        try:
            return client.post(
                f"{printer.base_url}/server/files/upload",
                data=data,
                files={"file": (remote, handle)},
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            raise FarmFileError(f"Не удалось загрузить файл {remote}: {exc}") from exc


def upload_to_farm(printer, file_path, name: str, mode: str, session=None) -> str:
    if mode not in ("folder", "prefix"):
        raise FarmFileError(f"Неизвестный режим: {mode}")
    _check_name(name)
    tried = [mode] if mode == "prefix" else ["folder", "prefix"]
    with _client(session) as client:
        for attempt in tried:
            response = _post_upload(client, printer, Path(file_path), physical_name(name, attempt), attempt == "folder")
            if response.status_code == 201:
                return attempt
            if attempt == "folder" and response.status_code in (400, 404):
                continue
            raise FarmFileError(
                f"Не удалось загрузить файл {name}: HTTP {response.status_code}",
                status=response.status_code,
            )
    raise FarmFileError(f"Не удалось загрузить файл {name}: папка недоступна")


def delete_from_farm(printer, names, mode: str, session=None) -> list[str]:
    if mode not in ("folder", "prefix"):
        raise FarmFileError(f"Неизвестный режим: {mode}")
    clean = list(names)
    for name in clean:
        _check_name(name)
    deleted = []
    with _client(session) as client:
        for name in clean:
            remote = f"{FARM_DIR}/{name}" if mode == "folder" else physical_name(name, mode)
            try:
                response = client.delete(
                    f"{printer.base_url}/server/files/gcodes/{quote(remote)}",
                    timeout=TIMEOUT,
                )
            except requests.RequestException as exc:
                raise FarmFileError(f"Не удалось удалить файл {name}: {exc}") from exc
            if response.status_code == 404:
                continue
            if response.status_code >= 400:
                raise FarmFileError(
                    f"Не удалось удалить файл {name}: HTTP {response.status_code}",
                    status=response.status_code,
                )
            deleted.append(name)
    return deleted
