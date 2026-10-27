import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from farm_parts import Part, parse_parts, parse_parts_bytes


def define_block(names):
    lines = ["; HEADER_BLOCK_START", "M190 S60"]
    for name in names:
        lines.append(f"EXCLUDE_OBJECT_DEFINE NAME={name} CENTER=125.0,105.0 POLYGONS=[[0,0]]")
    lines.append("; HEADER_BLOCK_END")
    return "\n".join(lines) + "\n"


class ParsePartsTests(unittest.TestCase):
    def test_single_part_thirty_five_copies(self):
        names = [f"Корпус.stl_id_{index}_copy_0" for index in range(35)]
        parts = parse_parts(define_block(names))
        self.assertEqual(parts, [Part("Корпус.stl", 35)])

    def test_two_parts(self):
        names = [
            "Корпус_id_0_copy_0",
            "Крышка_id_1_copy_0",
            "Корпус_id_0_copy_1",
            "Крышка_id_1_copy_1",
            "Корпус_id_0_copy_2",
        ]
        parts = parse_parts(define_block(names))
        self.assertEqual(parts, [Part("Корпус", 3), Part("Крышка", 2)])

    def test_no_blocks(self):
        self.assertEqual(parse_parts(define_block([])), [])
        self.assertEqual(parse_parts("G1 X0\nM104 S200\n"), [])

    def test_malformed_lines_are_skipped(self):
        text = "\n".join(
            [
                "EXCLUDE_OBJECT_DEFINE",
                "EXCLUDE_OBJECT_DEFINE CENTER=1,2",
                "EXCLUDE_OBJECT_DEFINE NAME=",
                "EXCLUDE_OBJECT_DEFINE NAME=_id_0_copy_0",
                "EXCLUDE_OBJECT_DEFINE NAME=Деталь_id_0_copy_0",
            ]
        )
        self.assertEqual(parse_parts(text), [Part("Деталь", 1)])

    def test_case_insensitive_marker(self):
        text = "exclude_object_define name=База_id_3_copy_1 CENTER=0,0\n"
        self.assertEqual(parse_parts(text), [Part("База", 1)])

    def test_empty_input(self):
        self.assertEqual(parse_parts(""), [])
        self.assertEqual(parse_parts(None), [])
        self.assertEqual(parse_parts_bytes(b""), [])

    def test_bytes_input(self):
        data = define_block(["Деталь_id_0_copy_0", "Деталь_id_0_copy_1"]).encode("utf-8")
        self.assertEqual(parse_parts_bytes(data), [Part("Деталь", 2)])

    def test_bytes_replaces_broken_utf8(self):
        data = b"EXCLUDE_OBJECT_DEFINE NAME=\xff\xfe_id_0_copy_0\n"
        parts = parse_parts_bytes(data)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].count, 1)


if __name__ == "__main__":
    unittest.main()
