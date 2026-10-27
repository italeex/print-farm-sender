import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote, urlsplit

DEFAULT_STATUS = {
    "print_stats": {"state": "printing", "filename": "деталь 7.gcode", "print_duration": 100},
    "virtual_sdcard": {
        "progress": 0.4,
        "layer": 51,
        "layer_count": 310,
        "cur_print_data": {"end_time": 0, "metadata": {"filament_type": "PETG"}},
    },
    "extruder": {"temperature": 250.4},
    "heater_bed": {"temperature": 74.2},
    "display_status": {"progress": 0.4},
}

UNSET = object()


def gcode_with_thumb(payload):
    encoded = base64.b64encode(payload).decode("ascii")
    body = f"; thumbnail begin 300x300 {len(encoded)}\n; {encoded}\n; thumbnail end\n"
    return body.encode("utf-8")


def gcode_with_parts(names):
    return "".join(f"EXCLUDE_OBJECT_DEFINE NAME={name} CENTER=125.0,105.0\n" for name in names).encode("utf-8")


class FakeMoonraker:
    def __init__(self, *, info=None, objects=UNSET, thumb=None, thumb_status=206, log_tail="", failures=0, parts=None):
        self.info = info or {"state": "ready", "state_message": "Printer is ready"}
        self.objects = DEFAULT_STATUS if objects is UNSET else objects
        self.thumb = thumb
        self.thumb_status = thumb_status
        self.log_tail = log_tail
        self.failures = failures
        self.parts = parts or []
        self.requests = []
        self.port = 0
        self._served_failures = 0
        self._server = None
        self._thread = None

    def start(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _record(self):
                path = unquote(urlsplit(self.path).path)
                fake.requests.append(path)
                return path

            def _send_json(self, payload, status=200):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_bytes(self, body, content_type, status=206):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_GET(self):
                path = self._record()
                if path == "/printer/info":
                    if fake._served_failures < fake.failures:
                        fake._served_failures += 1
                        self.send_error(500)
                        return
                    self._send_json({"result": fake.info})
                    return
                if path == "/server/info":
                    self._send_json({"result": {"klipper_connected": True}})
                    return
                if path == "/printer/objects/query":
                    if fake.objects is None:
                        self.send_error(500)
                        return
                    self._send_json({"result": {"status": fake.objects}})
                    return
                if path.startswith("/server/files/gcodes/"):
                    if fake.thumb_status not in (200, 206):
                        self.send_error(fake.thumb_status)
                        return
                    body = gcode_with_thumb(fake.thumb) if fake.thumb else b"G1 X0\n"
                    if fake.parts:
                        body += gcode_with_parts(fake.parts)
                    self._send_bytes(body, "application/octet-stream", fake.thumb_status)
                    return
                if path == "/server/files/logs/klippy.log":
                    self._send_bytes(fake.log_tail.encode("utf-8"), "text/plain")
                    return
                self.send_error(404)

            def do_POST(self):
                path = self._record()
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                if path.startswith("/printer/print/"):
                    self._send_json({"result": "ok"})
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

    def base_url(self):
        return f"http://127.0.0.1:{self.port}"

    def request_count(self, path_prefix):
        return sum(1 for path in self.requests if path.startswith(path_prefix))

    @property
    def request_paths(self):
        return list(self.requests)


class FakeUI:
    def __init__(self, html="<html><head><title>Mainsail</title></head><body></body></html>"):
        self.html = html
        self.port = 0
        self.user_agents = []
        self._server = None
        self._thread = None

    def start(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                fake.user_agents.append(self.headers.get("User-Agent") or "")
                body = fake.html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

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

