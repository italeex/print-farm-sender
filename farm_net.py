from __future__ import annotations

import ipaddress
import re
import socket
from concurrent.futures import ThreadPoolExecutor

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
SERVER_INFO_PATH = "/server/info"
PRINTER_INFO_PATH = "/printer/info"
DEFAULT_MOONRAKER_PORT = 7125
DEFAULT_UI_PORT = 4408
DEFAULT_WORKERS = 64
DEFAULT_PROBE_TIMEOUT = 0.35
MAX_HOSTS = 1024
FALLBACK_SUBNET = "192.168.1.0/24"


def detect_ui(host, port, timeout=3.0) -> str:
    try:
        response = requests.get(
            f"http://{host}:{port}/",
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )
        text = response.text or ""
    except Exception:
        return "unknown"
    lowered = text.lower()
    title = ""
    match = re.search(r"<title[^>]*>(.*?)</title>", lowered, re.S)
    if match:
        title = match.group(1)
    for kind in ("fluidd", "mainsail"):
        if kind in title:
            return kind
    for kind in ("fluidd", "mainsail"):
        if kind in lowered:
            return kind
    return "unknown"


def local_subnet() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            address = sock.getsockname()[0]
        network = ipaddress.ip_network(f"{address}/24", strict=False)
        if isinstance(network, ipaddress.IPv4Network):
            return str(network)
    except (OSError, ValueError):
        pass
    try:
        address = socket.gethostbyname_ex(socket.gethostname())[2][0]
        network = ipaddress.ip_network(f"{address}/24", strict=False)
        if isinstance(network, ipaddress.IPv4Network):
            return str(network)
    except (OSError, ValueError, IndexError):
        pass
    return FALLBACK_SUBNET


def _validate_subnet(subnet: str) -> ipaddress.IPv4Network:
    try:
        network = ipaddress.ip_network(str(subnet).strip(), strict=False)
    except ValueError as exc:
        raise ValueError(f"Некорректная подсеть: {subnet}") from exc
    if not isinstance(network, ipaddress.IPv4Network):
        raise ValueError("Поддерживаются только IPv4-подсети")
    if not network.is_private:
        raise ValueError("Сканировать можно только частные подсети")
    if network.num_addresses > MAX_HOSTS + 2:
        raise ValueError(f"Подсеть слишком большая: {network} (максимум 1024 адреса)")
    return network


def _printer_name(host: str, port: int, timeout: float) -> str:
    fallback = f"Принтер {host.rsplit('.', 1)[-1]}"
    try:
        response = requests.get(f"http://{host}:{port}{PRINTER_INFO_PATH}", timeout=timeout)
        if response.status_code != 200:
            return fallback
        payload = response.json()
        result = payload.get("result") if isinstance(payload, dict) else None
        hostname = str((result or {}).get("hostname") or "").strip()
        if hostname:
            return hostname
    except Exception:
        pass
    return fallback


def _probe_moonraker(host: str, port: int, timeout: float):
    try:
        response = requests.get(f"http://{host}:{port}{SERVER_INFO_PATH}", timeout=timeout)
        if response.status_code != 200:
            return None
        payload = response.json()
    except Exception:
        return None
    result = payload.get("result") if isinstance(payload, dict) else None
    return result if isinstance(result, dict) else None


def scan_subnet(
    subnet,
    *,
    moonraker_port=DEFAULT_MOONRAKER_PORT,
    ui_port=DEFAULT_UI_PORT,
    workers=DEFAULT_WORKERS,
    timeout=DEFAULT_PROBE_TIMEOUT,
    probe=None,
) -> dict:
    network = _validate_subnet(subnet)
    hosts = [str(host) for host in network.hosts()]
    prober = probe if probe is not None else _probe_moonraker

    def inspect(host):
        try:
            result = prober(host, moonraker_port, timeout)
        except Exception:
            return None
        if not isinstance(result, dict):
            return None
        try:
            name = _printer_name(host, moonraker_port, timeout)
            ui_kind = detect_ui(host, ui_port)
        except Exception:
            return None
        return {
            "host": host,
            "port": moonraker_port,
            "name": name,
            "ui_kind": ui_kind,
            "ui_port": ui_port,
        }

    found = []
    if hosts:
        with ThreadPoolExecutor(max_workers=max(1, min(int(workers), len(hosts)))) as pool:
            futures = [pool.submit(inspect, host) for host in hosts]
            for future in futures:
                try:
                    item = future.result()
                except Exception:
                    continue
                if isinstance(item, dict) and item.get("host"):
                    found.append(item)
    found.sort(key=lambda item: ipaddress.IPv4Address(str(item["host"])))
    return {"subnet": str(network), "scanned": len(hosts), "found": found}
