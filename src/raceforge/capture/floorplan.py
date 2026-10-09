"""2D floor plan of a scanned corridor (spec 0024): what a car-height slice of the scan blocks.

The ARKit mesh (RaceForge frame, Z up, metres) is cut to a height band above the floor (default
5-50 cm: bumpers, LiDAR and ultrasonic height) and projected onto a 5 cm occupancy grid. Floor and
ceiling triangles are ignored; walls, doors, windows and furniture count. The grid is the underlay
for drawing quick tracks on the real corridor and for measuring its width.
"""

import math
import struct
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

import numpy as np
from numpy.typing import NDArray

from raceforge.capture.tscan import ARKIT_CLASSES, Mesh

FLOOR = ARKIT_CLASSES.index("floor")
CEILING = ARKIT_CLASSES.index("ceiling")
MAX_SAMPLES = 4_000_000


@dataclass(frozen=True)
class Floorplan:
    origin: tuple[float, float]  # world (x, y) of the grid's lower-left corner
    resolution: float  # metres per cell
    occupied: NDArray[np.bool_]  # [row = y, col = x]
    floor_z: float

    @property
    def width(self) -> int:
        return int(self.occupied.shape[1])

    @property
    def height(self) -> int:
        return int(self.occupied.shape[0])

    def cell(self, x: float, y: float) -> tuple[int, int] | None:
        c = math.floor((x - self.origin[0]) / self.resolution)
        r = math.floor((y - self.origin[1]) / self.resolution)
        return (r, c) if 0 <= r < self.height and 0 <= c < self.width else None

    def blocked(self, x: float, y: float) -> bool:
        rc = self.cell(x, y)
        return rc is not None and bool(self.occupied[rc])


def _floor_level(meshes: Sequence[Mesh]) -> float:
    floor = [m.vertices[m.faces[m.classification == FLOOR]].reshape(-1, 3)[:, 2] for m in meshes]
    zs = np.concatenate(floor) if floor else np.zeros(0)
    if len(zs) >= 30:
        return float(np.median(zs))
    allz = np.concatenate([m.vertices[:, 2] for m in meshes]) if meshes else np.zeros(1)
    return float(np.percentile(allz, 2))


def _sample_faces(tri: NDArray[np.float64], res: float) -> NDArray[np.float64]:
    """Points on the triangles, about two per grid cell of their area (vertices included)."""
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    counts = np.minimum(np.ceil(area / (res * res) * 2).astype(int), 400)
    total = int(counts.sum())
    if total > MAX_SAMPLES:  # huge scans: thin out evenly
        counts = np.maximum((counts * MAX_SAMPLES / total).astype(int), 1)
    idx = np.repeat(np.arange(len(tri)), counts)
    rng = np.random.default_rng(0)
    u, v = rng.random(len(idx)), rng.random(len(idx))
    flip = u + v > 1
    u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
    pts = a[idx] + (b[idx] - a[idx]) * u[:, None] + (c[idx] - a[idx]) * v[:, None]
    return np.concatenate([pts, tri.reshape(-1, 3)])


def floorplan(
    meshes: Sequence[Mesh],
    resolution: float = 0.05,
    z_min: float = 0.05,
    z_max: float = 0.5,
    margin: float = 0.5,
) -> Floorplan:
    """Occupancy of the band ``floor + z_min … floor + z_max`` from the scan's meshes."""
    floor_z = _floor_level(meshes)
    tris = [
        m.vertices[m.faces[(m.classification != FLOOR) & (m.classification != CEILING)]]
        for m in meshes
    ]
    tri = np.concatenate(tris) if tris else np.zeros((0, 3, 3))
    lo_z, hi_z = floor_z + z_min, floor_z + z_max
    keep = (tri[:, :, 2].max(axis=1) >= lo_z) & (tri[:, :, 2].min(axis=1) <= hi_z)
    pts = _sample_faces(tri[keep], resolution) if keep.any() else np.zeros((0, 3))
    pts = pts[(pts[:, 2] >= lo_z) & (pts[:, 2] <= hi_z)]
    allv = np.concatenate([m.vertices for m in meshes]) if meshes else np.zeros((1, 3))
    x0, y0 = allv[:, 0].min() - margin, allv[:, 1].min() - margin
    x1, y1 = allv[:, 0].max() + margin, allv[:, 1].max() + margin
    w = max(1, math.ceil((x1 - x0) / resolution))
    h = max(1, math.ceil((y1 - y0) / resolution))
    grid = np.zeros((h, w), dtype=bool)
    if len(pts):
        cols = np.clip(((pts[:, 0] - x0) / resolution).astype(int), 0, w - 1)
        rows = np.clip(((pts[:, 1] - y0) / resolution).astype(int), 0, h - 1)
        grid[rows, cols] = True
    return Floorplan((float(x0), float(y0)), resolution, grid, floor_z)


def png_rgba(fp: Floorplan, rgb: tuple[int, int, int] = (128, 132, 140)) -> bytes:
    """The occupied cells as a PNG (row 0 = top = highest y), free cells transparent."""
    img = np.zeros((fp.height, fp.width, 4), dtype=np.uint8)
    occ = fp.occupied[::-1]
    img[occ] = (*rgb, 230)
    raw = b"".join(b"\x00" + img[r].tobytes() for r in range(fp.height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    ihdr = struct.pack(">IIBBBBB", fp.width, fp.height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def corridor_widths(
    fp: Floorplan, points: Sequence[tuple[float, float]], step: float = 0.25, max_half: float = 3.0
) -> list[float]:
    """Free width perpendicular to the polyline every ``step`` metres (wall to wall; ``inf`` when
    one side is open within ``max_half``)."""
    out: list[float] = []
    for (ax, ay), (bx, by) in pairwise(points):
        seg = math.hypot(bx - ax, by - ay)
        if seg < 1e-9:
            continue
        tx, ty = (bx - ax) / seg, (by - ay) / seg
        nx, ny = -ty, tx
        n = max(1, int(seg / step))
        for k in range(n):
            px, py = ax + tx * seg * (k + 0.5) / n, ay + ty * seg * (k + 0.5) / n
            if fp.blocked(px, py):
                continue
            sides = []
            for sign in (1.0, -1.0):
                d, hit = 0.0, math.inf
                while d < max_half:
                    d += fp.resolution / 2
                    if fp.blocked(px + sign * nx * d, py + sign * ny * d):
                        hit = d
                        break
                sides.append(hit)
            out.append(sides[0] + sides[1])
    return out
