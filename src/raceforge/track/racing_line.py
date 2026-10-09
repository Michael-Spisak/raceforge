"""A fast line through the corridor and a speed profile for it (spec 0029).

Minimum-curvature style without a QP solver: the centreline is shifted sideways (along its normals)
and smoothed iteratively; every iteration clamps the shift to the corridor minus a safety margin.
The speed profile limits lateral acceleration in curves and longitudinal acceleration/braking.
"""

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from raceforge.track.procedural import Corridor, normals_of

F = npt.NDArray[np.float64]


@dataclass(frozen=True)
class LineConfig:
    margin_m: float = 0.2  # half the car width + safety
    iterations: int = 800
    max_speed_m_s: float = 2.0
    lateral_accel_m_s2: float = 2.0
    accel_m_s2: float = 1.5
    brake_m_s2: float = 2.0


@dataclass(frozen=True)
class RacingLine:
    xy: F  # (N, 2)
    speed: F  # (N,)
    offset: F  # (N,) sideways shift from the centreline (+ = left)
    curvature: F  # (N,) signed, 1/m
    loop: bool


def curvature(xy: F, loop: bool) -> F:
    """Signed three-point curvature (1/m); open ends get their neighbour's value."""
    prev = np.roll(xy, 1, axis=0)
    nxt = np.roll(xy, -1, axis=0)
    a = np.linalg.norm(xy - prev, axis=1)
    b = np.linalg.norm(nxt - xy, axis=1)
    c = np.linalg.norm(nxt - prev, axis=1)
    cross = (xy[:, 0] - prev[:, 0]) * (nxt[:, 1] - xy[:, 1]) - (xy[:, 1] - prev[:, 1]) * (
        nxt[:, 0] - xy[:, 0]
    )
    k = np.where(a * b * c > 1e-12, 2 * cross / np.maximum(a * b * c, 1e-12), 0.0)
    if not loop and len(k) > 2:
        k[0], k[-1] = k[1], k[-2]
    return k


def _is_loop(corridor: Corridor) -> bool:
    c = corridor.centreline
    return bool(np.linalg.norm(c[0] - c[-1]) < 1.0) and len(c) > 8


def racing_line(corridor: Corridor, cfg: LineConfig | None = None) -> RacingLine:
    cfg = cfg or LineConfig()
    loop = _is_loop(corridor)
    centre = np.asarray(corridor.centreline, dtype=np.float64)
    if loop and np.linalg.norm(centre[0] - centre[-1]) < 1e-6:
        centre = centre[:-1]  # drop the duplicated closing sample
    widths = np.asarray(corridor.widths, dtype=np.float64)[: len(centre)]
    normals = normals_of(centre)
    bound = np.maximum(widths / 2 - cfg.margin_m, 0.0)
    e = np.zeros(len(centre))
    for _ in range(cfg.iterations):
        p = centre + normals * e[:, None]
        if loop:
            lap = np.roll(p, 1, axis=0) + np.roll(p, -1, axis=0) - 2 * p
        else:
            lap = np.zeros_like(p)
            lap[1:-1] = p[:-2] + p[2:] - 2 * p[1:-1]
        e = np.clip(e + 0.4 * (lap * normals).sum(axis=1), -bound, bound)
        if not loop:
            e[0] = e[-1] = 0.0  # start and finish on the centreline
    xy = centre + normals * e[:, None]
    k = curvature(xy, loop)
    speed = np.minimum(
        cfg.max_speed_m_s, np.sqrt(cfg.lateral_accel_m_s2 / np.maximum(np.abs(k), 1e-6))
    )
    ds = np.linalg.norm(np.roll(xy, -1, axis=0) - xy, axis=1)
    for _ in range(2 if loop else 1):  # twice round a loop so the passes wrap
        for i in range(1, len(xy)) if not loop else range(len(xy)):
            j = i - 1
            speed[i] = min(speed[i], float(np.sqrt(speed[j] ** 2 + 2 * cfg.accel_m_s2 * ds[j])))
        for i in range(len(xy) - 2, -1, -1) if not loop else range(len(xy) - 1, -1, -1):
            j = (i + 1) % len(xy)
            speed[i] = min(speed[i], float(np.sqrt(speed[j] ** 2 + 2 * cfg.brake_m_s2 * ds[i])))
    return RacingLine(xy=xy, speed=speed, offset=e, curvature=k, loop=loop)
