"""Geometry helpers for controllers (spec 0004)."""

import math

from raceforge.control.types import LidarScan


def deg(rad: float) -> float:
    return math.degrees(rad)


def rad(degrees: float) -> float:
    return math.radians(degrees)


def wrap_angle(a: float) -> float:
    """Wrap to [-pi, pi)."""
    return (a + math.pi) % (2 * math.pi) - math.pi


def sector_min(scan: LidarScan, from_deg: float, to_deg: float) -> float | None:
    """Smallest valid range in the angular sector [from_deg, to_deg] (CCW, 0° = forward)."""
    lo, hi = math.radians(from_deg), math.radians(to_deg)
    best: float | None = None
    for a, r in zip(scan.angles_rad, scan.ranges_m, strict=True):
        if r is None:
            continue
        w = wrap_angle(a)
        inside = lo <= w <= hi if lo <= hi else (w >= lo or w <= hi)
        if inside and (best is None or r < best):
            best = r
    return best


def sector_mean(scan: LidarScan, from_deg: float, to_deg: float) -> float | None:
    lo, hi = math.radians(from_deg), math.radians(to_deg)
    vals = [
        r
        for a, r in zip(scan.angles_rad, scan.ranges_m, strict=True)
        if r is not None and lo <= wrap_angle(a) <= hi
    ]
    return sum(vals) / len(vals) if vals else None


def centering_error(left_m: float | None, right_m: float | None) -> float:
    """Positive when the car is closer to the right wall (should steer left)."""
    if left_m is None or right_m is None:
        return 0.0
    return (left_m - right_m) / 2


def wall_angle(scan: LidarScan, side: str, spread_deg: float = 20.0) -> float | None:
    """Angle of the wall on ``side`` ("left"/"right") relative to the heading (rad, + = left).

    Uses two beams, 90° ± spread/2, and the geometry of a straight wall.
    """
    sign = 1.0 if side == "left" else -1.0
    a1, a2 = 90.0 - spread_deg / 2, 90.0 + spread_deg / 2
    r1 = (
        sector_min(scan, sign * a1 - 1, sign * a1 + 1)
        if sign > 0
        else sector_min(scan, -a1 - 1, -a1 + 1)
    )
    r2 = (
        sector_min(scan, sign * a2 - 1, sign * a2 + 1)
        if sign > 0
        else sector_min(scan, -a2 - 1, -a2 + 1)
    )
    if r1 is None or r2 is None:
        return None
    t1, t2 = math.radians(a1), math.radians(a2)
    x1, y1 = r1 * math.cos(t1), r1 * math.sin(t1)
    x2, y2 = r2 * math.cos(t2), r2 * math.sin(t2)
    ang = math.atan2(y1 - y2, x1 - x2)
    return sign * ang
