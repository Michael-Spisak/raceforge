"""Build the car's localisation map from a track (spec 0029).

Walls and static objects are rasterised at 5 cm; a jump-flooding distance transform gives every cell
the distance to the nearest wall (capped at 1.27 m, stored in cm); cells within the corridor get the
"drivable" bit. The result is a :class:`raceforge.control.localisation.DistanceMap`.
"""

import math

import numpy as np
import numpy.typing as npt

from raceforge.control.localisation import DIST_MASK, DRIVABLE, DistanceMap
from raceforge.track.procedural import Corridor

F = npt.NDArray[np.float64]
RESOLUTION_M = 0.05
MARGIN_M = 0.6


def _yaw(w: float, x: float, y: float, z: float) -> float:
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def _segments(corridor: Corridor) -> list[tuple[F, F]]:
    segs: list[tuple[F, F]] = []
    for wall in corridor.track.walls:
        pts = np.array([(p.x, p.y) for p in wall.points])
        segs += [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
        if np.linalg.norm(pts[0] - pts[-1]) < 0.6:  # a closed ring (loop track)
            segs.append((pts[-1], pts[0]))
    for obj in corridor.track.objects:
        if not obj.static:
            continue  # movable objects are not part of the map; the reactive layer handles them
        c = np.array([obj.pose.position.x, obj.pose.position.y])
        q = obj.pose.orientation
        yaw = _yaw(q.w, q.x, q.y, q.z)
        hx, hy = obj.size.x / 2, obj.size.y / 2
        rot = np.array([[math.cos(yaw), -math.sin(yaw)], [math.sin(yaw), math.cos(yaw)]])
        corners = [c + rot @ np.array(v) for v in ((hx, hy), (-hx, hy), (-hx, -hy), (hx, -hy))]
        segs += [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
    return segs


def _distance_transform(walls: npt.NDArray[np.bool_]) -> F:
    """Euclidean distance (in cells) to the nearest wall cell: jump flooding, vectorised."""
    h, w = walls.shape
    big = 1e9
    sy = np.where(walls, np.arange(h)[:, None] * np.ones((1, w)), big)
    sx = np.where(walls, np.ones((h, 1)) * np.arange(w)[None, :], big)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    step = 1 << max(0, math.ceil(math.log2(max(h, w))) - 1)
    while step >= 1:
        for dy in (-step, 0, step):
            for dx in (-step, 0, step):
                if dx == 0 and dy == 0:
                    continue
                cy = np.full((h, w), big)
                cx = np.full((h, w), big)
                ys = slice(max(0, -dy), h - max(0, dy))
                yd = slice(max(0, dy), h - max(0, -dy))
                xs = slice(max(0, -dx), w - max(0, dx))
                xd = slice(max(0, dx), w - max(0, -dx))
                cy[ys, xs] = sy[yd, xd]
                cx[ys, xs] = sx[yd, xd]
                better = (cy - yy) ** 2 + (cx - xx) ** 2 < (sy - yy) ** 2 + (sx - xx) ** 2
                sy = np.where(better, cy, sy)
                sx = np.where(better, cx, sx)
        step //= 2
    return np.sqrt((sy - yy) ** 2 + (sx - xx) ** 2)


def build_map(corridor: Corridor, resolution_m: float = RESOLUTION_M) -> DistanceMap:
    segs = _segments(corridor)
    if not segs:
        raise ValueError("the track has no walls to localise against")
    pts = np.array([p for s in segs for p in s])
    lo = pts.min(axis=0) - MARGIN_M
    hi = pts.max(axis=0) + MARGIN_M
    width = math.ceil((hi[0] - lo[0]) / resolution_m)
    height = math.ceil((hi[1] - lo[1]) / resolution_m)
    walls = np.zeros((height, width), dtype=bool)
    for a, b in segs:
        n = max(2, math.ceil(float(np.linalg.norm(b - a)) / (resolution_m / 3)))
        t = np.linspace(0.0, 1.0, n)[:, None]
        p = a + (b - a) * t
        cols = np.clip(((p[:, 0] - lo[0]) / resolution_m).astype(int), 0, width - 1)
        rows = np.clip(((p[:, 1] - lo[1]) / resolution_m).astype(int), 0, height - 1)
        walls[rows, cols] = True
    dist_cm = np.minimum(_distance_transform(walls) * resolution_m * 100.0, DIST_MASK)
    cells = np.round(dist_cm).astype(np.uint8)

    # drivable: within half the corridor width of the nearest centreline sample
    yy, xx = np.mgrid[0:height, 0:width]
    cx = lo[0] + (xx + 0.5) * resolution_m
    cy = lo[1] + (yy + 0.5) * resolution_m
    best = np.full((height, width), np.inf)
    half = np.zeros((height, width))
    for (px, py), wd in zip(corridor.centreline, corridor.widths, strict=True):
        d2 = (cx - px) ** 2 + (cy - py) ** 2
        closer = d2 < best
        best = np.where(closer, d2, best)
        half = np.where(closer, wd / 2, half)
    drivable = (np.sqrt(best) <= half) & ~walls
    cells = cells | np.where(drivable, DRIVABLE, 0).astype(np.uint8)
    return DistanceMap(cells, float(lo[0]), float(lo[1]), resolution_m)
