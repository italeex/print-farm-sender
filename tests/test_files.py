import json
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from farm_core import Printer


class FakeFileServer:
    def __init__(self, *, folder_broken=False, upload_status=0):
        self.folder_broken = folder_broken
        self.upload_status = upload_status
        self.store = {}
        self.requests = []
        self.port = 0
        self._server = None
        self._thread = None

    def start(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _send_json(self, payload, status=200):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _path(self):
                raw = urlsplit(self.path)
                fake.requests.append(raw.path)
                return raw.path, raw.query

            def do_GET(self):
                path, query = self._path()
                if path == "/server/files/directory":
                    if fake.folder_broken:
                        self._send_json({"error": "Directory does not exist"}, status=400)
                        return
                    files = [
                        {"filename": key.split("/", 1)[1], "size": len(data), "modified": 100.0}
                        for key, data in fake.store.items()
                        if key.startswith("FARM/")
                    ]
                    self._send_json({"result": {"dirs": [], "files": files, "root_info": {}}})
                    return
                if path == "/server/files/list":
                    files = [
                        {"path": key, "size": len(data), "modified": 100.0}
                        for key, data in fake.store.items()
                        if "/" not in key
                    ]
                    self._send_json({"result": files})
                    return
                self.send_error(404)

            def do_POST(self):
                path, _ = self._path()
                if path == "/server/files/upload":
                    if fake.upload_status:
                        self._send_json({"error": "boom"}, status=fake.upload_status)
                        return
                    length = int(self.headers.get("Content-Length") or 0)
                    body = self.rfile.read(length).decode("latin-1") if length else ""
                    match = re.search(r'name="path"\r\n\r\n([^\r]+)', body)
                    fnames = re.findall(r'filename="([^"]+)"', body)
                    filename = fnames[-1] if fnames else "unknown"
                    if match:
                        if fake.folder_broken:
                            self._send_json({"error": "Directory does not exist"}, status=400)
                            return
                        key = f"{match.group(1).strip()}/{filename}"
                    else:
                        key = filename
                    fake.store[key] = b"data"
                    self._send_json({"result": {"item": {"path": key}}}, status=201)
                    return
                self.send_error(404)

            def do_DELETE(self):
                path, _ = self._path()
                if path.startswith("/server/files/gcodes/"):
                    key = unquote(path[len("/server/files/gcodes/"):])
                    if key in fake.store:
                        del fake.store[key]
                        self._send_json({"result": "ok"})
                    else:
                        self._send_json({"error": "not found"}, status=404)
                    return
                self.send_error(404)

            def log_message(self, *args):
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def printer(self):
        return Printer(name="Тест", host="127.0.0.1", port=self.port)


def make_gcode(folder, content=b"G1 X0\n"):
    path = Path(folder) / "src.gcode"
    path.write_bytes(content)
    return path


class FilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def start_server(self, **kwargs):
        server = FakeFileServer(**kwargs).start()
        self.addCleanup(server.stop)
        return server

    def test_names(self):
        import farm_files as ff
        self.assertEqual(ff.remote_name(7), "N7.gcode")
        self.assertEqual(ff.physical_name("N7.gcode", "folder"), "N7.gcode")
        self.assertEqual(ff.physical_name("N7.gcode", "prefix"), "FARM_N7.gcode")

    def test_folder_cycle(self):
        import farm_files as ff
        server = self.start_server()
        printer = server.printer()
        src = make_gcode(self.tmp.name)
        used = ff.upload_to_farm(printer, src, "N7.gcode", "folder")
        self.assertEqual(used, "folder")
        self.assertIn("FARM/N7.gcode", server.store)
        items = ff.list_farm(printer, "folder")
        self.assertEqual([(i["name"], i["size"]) for i in items], [("N7.gcode", 4)])
        self.assertIn("modified", items[0])
        deleted = ff.delete_from_farm(printer, ["N7.gcode"], "folder")
        self.assertEqual(deleted, ["N7.gcode"])
        self.assertEqual(server.store, {})

    def test_prefix_cycle(self):
        import farm_files as ff
        server = self.start_server()
        printer = server.printer()
        src = make_gcode(self.tmp.name)
        used = ff.upload_to_farm(printer, src, "N7.gcode", "prefix")
        self.assertEqual(used, "prefix")
        self.assertIn("FARM_N7.gcode", server.store)
        items = ff.list_farm(printer, "prefix")
        self.assertEqual([i["name"] for i in items], ["N7.gcode"])
        deleted = ff.delete_from_farm(printer, ["N7.gcode"], "prefix")
        self.assertEqual(deleted, ["N7.gcode"])

    def test_folder_fallback_to_prefix_on_400(self):
        import farm_files as ff
        server = self.start_server(folder_broken=True)
        printer = server.printer()
        src = make_gcode(self.tmp.name)
        used = ff.upload_to_farm(printer, src, "N7.gcode", "folder")
        self.assertEqual(used, "prefix")
        self.assertIn("FARM_N7.gcode", server.store)
        items = ff.list_farm(printer, "prefix")
        self.assertEqual([i["name"] for i in items], ["N7.gcode"])

    def test_missing_folder_list_empty(self):
        import farm_files as ff
        server = self.start_server(folder_broken=True)
        self.assertEqual(ff.list_farm(server.printer(), "folder"), [])

    def test_invalid_names_rejected(self):
        import farm_files as ff
        server = self.start_server()
        printer = server.printer()
        for bad in ("../N1.gcode", "a/b.gcode", "N1.gcode/../x", "x.gcode", "N.gcode", ".."):
            with self.assertRaises(ff.FarmFileError, msg=bad):
                ff.delete_from_farm(printer, [bad], "folder")
        self.assertEqual(server.requests, [])

    def test_upload_error_raises(self):
        import farm_files as ff
        server = self.start_server(upload_status=500)
        printer = server.printer()
        src = make_gcode(self.tmp.name)
        with self.assertRaises(ff.FarmFileError):
            ff.upload_to_farm(printer, src, "N7.gcode", "prefix")


if __name__ == "__main__":
    unittest.main()
