import re
from dataclasses import dataclass

DEFINE_MARKER = "EXCLUDE_OBJECT_DEFINE"
NAME_PATTERN = re.compile(r"\bNAME=(\S+)", re.IGNORECASE)
COPY_SUFFIX = re.compile(r"_id_\d+_copy_\d+$")


@dataclass(frozen=True)
class Part:
    name: str
    count: int


def parse_parts(text: str) -> list[Part]:
    if not text:
        return []
    counts: dict[str, int] = {}
    for line in text.splitlines():
        if DEFINE_MARKER not in line.upper():
            continue
        match = NAME_PATTERN.search(line)
        if match is None:
            continue
        name = COPY_SUFFIX.sub("", match.group(1))
        if not name:
            continue
        counts[name] = counts.get(name, 0) + 1
    return [Part(name, counts[name]) for name in sorted(counts)]


def parse_parts_bytes(data: bytes) -> list[Part]:
    if not data:
        return []
    return parse_parts(data.decode("utf-8", errors="replace"))
