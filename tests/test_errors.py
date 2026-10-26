import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from farm_errors import CREALITY_HELP_URL, extract_log_error, parse_error


class ParseErrorTests(unittest.TestCase):
    def test_creality_code(self):
        info = parse_error("TE2509: Unable to obtain nozzle temperature")
        self.assertEqual(info.code, "TE2509")
        self.assertIn("термистор", info.label.lower())
        self.assertEqual(info.help_url, CREALITY_HELP_URL)

    def test_numeric_key_code(self):
        info = parse_error("!! key2564 Heater extruder not heating at expected rate")
        self.assertEqual(info.code, "2564")
        self.assertIn("нагрев", info.label.lower())

    def test_key_with_colon_from_screen(self):
        info = parse_error("Key：2509，Nozzle Sensor temperature exception")
        self.assertEqual(info.code, "2509")

    def test_unknown_code_keeps_raw_detail(self):
        info = parse_error("ZZ9999: weird failure")
        self.assertEqual(info.label, "Ошибка принтера")
        self.assertIn("ZZ9999", info.detail)

    def test_keyword_fallback_without_code(self):
        info = parse_error("Lost communication with MCU 'mcu'")
        self.assertIn("MCU", info.label)

    def test_ready_state_returns_none(self):
        self.assertIsNone(parse_error("", state="ready"))

    def test_shutdown_state_without_message(self):
        info = parse_error("", state="shutdown")
        self.assertIn("Klipper", info.label)

    def test_extract_log_error(self):
        log = 'ok\n!! {"code":"key2564", "msg":"Heater extruder not heating at expected rate"}\n'
        self.assertEqual(
            extract_log_error(log),
            ("2564", "Heater extruder not heating at expected rate"),
        )

    def test_extract_log_error_garbage(self):
        self.assertIsNone(extract_log_error("!! not json\n"))


if __name__ == "__main__":
    unittest.main()
