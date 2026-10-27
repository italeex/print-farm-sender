import json
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
CELL = re.compile(r"([A-Za-z]+)(\d+)")
PREFIX = re.compile(r"^[^\W\d_]{2,}\.")
SPLIT = re.compile(r"^([^\s_]+)[\s_]+(.*)$")
EXCEL_EPOCH = date(1899, 12, 30)
PLAN_SUFFIXES = (".xlsx", ".json")


def part_key(name) -> str:
    text = str(name or "").strip()
    if not text:
        return ""
    token = re.split(r"[\s_]", text, maxsplit=1)[0]
    return PREFIX.sub("", token).upper()


def split_code_name(name) -> tuple[str, str]:
    text = " ".join(str(name or "").split())
    match = SPLIT.match(text)
    if match is None:
        return text, text
    return match.group(1), match.group(2).strip() or match.group(1)


def parse_plan_file(path) -> dict:
    target = Path(path)
    if not target.is_file():
        raise ValueError(f"Файл не найден: {target}")
    suffix = target.suffix.lower()
    if suffix == ".xlsx":
        return _parse_xlsx(target)
    if suffix == ".json":
        return _parse_json(target)
    raise ValueError("Поддерживаются файлы .xlsx и .json")


def _parse_xlsx(path: Path) -> dict:
    rows = _xlsx_rows(path)
    dates = _dates_from_row(rows.get(2, {}))
    if not dates:
        raise ValueError("В файле плана не найдены даты")
    days = [dates[column] for column in sorted(dates)]
    items = []
    name = ""
    for number in sorted(rows):
        if number < 3:
            continue
        values = rows[number]
        text = _text(values.get(0))
        if text:
            name = text
        if _text(values.get(1)).lower() != "план" or not name:
            continue
        code, title = split_code_name(name)
        key = part_key(name)
        if not key:
            continue
        for column, day in dates.items():
            target = _quantity(values.get(column))
            if target <= 0:
                continue
            items.append({"date": day, "code": code, "name": title, "key": key, "target": target})
    return _finalize(items, week_start=days[0], days=days)


def _parse_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Не удалось прочитать JSON-план") from exc
    if not isinstance(payload, dict):
        raise ValueError("Ожидался JSON-объект плана")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("В плане нет списка items")
    items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        day = _day(raw.get("date"))
        if not day:
            continue
        code = str(raw.get("code") or raw.get("name") or "").strip()
        name = str(raw.get("name") or code).strip()
        key = str(raw.get("key") or "").strip() or part_key(code or name)
        target = _quantity(raw.get("target"))
        if not key or target <= 0:
            continue
        items.append({"date": day, "code": code, "name": name, "key": key, "target": target})
    week_start = _day(payload.get("week_start")) or (items[0]["date"] if items else "")
    days = sorted({item["date"] for item in items} | ({week_start} if week_start else set()))
    return _finalize(items, week_start=week_start, days=days)


def _finalize(items, *, week_start: str, days: list[str]) -> dict:
    merged: dict[tuple[str, str], dict] = {}
    for item in items:
        marker = (item["date"], item["key"])
        known = merged.get(marker)
        if known is not None:
            known["target"] += item["target"]
            continue
        merged[marker] = dict(item)
    ordered = sorted(merged.values(), key=lambda item: (item["date"], item["key"]))
    return {"week_start": week_start, "days": list(days), "items": ordered}


def _xlsx_rows(path: Path) -> dict[int, dict[int, object]]:
    try:
        with zipfile.ZipFile(path) as archive:
            shared = _shared_strings(archive)
            name = _sheet_name(archive)
            raw = archive.read(name)
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise ValueError("Не удалось прочитать файл Excel") from exc
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError("Повреждённый файл Excel") from exc
    rows: dict[int, dict[int, object]] = {}
    for row in root.iter(f"{NS}row"):
        try:
            number = int(row.get("r") or 0)
        except ValueError:
            continue
        values: dict[int, object] = {}
        for cell in row.findall(f"{NS}c"):
            column = _column(cell.get("r") or "")
            if column < 0:
                continue
            value = _cell_value(cell, shared)
            if value is None:
                continue
            values[column] = value
        rows[number] = values
    return rows


def _sheet_name(archive: zipfile.ZipFile) -> str:
    preferred = "xl/worksheets/sheet1.xml"
    if preferred in archive.namelist():
        return preferred
    for name in archive.namelist():
        if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"):
            return name
    raise KeyError(preferred)


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        raw = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    return ["".join(node.text or "" for node in item.iter(f"{NS}t")) for item in root.findall(f"{NS}si")]


def _cell_value(cell, shared: list[str]):
    kind = cell.get("t") or ""
    if kind == "inlineStr":
        node = cell.find(f"{NS}is")
        if node is None:
            return None
        return "".join(text.text or "" for text in node.iter(f"{NS}t"))
    node = cell.find(f"{NS}v")
    if node is None or node.text is None:
        return None
    text = node.text
    if kind == "s":
        try:
            return shared[int(text)]
        except (ValueError, IndexError):
            return None
    if kind in ("str", "e"):
        return text
    return _number(text)


def _number(text: str):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    if value.is_integer():
        return int(value)
    return value


def _column(reference: str) -> int:
    match = CELL.match(reference.strip())
    if match is None:
        return -1
    index = 0
    for char in match.group(1).upper():
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _dates_from_row(row: dict[int, object]) -> dict[int, str]:
    dates: dict[int, str] = {}
    for column, value in row.items():
        if column < 3 or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        try:
            dates[column] = (EXCEL_EPOCH + timedelta(days=int(value))).isoformat()
        except (OverflowError, ValueError):
            continue
    return dates


def _text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _quantity(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    try:
        return max(0, int(value))
    except (OverflowError, ValueError):
        return 0


def _day(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return ""
