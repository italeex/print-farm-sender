import json
import sys
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path
from unittest import mock
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from farm_plan import parse_plan_file, part_key

EPOCH = date(1899, 12, 30)
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
LETTERS = [chr(65 + index) for index in range(26)]
DAYS = ["2026-10-26", "2026-10-27", "2026-10-28"]
STYLES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    f'<styleSheet xmlns="{NS}"><numFmts count="1">'
    '<numFmt numFmtId="164" formatCode="dd.mm.yyyy"/></numFmts>'
    '<cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="164" applyNumberFormat="1"/></cellXfs>'
    "</styleSheet>"
)


def serial(day):
    return (date.fromisoformat(day) - EPOCH).days


def worksheet(rows_xml):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<worksheet xmlns="{NS}"><sheetData>{rows_xml}</sheetData></worksheet>'
    )


def cover_sheet():
    return worksheet(
        '<row r="1"><c r="A1" t="inlineStr"><is><t>Сводка</t></is></c></row>'
        '<row r="2"><c r="D2" s="1"><v>46321</v></c><c r="E2"><v>46322</v></c></row>'
        '<row r="3"><c r="A3" t="inlineStr"><is><t>Итого</t></is></c><c r="D3"><v>5</v></c></row>'
    )


def write_plan_xlsx(
    path,
    days,
    parts,
    *,
    fact_rows=True,
    sheet_name="План-график",
    cover=False,
    extra_columns=(),
):
    shared = []
    index = {}

    def sid(text):
        if text not in index:
            index[text] = len(shared)
            shared.append(text)
        return index[text]

    def ref(column, row):
        return f"{LETTERS[column]}{row}"

    rows = [
        '<row r="1">'
        f'<c r="A1" t="s"><v>{sid("Номенклатура")}</v></c>'
        f'<c r="C1" t="s"><v>{sid("в план")}</v></c>'
        "</row>"
    ]
    dates = "".join(
        f'<c r="{ref(3 + pos, 2)}" s="1"><v>{serial(day)}</v></c>' for pos, day in enumerate(days)
    )
    dates += "".join(f'<c r="{ref(column, 2)}"><v>{value}</v></c>' for column, value in extra_columns)
    rows.append(f'<row r="2">{dates}</row>')
    row = 3
    for name, targets in parts:
        plan = "".join(
            f'<c r="{ref(3 + pos, row)}"><v>{value}</v></c>'
            for pos, value in enumerate(targets)
            if value is not None
        )
        cells = ""
        if name:
            cells += f'<c r="A{row}" t="s"><v>{sid(name)}</v></c>'
        cells += f'<c r="B{row}" t="s"><v>{sid("план")}</v></c>'
        cells += f'<c r="C{row}"><v>444</v></c>'
        rows.append(f'<row r="{row}">{cells}{plan}</row>')
        row += 1
        if not fact_rows:
            continue
        facts = "".join(f'<c r="{ref(3 + pos, row)}"><v>99</v></c>' for pos in range(len(days)))
        rows.append(
            f'<row r="{row}"><c r="B{row}" t="s"><v>{sid("факт")}</v></c>'
            f'<c r="C{row}"><v>777</v></c>{facts}</row>'
        )
        row += 1
    strings = "".join(f"<si><t>{escape(text)}</t></si>" for text in shared)
    shared_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<sst xmlns="{NS}" count="{len(shared)}" uniqueCount="{len(shared)}">{strings}</sst>'
    )
    sheets = []
    if cover:
        sheets.append(("Сводка", cover_sheet()))
    sheets.append((sheet_name, worksheet("".join(rows))))
    sheet_nodes = "".join(
        f'<sheet name="{escape(name)}" sheetId="{pos}" r:id="rId{pos}"/>'
        for pos, (name, _) in enumerate(sheets, 1)
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<workbook xmlns="{NS}" xmlns:r="{REL_NS}"><sheets>{sheet_nodes}</sheets></workbook>'
    )
    rel_base = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    rels = "".join(
        f'<Relationship Id="rId{pos}" Type="{rel_base}/worksheet" '
        f'Target="worksheets/sheet{pos}.xml"/>'
        for pos in range(1, len(sheets) + 1)
    )
    rels += f'<Relationship Id="rId{len(sheets) + 1}" Type="{rel_base}/styles" Target="styles.xml"/>'
    rels += (
        f'<Relationship Id="rId{len(sheets) + 2}" Type="{rel_base}/sharedStrings" '
        'Target="sharedStrings.xml"/>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Relationships xmlns="{PKG_NS}">{rels}</Relationships>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared_xml)
        archive.writestr("xl/styles.xml", STYLES)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        for pos, (_, body) in enumerate(sheets, 1):
            archive.writestr(f"xl/worksheets/sheet{pos}.xml", body)
    return Path(path)


class PartKeyTests(unittest.TestCase):
    def test_strips_prefix_space_and_underscore(self):
        self.assertEqual(part_key("МВСЛ.ГР3.01.42000.01_Крышка.stl"), "ГР3.01.42000.01")
        self.assertEqual(part_key("МФСЛ.ГР3.01.42000.01 Крышка (3D)"), "ГР3.01.42000.01")

    def test_strips_bare_stl_suffix(self):
        self.assertEqual(part_key("МВСЛ.ГР3.01.42000.01.stl"), "ГР3.01.42000.01")
        self.assertEqual(part_key("ГР3.01.42000.01.stl"), "ГР3.01.42000.01")
        self.assertEqual(part_key("гр3.01.42000.01.STL"), "ГР3.01.42000.01")
        self.assertEqual(part_key("Крышка.stl"), "КРЫШКА")
        self.assertEqual(part_key(".stl"), "")

    def test_only_factory_prefixes_stripped(self):
        self.assertEqual(part_key("ПЛ.ИМ2.01.56060.02"), "ПЛ.ИМ2.01.56060.02")
        self.assertNotEqual(part_key("ПЛ.ИМ2.01.56060.02"), part_key("ИМ2.01.56060.02"))
        self.assertEqual(part_key("AB.ГР3.01.42000.01"), "AB.ГР3.01.42000.01")

    def test_lowercase_prefix(self):
        self.assertEqual(part_key("мвсл.гр3.01.42000.01 Крышка (3D)"), "ГР3.01.42000.01")

    def test_without_prefix(self):
        self.assertEqual(part_key("ГР3.01.42000.01"), "ГР3.01.42000.01")
        self.assertEqual(part_key("  ГР3.01.42000.01_Крышка  "), "ГР3.01.42000.01")

    def test_empty(self):
        self.assertEqual(part_key(""), "")
        self.assertEqual(part_key(None), "")


class XlsxPlanTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.folder / "План график.xlsx"

    def test_parse_plan_and_skip_fact(self):
        write_plan_xlsx(
            self.path,
            DAYS,
            [
                ("МВСЛ.ГР3.01.42000.01 Крышка (3D)", (20, 0, 5)),
                ("МФСЛ.ГР3.02.10000.01 Корпус (3D)", (None, 7, None)),
            ],
        )
        parsed = parse_plan_file(self.path)
        self.assertEqual(parsed["week_start"], DAYS[0])
        self.assertEqual(parsed["days"], DAYS)
        self.assertEqual(
            [(item["date"], item["key"], item["target"]) for item in parsed["items"]],
            [
                (DAYS[0], "ГР3.01.42000.01", 20),
                (DAYS[1], "ГР3.02.10000.01", 7),
                (DAYS[2], "ГР3.01.42000.01", 5),
            ],
        )
        first = parsed["items"][0]
        self.assertEqual(first["code"], "МВСЛ.ГР3.01.42000.01")
        self.assertEqual(first["name"], "Крышка (3D)")
        self.assertEqual(sum(item["target"] for item in parsed["items"]), 32)

    def test_merged_cells_and_missing_fact_rows(self):
        write_plan_xlsx(
            self.path,
            DAYS,
            [
                ("МВСЛ.ГР3.01.42000.01 Крышка (3D)", (10, None, 0)),
                ("", (4, None, None)),
            ],
            fact_rows=False,
        )
        parsed = parse_plan_file(self.path)
        self.assertEqual(parsed["days"], DAYS)
        self.assertEqual(len(parsed["items"]), 1)
        self.assertEqual(parsed["items"][0]["target"], 14)
        self.assertEqual(parsed["items"][0]["date"], DAYS[0])

    def test_unsupported_suffix(self):
        other = self.folder / "plan.csv"
        other.write_text("дата;деталь", encoding="utf-8")
        with self.assertRaises(ValueError):
            parse_plan_file(other)

    def test_missing_file(self):
        with self.assertRaises(ValueError):
            parse_plan_file(self.folder / "нет.xlsx")

    def test_selects_named_plan_sheet(self):
        write_plan_xlsx(
            self.path,
            DAYS,
            [("МВСЛ.ГР3.01.42000.01 Крышка (3D)", (4, None, None))],
            cover=True,
        )
        parsed = parse_plan_file(self.path)
        self.assertEqual(parsed["days"], DAYS)
        self.assertEqual(parsed["week_start"], DAYS[0])
        self.assertEqual([item["target"] for item in parsed["items"]], [4])

    def test_selects_sheet_by_markers(self):
        write_plan_xlsx(
            self.path,
            DAYS,
            [("МВСЛ.ГР3.01.42000.01 Крышка (3D)", (2, 0, 0))],
            sheet_name="Лист1",
            cover=True,
        )
        parsed = parse_plan_file(self.path)
        self.assertEqual(parsed["days"], DAYS)
        self.assertEqual(parsed["items"][0]["target"], 2)

    def test_reports_missing_plan_sheet(self):
        path = self.folder / "cover.xlsx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("xl/worksheets/sheet1.xml", cover_sheet())
        with self.assertRaises(ValueError) as caught:
            parse_plan_file(path)
        self.assertIn("План-график", str(caught.exception))

    def test_rejects_sheet_without_plan_rows(self):
        write_plan_xlsx(self.path, DAYS, [])
        with self.assertRaises(ValueError) as caught:
            parse_plan_file(self.path)
        self.assertIn("план", str(caught.exception))

    def test_ignores_numeric_headers_outside_date_columns(self):
        write_plan_xlsx(
            self.path,
            DAYS,
            [("МВСЛ.ГР3.01.42000.01 Крышка (3D)", (1, 2, 3))],
            extra_columns=((9, 45), (10, 45000)),
        )
        parsed = parse_plan_file(self.path)
        self.assertEqual(parsed["days"], DAYS)
        self.assertEqual(parsed["week_start"], DAYS[0])
        self.assertEqual([item["date"] for item in parsed["items"]], DAYS)

    def test_rejects_invalid_zip(self):
        bad = self.folder / "bad.xlsx"
        bad.write_bytes(b"PK\x03\x04broken")
        with self.assertRaises(ValueError):
            parse_plan_file(bad)

    def test_rejects_oversized_file(self):
        write_plan_xlsx(self.path, DAYS, [])
        with mock.patch("farm_plan.MAX_XLSX_BYTES", 10):
            with self.assertRaises(ValueError) as caught:
                parse_plan_file(self.path)
        self.assertIn("больше", str(caught.exception))


class JsonPlanTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_normalizes_items(self):
        path = self.folder / "plan.json"
        path.write_text(
            json.dumps(
                {
                    "week_start": DAYS[0],
                    "items": [
                        {"date": DAYS[0], "code": "МВСЛ.ГР3.01.42000.01", "name": "Крышка (3D)", "target": 10},
                        {"date": DAYS[1], "name": "МФСЛ.ГР3.02.10000.01 Корпус", "target": 3},
                        {"date": DAYS[1], "code": "МВСЛ.ГР3.01.42000.01", "name": "Крышка (3D)", "target": 0},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        parsed = parse_plan_file(path)
        self.assertEqual(parsed["week_start"], DAYS[0])
        self.assertEqual(parsed["days"], DAYS[:2])
        self.assertEqual(
            [(item["date"], item["key"], item["target"]) for item in parsed["items"]],
            [(DAYS[0], "ГР3.01.42000.01", 10), (DAYS[1], "ГР3.02.10000.01", 3)],
        )
        self.assertEqual(parsed["items"][1]["code"], "МФСЛ.ГР3.02.10000.01 Корпус")

    def test_week_start_falls_back_to_first_item(self):
        path = self.folder / "plan.json"
        path.write_text(
            json.dumps({"items": [{"date": DAYS[1], "code": "МВСЛ.ГР3.01.42000.01", "target": 1}]}),
            encoding="utf-8",
        )
        parsed = parse_plan_file(path)
        self.assertEqual(parsed["week_start"], DAYS[1])
        self.assertEqual(parsed["days"], [DAYS[1]])

    def test_normalizes_explicit_key(self):
        path = self.folder / "plan.json"
        path.write_text(
            json.dumps(
                {
                    "items": [
                        {"date": DAYS[0], "key": "мвсл.гр3.01.42000.01.stl", "target": 2},
                        {"date": DAYS[0], "key": "ГР3.01.42000.01", "target": 3},
                    ]
                }
            ),
            encoding="utf-8",
        )
        parsed = parse_plan_file(path)
        self.assertEqual([item["key"] for item in parsed["items"]], ["ГР3.01.42000.01"])
        self.assertEqual(parsed["items"][0]["target"], 5)

    def test_rejects_bad_payload(self):
        path = self.folder / "plan.json"
        path.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            parse_plan_file(path)
        path.write_text("{", encoding="utf-8")
        with self.assertRaises(ValueError):
            parse_plan_file(path)


if __name__ == "__main__":
    unittest.main()
