"""Summaries of recorded drives (spec 0027 run logs) for the MCP server and reports (spec 0028)."""

import gzip
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any


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
            row = json.loads(line)
        except ValueError:
            continue
        msg = row.get("msg") or {}
        kind = msg.get("type")
        if kind == "hello":
            car = msg.get("car")
        elif kind == "event":
            events += 1
        if kind != "telemetry":
            continue
        frame = msg.get("frame") or {}
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
        meas = frame.get("meas") or {}
        speed = _num(meas.get("speed_m_s"))
        speed_prev = speed if speed is not None else 0.0
        if speed is not None:
            speeds.append(speed)
        loop = frame.get("loop") or {}
        if (rate := _num(loop.get("rate_hz"))) is not None:
            rates.append(rate)
        if (miss := loop.get("deadline_misses")) is not None and isinstance(miss, int):
            misses.append(miss)
        power = frame.get("power") or {}
        volts = _num(power.get("ev3_battery_v")) or _num(power.get("motor_battery_v"))
        if volts is not None:
            battery.append(volts)
        faults.update(str(f) for f in frame.get("faults") or [])
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
