import json
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
CELL = re.compile(r"([A-Za-z]+)(\d+)")
PREFIX = re.compile(r"^(?:МВСЛ|МФСЛ)\.", re.IGNORECASE)
SPLIT = re.compile(r"^([^\s_]+)[\s_]+(.*)$")
EXCEL_EPOCH = date(1899, 12, 30)
PLAN_SUFFIXES = (".xlsx", ".json")
MAX_XLSX_BYTES = 20 * 1024 * 1024
MIN_SERIAL = (date(2000, 1, 1) - EXCEL_EPOCH).days
MAX_SERIAL = (date(2100, 12, 31) - EXCEL_EPOCH).days
PLAN_SHEET_NAMES = ("план-график", "план график")
DATE_FORMATS = frozenset(range(14, 23)) | frozenset((45, 46, 47))


def part_key(name) -> str:
    text = str(name or "").strip()
    if not text:
        return ""
    token = re.split(r"[\s_]", text, maxsplit=1)[0]
    if token.lower().endswith(".stl"):
        token = token[:-4]
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
        try:
            size = target.stat().st_size
        except OSError as exc:
            raise ValueError("Не удалось прочитать файл Excel") from exc
        if size > MAX_XLSX_BYTES:
            raise ValueError("Файл плана больше 20 МБ")
        return _parse_xlsx(target)
    if suffix == ".json":
        return _parse_json(target)
    raise ValueError("Поддерживаются файлы .xlsx и .json")


def _parse_xlsx(path: Path) -> dict:
    rows, date_columns = _xlsx_rows(path)
    dates = _dates_from_row(rows.get(2, {}), date_columns)
    if not dates:
        raise ValueError("В файле плана не найдены даты")
    days = [dates[column] for column in sorted(dates)]
    items = []
    plan_rows = 0
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
        plan_rows += 1
        code, title = split_code_name(name)
        key = part_key(name)
        if not key:
            continue
        for column, day in dates.items():
            target = _quantity(values.get(column))
            if target <= 0:
                continue
            items.append({"date": day, "code": code, "name": title, "key": key, "target": target})
    if not plan_rows:
        raise ValueError("В файле плана не найдены строки «план»")
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
        key = part_key(raw.get("key") or code or name)
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


def _xlsx_rows(path: Path) -> tuple[dict[int, dict[int, object]], set[int]]:
    try:
        with zipfile.ZipFile(path) as archive:
            shared = _shared_strings(archive)
            styles = _date_styles(archive)
            chosen = None
            for name, sheet in _sheets(archive):
                rows, date_columns = _read_sheet(archive, sheet, shared, styles)
                if _sheet_title(name) in PLAN_SHEET_NAMES or _looks_like_plan(rows):
                    chosen = (rows, date_columns)
                    break
    except (zipfile.BadZipFile, zipfile.LargeZipFile, KeyError, OSError) as exc:
        raise ValueError("Не удалось прочитать файл Excel") from exc
    if chosen is None:
        raise ValueError("В файле плана не найден лист «План-график»")
    return chosen


def _read_sheet(archive, name, shared, styles) -> tuple[dict[int, dict[int, object]], set[int]]:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise ValueError("В файле плана не найден лист «План-график»") from exc
    if info.file_size > MAX_XLSX_BYTES:
        raise ValueError("Файл плана больше 20 МБ")
    try:
        root = ET.fromstring(archive.read(name))
    except ET.ParseError as exc:
        raise ValueError("Повреждённый файл Excel") from exc
    rows: dict[int, dict[int, object]] = {}
    date_columns: set[int] = set()
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
            if number == 2 and _style(cell) in styles:
                date_columns.add(column)
            value = _cell_value(cell, shared)
            if value is None:
                continue
            values[column] = value
        rows[number] = values
    return rows, date_columns


def _sheets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    try:
        root = ET.fromstring(archive.read("xl/workbook.xml"))
    except (KeyError, ET.ParseError):
        return [("", name) for name in _worksheet_names(archive)]
    rels = _sheet_rels(archive)
    result = []
    for node in root.iter(f"{NS}sheet"):
        path = rels.get(node.get(f"{REL_NS}id") or "", "")
        if path:
            result.append((str(node.get("name") or ""), path))
    if not result:
        return [("", name) for name in _worksheet_names(archive)]
    return result


def _sheet_rels(archive: zipfile.ZipFile) -> dict[str, str]:
    try:
        root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    except (KeyError, ET.ParseError):
        return {}
    rels = {}
    for node in root.findall(f"{PKG_NS}Relationship"):
        target = str(node.get("Target") or "").strip()
        if not target:
            continue
        if target.startswith("/"):
            path = target.lstrip("/")
        else:
            path = posixpath.normpath(posixpath.join("xl", target))
        rels[str(node.get("Id") or "")] = path
    return rels


def _worksheet_names(archive: zipfile.ZipFile) -> list[str]:
    names = [
        name
        for name in archive.namelist()
        if name.startswith("xl/worksheets/") and name.endswith(".xml")
    ]
    names.sort(key=lambda name: (name != "xl/worksheets/sheet1.xml", name))
    return names


def _sheet_title(name) -> str:
    return " ".join(str(name or "").split()).lower()


def _looks_like_plan(rows: dict[int, dict[int, object]]) -> bool:
    header = False
    plan = False
    for values in rows.values():
        for column, value in values.items():
            text = _text(value).lower()
            if column == 0 and "номенклатура" in text:
                header = True
            if text == "план":
                plan = True
    return header and plan


def _date_styles(archive: zipfile.ZipFile) -> set[int]:
    try:
        root = ET.fromstring(archive.read("xl/styles.xml"))
    except (KeyError, ET.ParseError):
        return set()
    custom = {}
    for node in root.iter(f"{NS}numFmt"):
        try:
            custom[int(node.get("numFmtId") or -1)] = str(node.get("formatCode") or "")
        except ValueError:
            continue
    styles = set()
    xfs = root.find(f"{NS}cellXfs")
    if xfs is None:
        return styles
    for index, xf in enumerate(xfs.findall(f"{NS}xf")):
        try:
            fmt = int(xf.get("numFmtId") or 0)
        except ValueError:
            continue
        if fmt in DATE_FORMATS or _is_date_format(custom.get(fmt, "")):
            styles.add(index)
    return styles


def _is_date_format(code: str) -> bool:
    text = re.sub(r'"[^"]*"', "", str(code or ""))
    text = re.sub(r"\[[^\]]*\]", "", text)
    return bool(re.search(r"[ydm]", text.replace("\\", ""), re.IGNORECASE))


def _style(cell) -> int:
    try:
        return int(cell.get("s") or -1)
    except ValueError:
        return -1


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


def _dates_from_row(row: dict[int, object], columns: set[int]) -> dict[int, str]:
    dates: dict[int, str] = {}
    for column in sorted(columns):
        if column < 3:
            continue
        value = row.get(column)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        serial = int(value)
        if serial < MIN_SERIAL or serial > MAX_SERIAL:
            continue
        dates[column] = (EXCEL_EPOCH + timedelta(days=serial)).isoformat()
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
