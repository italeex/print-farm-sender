import base64
import binascii
import hashlib
import re
import threading
from collections import OrderedDict
from pathlib import Path
from urllib.parse import quote

import requests

MAX_THUMB_BYTES = 262144

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

THUMB_PATTERN = re.compile(rb"; thumbnail begin (\d+)x(\d+) (\d+)\r?\n(.*?); thumbnail end", re.DOTALL)
LINE_PREFIX = re.compile(rb"^[ \t]*;[ \t]?", re.MULTILINE)


def extract_thumbnail(data: bytes) -> bytes | None:
    try:
        if not data:
            return None
        best = None
        best_area = -1
        for match in THUMB_PATTERN.finditer(data):
            try:
                width = int(match.group(1))
                height = int(match.group(2))
            except (ValueError, TypeError):
                continue
            area = width * height
            if area <= best_area:
                continue
            raw = match.group(4)
            text = LINE_PREFIX.sub(b"", raw).replace(b"\r", b"\n")
            cleaned = b"".join(line.strip() for line in text.split(b"\n") if line.strip())
            try:
                decoded = base64.b64decode(cleaned, validate=False)
            except (binascii.Error, ValueError):
                continue
            if not decoded.startswith(PNG_SIGNATURE):
                continue
            best = decoded
            best_area = area
        return best
    except Exception:
        return None


def thumbnail_from_file(path: Path) -> bytes | None:
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_THUMB_BYTES)
        return extract_thumbnail(data)
    except (OSError, ValueError):
        return None


def thumbnail_from_http(session, base_url: str, filename: str, *, timeout: float = 5.0) -> bytes | None:
    response = None
    try:
        url = f"{base_url}/server/files/gcodes/{quote(filename)}"
        response = session.get(url, headers={"Range": f"bytes=0-{MAX_THUMB_BYTES - 1}"}, timeout=timeout, stream=True)
        if response.status_code not in (200, 206):
            return None
        data = response.raw.read(MAX_THUMB_BYTES)
        return extract_thumbnail(data)
    except requests.RequestException:
        return None
    except Exception:
        return None
    finally:
        try:
            if response is not None:
                response.close()
        except Exception:
            pass


class ThumbCache:
    def __init__(self, maxsize: int = 64):
        self._maxsize = maxsize
        self._store: dict = {}
        self._order: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def _touch(self, key):
        try:
            self._order.move_to_end(key)
        except KeyError:
            self._order[key] = None

    def _evict(self):
        while len(self._order) > self._maxsize:
            oldest, _ = self._order.popitem(last=False)
            self._store.pop(oldest, None)

    def put(self, key, data):
        if data:
            version = hashlib.sha1(bytes(data)).hexdigest()[:12]
        else:
            version = ""
        with self._lock:
            self._store[key] = (version, data)
            self._touch(key)
            self._evict()

    def version(self, key):
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return ""
            return entry[0]

    def get(self, key, loader):
        with self._lock:
            entry = self._store.get(key)
            if entry:
                self._touch(key)
                return entry[1]
        try:
            data = loader()
        except Exception:
            return None
        if data:
            self.put(key, data)
        return data
