"""Calibration from recorded drives (spec 0033): pure functions over telemetry samples.

The Live tab records the frames of one wizard step (receive time + TelemetryFrame JSON) and sends
them here. Results are suggestions for fields of the car config (car.yaml) that the user applies.

- Straight drive: the user drives (teleop, steering 0) and measures the distance with a tape.
  ``drive_counts_per_m`` is scaled by measured/true distance; the heading drift over the distance
  gives the steering zero point (``ev3.steer_trim_rad``).
- Full-lock circle: radius R = speed / yaw rate in the steady part of the circle; the achieved
  wheel angle atan(L / R) against the commanded angle scales ``ev3.steer_motor_deg_per_rad``.
"""

import itertools
import math
from typing import Any

from raceforge.api.models import CalibrationResult, CalibrationSample


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def _series(samples: list[CalibrationSample]) -> list[tuple[float, float, float, float]]:
    """(t_s, speed, yaw_rate, commanded steering) for every sample that has them."""
    out: list[tuple[float, float, float, float]] = []
    for s in sorted(samples, key=lambda x: x.t_ms):
        meas: dict[str, Any] = s.frame.get("meas") or {}
        cmd: dict[str, Any] = s.frame.get("cmd") or {}
        v = _num(meas.get("speed_m_s"))
        w = _num(meas.get("yaw_rate_rad_s"))
        c = _num(cmd.get("steering_rad"))
        if v is not None and w is not None:
            out.append((s.t_ms / 1000.0, v, w, c if c is not None else 0.0))
    return out


def straight(
    samples: list[CalibrationSample],
    true_distance_m: float,
    drive_counts_per_m: float,
    steer_trim_rad: float,
    wheelbase_m: float,
) -> CalibrationResult:
    rows = _series(samples)
    if len(rows) < 10:
        raise ValueError("too few frames with speed and yaw rate: drive for a few seconds")
    distance = heading = 0.0
    for (t0, v0, w0, _), (t1, _v1, _w1, _c1) in itertools.pairwise(rows):
        dt = max(0.0, t1 - t0)
        distance += abs(v0) * dt
        heading += w0 * dt
    if distance < 0.5 or true_distance_m <= 0:
        raise ValueError("drive at least 0.5 m and enter the measured distance")
    counts = drive_counts_per_m * distance / true_distance_m
    curvature = heading / true_distance_m  # + = drifted left
    trim = steer_trim_rad - math.atan(wheelbase_m * curvature)
    return CalibrationResult(
        kind="straight",
        changes={"ev3.drive_counts_per_m": round(counts, 2), "ev3.steer_trim_rad": round(trim, 4)},
        details={
            "measured_distance_m": round(distance, 3),
            "true_distance_m": true_distance_m,
            "heading_drift_deg": round(math.degrees(heading), 2),
            "frames": len(rows),
        },
    )


def _circle_side(
    samples: list[CalibrationSample], name: str, wheelbase_m: float
) -> tuple[float, float, float]:
    rows = [r for r in _series(samples) if abs(r[1]) > 0.1 and abs(r[2]) > 0.05]
    if len(rows) < 10:
        raise ValueError(f"{name}: drive a full-lock circle for a few seconds")
    steady = rows[len(rows) // 4 :]  # skip the turn-in
    v = sum(abs(r[1]) for r in steady) / len(steady)
    w = sum(abs(r[2]) for r in steady) / len(steady)
    cmd = sum(abs(r[3]) for r in steady) / len(steady)
    radius = v / w
    return radius, math.atan(wheelbase_m / radius), cmd


def circle(
    left: list[CalibrationSample],
    right: list[CalibrationSample],
    wheelbase_m: float,
    steer_motor_deg_per_rad: float,
) -> CalibrationResult:
    r_left, a_left, c_left = _circle_side(left, "left", wheelbase_m)
    r_right, a_right, c_right = _circle_side(right, "right", wheelbase_m)
    if min(c_left, c_right) <= 0:
        raise ValueError("no steering command recorded: steer to full lock while driving")
    ratio = (c_left + c_right) / (a_left + a_right)
    return CalibrationResult(
        kind="circle",
        changes={"ev3.steer_motor_deg_per_rad": round(steer_motor_deg_per_rad * ratio, 2)},
        details={
            "radius_left_m": round(r_left, 3),
            "radius_right_m": round(r_right, 3),
            "achieved_left_deg": round(math.degrees(a_left), 1),
            "achieved_right_deg": round(math.degrees(a_right), 1),
            "commanded_deg": round(math.degrees((c_left + c_right) / 2), 1),
            "asymmetry_deg": round(math.degrees(a_left - a_right), 1),
        },
    )
