"""Recorded drives (spec 0027 run logs): summaries (spec 0028) and replay timelines (spec 0034)."""

import gzip
import json
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from raceforge.workspace.sync import Workspace


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def summarize(lines: Iterable[str], notes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """One pass over a ``telemetry.jsonl.gz`` run log: speed, distance, loop, battery, states."""
    frames = events = 0
    t0 = t_prev = None
    state_prev: str | None = None
    speed_prev = 0.0
    distance = 0.0
    speeds: list[float] = []
    rates: list[float] = []
    misses: list[int] = []
    battery: list[float] = []
    faults: set[str] = set()
    state_s: dict[str, float] = {}
    car = None
    for line in lines:
        try:
            row: Any = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        row = cast(dict[str, Any], row)
        msg: Any = row.get("msg") or {}
        kind = msg.get("type")
        if kind == "hello":
            car = msg.get("car")
        elif kind == "event":
            events += 1
        if kind != "telemetry":
            continue
        frame: Any = msg.get("frame") or {}
        t = _num(row.get("rx_t"))
        if t is None:
            continue
        frames += 1
        if t0 is None:
            t0 = t
        if t_prev is not None:
            dt = max(0.0, t - t_prev)
            distance += abs(speed_prev) * dt
            if state_prev is not None:
                state_s[state_prev] = state_s.get(state_prev, 0.0) + dt
        t_prev = t
        state_prev = str(frame.get("state") or "-")
        meas: Any = frame.get("meas") or {}
        speed = _num(meas.get("speed_m_s"))
        speed_prev = speed if speed is not None else 0.0
        if speed is not None:
            speeds.append(speed)
        loop: Any = frame.get("loop") or {}
        if (rate := _num(loop.get("rate_hz"))) is not None:
            rates.append(rate)
        if (miss := loop.get("deadline_misses")) is not None and isinstance(miss, int):
            misses.append(miss)
        power: Any = frame.get("power") or {}
        volts = _num(power.get("ev3_battery_v")) or _num(power.get("motor_battery_v"))
        if volts is not None:
            battery.append(volts)
        fault_list: list[Any] = frame.get("faults") or []
        faults.update(str(f) for f in fault_list)
    duration = (t_prev - t0) if t0 is not None and t_prev is not None else 0.0
    return {
        "car": car,
        "frames": frames,
        "events": events,
        "duration_s": round(duration, 2),
        "distance_m": round(distance, 2),
        "speed_mean_m_s": round(sum(speeds) / len(speeds), 3) if speeds else None,
        "speed_max_m_s": round(max(speeds), 3) if speeds else None,
        "loop_rate_mean_hz": round(sum(rates) / len(rates), 1) if rates else None,
        "loop_rate_min_hz": round(min(rates), 1) if rates else None,
        "deadline_misses": (max(misses) - min(misses)) if misses else None,
        "battery_start_v": round(battery[0], 2) if battery else None,
        "battery_min_v": round(min(battery), 2) if battery else None,
        "faults": sorted(faults),
        "state_seconds": {k: round(v, 2) for k, v in sorted(state_s.items())},
        "notes": notes or [],
    }


def summarize_file(path: Path, notes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return summarize(f, notes)


def compare(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Both summaries and ``b - a`` for every number they share."""
    diff = {
        k: round(b[k] - a[k], 3)
        for k in a
        if isinstance(a[k], int | float)
        and isinstance(b.get(k), int | float)
        and not isinstance(a[k], bool)
    }
    return {"a": a, "b": b, "b_minus_a": diff}


def timeline(
    lines: Iterable[str], notes: list[dict[str, Any]] | None = None, max_hz: float = 10.0
) -> dict[str, Any]:
    """Frames for the replay player (spec 0034), thinned to ``max_hz``; times in seconds from the
    first frame. Events are kept in full; ``moved_s`` is when the car first moved (for syncing
    two drives in a comparison)."""
    period = 1.0 / max(0.5, min(max_hz, 50.0))
    t0: float | None = None
    last = -1e9
    times: list[float] = []
    frames: list[Any] = []
    events: list[dict[str, Any]] = []
    car: str | None = None
    moved: float | None = None
    for line in lines:
        try:
            row: Any = json.loads(line)
        except ValueError:
            continue
        msg: Any = row.get("msg") or {}
        t = _num(row.get("rx_t"))
        if t is None:
            continue
        kind = msg.get("type")
        if kind == "hello":
            car = str(msg.get("car") or "")
        if kind not in ("telemetry", "event"):
            continue
        if t0 is None:
            t0 = t
        rel = round(t - t0, 3)
        if kind == "event":
            events.append(
                {"t": rel, "kind": str(msg.get("kind", "")), "detail": str(msg.get("detail", ""))}
            )
            continue
        frame: Any = msg.get("frame") or {}
        meas: Any = frame.get("meas") or {}
        speed = _num(meas.get("speed_m_s"))
        if moved is None and speed is not None and abs(speed) > 0.05:
            moved = rel
        if rel - last >= period - 1e-6:  # float steps: 0.1 - 0.0 may be 0.0999…
            last = rel
            times.append(rel)
            frames.append(frame)
    marks: list[dict[str, Any]] = []
    for n in notes or []:
        nt = _num(n.get("rx_t"))
        if t0 is not None and nt is not None:
            marks.append({"t": round(nt - t0, 3), "text": str(n.get("text", ""))})
    return {
        "car": car,
        "duration_s": times[-1] if times else 0.0,
        "moved_s": moved,
        "t": times,
        "frames": frames,
        "events": events,
        "notes": marks,
    }


def timeline_file(
    path: Path, notes: list[dict[str, Any]] | None = None, max_hz: float = 10.0
) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return timeline(f, notes, max_hz)


def run_log(ws: "Workspace", run: str) -> tuple[Path, list[dict[str, Any]]]:
    """The telemetry file and notes of a workspace ``run`` object (blobs fetched on demand)."""
    from raceforge.backend.models import FileSetContent

    obj = next((o for o in ws.objects("run") if o.slug == run), None)
    if obj is None or obj.latest is None:
        raise ValueError(f"no run {run!r}")
    files = FileSetContent.model_validate(ws.version_content(obj.latest.id)).files
    by_name = {f.path: f for f in files}
    log = by_name.get("telemetry.jsonl.gz")
    if log is None:
        raise ValueError(f"run {run!r} has no telemetry.jsonl.gz (a race result, not a drive)")
    notes: list[dict[str, Any]] = []
    if "notes.json" in by_name:
        notes = json.loads(ws.blob(by_name["notes.json"].sha256).read_text("utf-8"))
    return ws.blob(log.sha256), notes
