from dataclasses import dataclass

from farm_core import Printer
from farm_errors import ErrorInfo

PRINT_STATE_MAP = {
    "printing": "printing",
    "paused": "paused",
    "error": "error",
}


@dataclass(frozen=True)
class PrinterView:
    key: str
    name: str
    host: str
    port: int
    mainsail_url: str
    online: bool
    state: str
    file: str
    progress: float
    layer: int
    layer_total: int
    eta_seconds: int | None
    nozzle: float
    bed: float
    filament: str
    thumb_version: str
    error: ErrorInfo | None
    last_seen: float


@dataclass(frozen=True)
class FarmSummary:
    printing: int
    paused: int
    idle: int
    error: int
    offline: int
    nearest_finish: int | None


def _as_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def build_view(printer: Printer, *, info, status, now, error, thumb_version) -> PrinterView:
    online = info is not None
    print_stats = (status or {}).get("print_stats") or {}
    if not online:
        state = "offline"
    elif info.get("state") in ("error", "shutdown") or status is None:
        state = "error"
    else:
        state = PRINT_STATE_MAP.get(print_stats.get("state"), "idle")

    vdisk = (status or {}).get("virtual_sdcard") or {}
    cur_print_data = vdisk.get("cur_print_data") or {}
    metadata = cur_print_data.get("metadata") or {}

    end_time = cur_print_data.get("end_time")
    eta_seconds = None
    if (
        state == "printing"
        and isinstance(end_time, (int, float))
        and not isinstance(end_time, bool)
        and end_time > now
    ):
        eta_seconds = int(end_time - now)

    return PrinterView(
        key=printer.key,
        name=printer.name,
        host=printer.host,
        port=printer.port,
        mainsail_url=printer.mainsail_url,
        online=online,
        state=state,
        file=print_stats.get("filename") or "",
        progress=min(max(_as_float(vdisk.get("progress")), 0.0), 1.0),
        layer=_as_int(vdisk.get("layer")),
        layer_total=_as_int(vdisk.get("layer_count")),
        eta_seconds=eta_seconds,
        nozzle=_as_float(((status or {}).get("extruder") or {}).get("temperature")),
        bed=_as_float(((status or {}).get("heater_bed") or {}).get("temperature")),
        filament=metadata.get("filament_type") or "",
        thumb_version=thumb_version,
        error=error,
        last_seen=now if online else 0,
    )


def summarize(views) -> FarmSummary:
    counts = {"printing": 0, "paused": 0, "idle": 0, "error": 0, "offline": 0}
    etas = []
    for view in views:
        counts[view.state] = counts.get(view.state, 0) + 1
        if view.state == "printing" and view.eta_seconds is not None:
            etas.append(view.eta_seconds)
    return FarmSummary(
        printing=counts["printing"],
        paused=counts["paused"],
        idle=counts["idle"],
        error=counts["error"],
        offline=counts["offline"],
        nearest_finish=min(etas) if etas else None,
    )
