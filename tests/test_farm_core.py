import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import farm_core
from farm_core import (
    AppConfig,
    ConfigError,
    Printer,
    duplicate_basenames,
    load_config,
    save_config,
    seed_printers,
    validate_files,
)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config_path = self.root / "printers.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_printer_roundtrip(self):
        printer = Printer(name="F008 100", host="192.168.7.100", port=7125)
        self.assertEqual(Printer.from_dict(printer.to_dict()), printer)
        self.assertEqual(printer.key, "192.168.7.100:7125")
        self.assertEqual(printer.base_url, "http://192.168.7.100:7125")

    def test_config_roundtrip_preserves_cyrillic(self):
        config = AppConfig(printers=[Printer("Принтер 1", "10.0.0.1")], last_dir="C:\\gcode")
        save_config(config, self.config_path)
        loaded = load_config(self.config_path)
        self.assertEqual(loaded.printers, config.printers)
        self.assertEqual(loaded.last_dir, config.last_dir)

    def test_load_config_seeds_when_missing(self):
        device_dir = self.root / "7.3"
        device_dir.mkdir()
        (device_dir / "deviceInfo.json").write_text(
            json.dumps(
                {
                    "groups": [
                        {
                            "list": [
                                {"address": "192.168.7.100", "model": "F008", "moonrakerPort": 0},
                                {"address": "192.168.7.100", "model": "F008", "moonrakerPort": 0},
                                {"address": "", "model": "F008"},
                                {"address": "192.168.7.103", "model": "F008", "moonrakerPort": 7125},
                            ]
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        config = load_config(self.config_path, seed_base=self.root)
        self.assertEqual(
            [p.key for p in config.printers],
            ["192.168.7.100:7125", "192.168.7.103:7125"],
        )
        self.assertEqual(config.printers[0].name, "F008 100")
        self.assertTrue(self.config_path.exists())

    def test_load_config_empty_seed_when_no_device_info(self):
        config = load_config(self.config_path, seed_base=self.root / "missing")
        self.assertEqual(config.printers, [])
        self.assertTrue(self.config_path.exists())

    def test_broken_config_raises(self):
        self.config_path.write_text("{", encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_config(self.config_path)

    def test_duplicate_basenames_case_insensitive(self):
        files = [Path("a/part.gcode"), Path("b/PART.gcode"), Path("c/other.gcode")]
        self.assertEqual(duplicate_basenames(files), ["PART.gcode"])

    def test_validate_files(self):
        good = self.root / "ok.gcode"
        good.write_text("G1 X0", encoding="utf-8")
        empty = self.root / "empty.gcode"
        empty.write_text("", encoding="utf-8")
        wrong = self.root / "note.txt"
        wrong.write_text("x", encoding="utf-8")
        self.assertIsNone(validate_files([good]))
        self.assertIn("Не выбраны", validate_files([]))
        self.assertIn(".gcode", validate_files([wrong]))
        self.assertIn("Пустой", validate_files([empty]))
        self.assertIn("не найден", validate_files([self.root / "missing.gcode"]))


if __name__ == "__main__":
    unittest.main()
