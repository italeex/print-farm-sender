import json
import sys
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from farm_plan import parse_plan_file, part_key

EPOCH = date(1899, 12, 30)
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
LETTERS = [chr(65 + index) for index in range(26)]
DAYS = ["2026-10-26", "2026-10-27", "2026-10-28"]


def serial(day):
    return (date.fromisoformat(day) - EPOCH).days


def write_plan_xlsx(path, days, parts, *, fact_rows=True):
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
    dates = "".join(f'<c r="{ref(3 + pos, 2)}"><v>{serial(day)}</v></c>' for pos, day in enumerate(days))
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
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<worksheet xmlns="{NS}"><sheetData>{"".join(rows)}</sheetData></worksheet>'
    )
    strings = "".join(f"<si><t>{escape(text)}</t></si>" for text in shared)
    shared_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<sst xmlns="{NS}" count="{len(shared)}" uniqueCount="{len(shared)}">{strings}</sst>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return Path(path)


class PartKeyTests(unittest.TestCase):
    def test_strips_prefix_space_and_underscore(self):
        self.assertEqual(part_key("МВСЛ.ГР3.01.42000.01_Крышка.stl"), "ГР3.01.42000.01")
        self.assertEqual(part_key("МФСЛ.ГР3.01.42000.01 Крышка (3D)"), "ГР3.01.42000.01")

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
