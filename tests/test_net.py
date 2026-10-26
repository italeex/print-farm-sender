import ipaddress
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from farm_net import detect_ui, local_subnet, scan_subnet
from tests.fake_moonraker import FakeMoonraker, FakeUI


class DetectUiTests(unittest.TestCase):
    def serve(self, html):
        server = FakeUI(html).start()
        self.addCleanup(server.stop)
        return server

    def test_mainsail_title(self):
        server = self.serve("<html><head><title>Mainsail</title></head><body></body></html>")
        self.assertEqual(detect_ui("127.0.0.1", server.port, timeout=2), "mainsail")

    def test_fluidd_title(self):
        server = self.serve("<html><head><title>Fluidd</title></head><body></body></html>")
        self.assertEqual(detect_ui("127.0.0.1", server.port, timeout=2), "fluidd")

    def test_body_only_fluidd(self):
        server = self.serve('<html><body><div id="fluidd"></div></body></html>')
        self.assertEqual(detect_ui("127.0.0.1", server.port, timeout=2), "fluidd")

    def test_title_wins_over_body(self):
        server = self.serve("<html><head><title>Mainsail</title></head><body>fluidd</body></html>")
        self.assertEqual(detect_ui("127.0.0.1", server.port, timeout=2), "mainsail")

    def test_unknown_page(self):
        server = self.serve("<html><head><title>OctoPrint</title></head><body>ok</body></html>")
        self.assertEqual(detect_ui("127.0.0.1", server.port, timeout=2), "unknown")

    def test_connection_error_is_unknown(self):
        server = FakeUI().start()
        port = server.port
        server.stop()
        self.assertEqual(detect_ui("127.0.0.1", port, timeout=0.5), "unknown")

    def test_browser_user_agent_sent(self):
        server = self.serve("<html><head><title>Mainsail</title></head><body></body></html>")
        detect_ui("127.0.0.1", server.port, timeout=2)
        self.assertTrue(server.user_agents)
        self.assertIn("Mozilla", server.user_agents[0])


class LocalSubnetTests(unittest.TestCase):
    def test_returns_private_slash_24(self):
        network = ipaddress.ip_network(local_subnet())
        self.assertIsInstance(network, ipaddress.IPv4Network)
        self.assertEqual(network.prefixlen, 24)
        self.assertTrue(network.is_private)

    def test_fallback_to_hostname(self):
        with mock.patch("farm_net.socket.socket", side_effect=OSError("нет сети")):
            with mock.patch(
                "farm_net.socket.gethostbyname_ex",
                return_value=("host", [], ["10.5.6.7"]),
            ):
                self.assertEqual(local_subnet(), "10.5.6.0/24")

    def test_fallback_to_default(self):
        with mock.patch("farm_net.socket.socket", side_effect=OSError("нет сети")):
            with mock.patch("farm_net.socket.gethostbyname_ex", side_effect=OSError("нет имени")):
                self.assertEqual(local_subnet(), "192.168.1.0/24")


class ScanSubnetValidationTests(unittest.TestCase):
    def test_public_subnet_rejected(self):
        with self.assertRaises(ValueError) as context:
            scan_subnet("8.8.8.0/24", probe=lambda *args: None)
        self.assertIn("частные", str(context.exception))

    def test_invalid_subnet_rejected(self):
        with self.assertRaises(ValueError) as context:
            scan_subnet("не-подсеть", probe=lambda *args: None)
        self.assertIn("Некорректная", str(context.exception))

    def test_too_large_subnet_rejected(self):
        with self.assertRaises(ValueError) as context:
            scan_subnet("10.0.0.0/8", probe=lambda *args: None)
        self.assertIn("слишком большая", str(context.exception))

    def test_ipv6_rejected(self):
        with self.assertRaises(ValueError):
            scan_subnet("fd00::/120", probe=lambda *args: None)


class ScanSubnetTests(unittest.TestCase):
    def test_probe_results_sorted_and_counted(self):
        calls = []

        def probe(host, moonraker_port, timeout):
            calls.append((host, moonraker_port, timeout))
            if host.endswith((".2", ".5")):
                return {"klipper_connected": True}
            return None

        with mock.patch("farm_net.detect_ui", return_value="unknown"):
            with mock.patch(
                "farm_net._printer_name",
                side_effect=lambda host, port, timeout: f"Принтер {host.rsplit('.', 1)[-1]}",
            ):
                result = scan_subnet(
                    "192.168.7.3/29",
                    moonraker_port=7000,
                    ui_port=5000,
                    timeout=0.1,
                    probe=probe,
                )
        self.assertEqual(result["subnet"], "192.168.7.0/29")
        self.assertEqual(result["scanned"], 6)
        self.assertEqual([item["host"] for item in result["found"]], ["192.168.7.2", "192.168.7.5"])
        self.assertEqual([item["name"] for item in result["found"]], ["Принтер 2", "Принтер 5"])
        self.assertEqual([item["port"] for item in result["found"]], [7000, 7000])
        self.assertEqual([item["ui_port"] for item in result["found"]], [5000, 5000])
        self.assertEqual(len(calls), 6)
        self.assertTrue(all(call[1:] == (7000, 0.1) for call in calls))

    def test_non_dict_probe_result_ignored(self):
        result = scan_subnet("127.0.0.0/30", timeout=0.1, probe=lambda *args: ["нет"])
        self.assertEqual(result["found"], [])

    def test_probe_errors_do_not_raise(self):
        def probe(host, *args):
            if host.endswith(".1"):
                raise RuntimeError("boom")
            return None

        result = scan_subnet("127.0.0.0/30", timeout=0.1, probe=probe)
        self.assertEqual(result["scanned"], 2)
        self.assertEqual(result["found"], [])

    def test_default_probe_finds_fake_moonraker(self):
        moonraker = FakeMoonraker(info={"state": "ready", "hostname": "K1-158"}).start()
        self.addCleanup(moonraker.stop)
        ui = FakeUI("<html><head><title>Fluidd</title></head><body></body></html>").start()
        self.addCleanup(ui.stop)
        result = scan_subnet(
            "127.0.0.0/30",
            moonraker_port=moonraker.port,
            ui_port=ui.port,
            timeout=1.0,
        )
        self.assertEqual(result["scanned"], 2)
        self.assertEqual(len(result["found"]), 1)
        found = result["found"][0]
        self.assertEqual(found["host"], "127.0.0.1")
        self.assertEqual(found["port"], moonraker.port)
        self.assertEqual(found["name"], "K1-158")
        self.assertEqual(found["ui_kind"], "fluidd")
        self.assertEqual(found["ui_port"], ui.port)

    def test_default_probe_uses_octet_name_without_hostname(self):
        moonraker = FakeMoonraker().start()
        self.addCleanup(moonraker.stop)
        ui = FakeUI().start()
        self.addCleanup(ui.stop)
        result = scan_subnet(
            "127.0.0.0/30",
            moonraker_port=moonraker.port,
            ui_port=ui.port,
            timeout=1.0,
        )
        self.assertEqual(result["found"][0]["name"], "Принтер 1")


if __name__ == "__main__":
    unittest.main()
