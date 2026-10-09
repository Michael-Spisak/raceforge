"""Seeded procedural corridor generator -> core.Track (spec 0003).

Track conventions used by RaceForge tracks:
- Wall polylines are oriented so that the free corridor space lies on their LEFT.
- The race setup's ``checkpoints`` are corridor cross-sections every 0.5 m along the centreline;
  their midpoints form the centreline used for progress measurement.
"""

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field, model_validator

from raceforge.core.primitives import Pose, Pose2D, Quat, Segment2D, Vec2, Vec3
from raceforge.core.track import (
    ClassDef,
    DoorState,
    Polygon2D,
    RaceSetup,
    RandomRange,
    SurfaceRegion,
    Track,
    TrackObject,
    Wall,
)

type Arr = npt.NDArray[np.float64]
SAMPLE_M = 0.25
CHECKPOINT_M = 0.5

BUILTIN_CLASSES = [
    ClassDef(id="door", name="Door leaf", builtin=True, material="wood", lidar_reflectivity=0.6),
    ClassDef(id="pillar", name="Pillar", builtin=True, material="concrete", lidar_reflectivity=0.8),
    ClassDef(id="bin", name="Trash bin", builtin=True, material="plastic", lidar_reflectivity=0.5),
    ClassDef(id="bench", name="Bench", builtin=True, material="wood", lidar_reflectivity=0.6),
]


class CorridorParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    seed: int = 0
    loop: bool = True
    length_m: float = Field(default=60.0, ge=20.0, le=120.0)
    width_min_m: float = Field(default=1.4, ge=0.8)
    width_max_m: float = Field(default=2.4, le=4.0)
    jog_rate_per_10m: float = Field(default=0.25, ge=0)
    niche_rate_per_10m: float = Field(default=1.0, ge=0)
    door_rate_per_10m: float = Field(default=0.8, ge=0)
    pillar_rate_per_10m: float = Field(default=0.4, ge=0)
    object_rate_per_10m: float = Field(default=0.6, ge=0)
    glass_prob: float = Field(default=0.15, ge=0, le=1)
    wall_height_m: float = Field(default=2.5, gt=0)
    friction_min: float = Field(default=0.6, gt=0)
    friction_max: float = Field(default=0.9, gt=0)

    @model_validator(mode="after")
    def _check(self) -> "CorridorParams":
        if self.width_max_m < self.width_min_m:
            raise ValueError("width_max_m must be >= width_min_m")
        return self


@dataclass(frozen=True)
class Corridor:
    track: Track
    centreline: Arr  # (N, 2) samples every SAMPLE_M
    widths: Arr  # (N,) nominal corridor width
    s: Arr  # (N,) arc length


class GenerationError(ValueError):
    pass


def fillet(points: Arr, radius: float, closed: bool) -> Arr:
    """Round every interior corner of a polyline with an arc of ``radius`` and resample."""
    pts = points if not closed else points[:-1]
    n = len(pts)
    out: list[Arr] = []
    indices = range(n) if closed else range(1, n - 1)
    if not closed:
        out.append(pts[0])
    for i in indices:
        p0, p1, p2 = pts[(i - 1) % n], pts[i], pts[(i + 1) % n]
        d1 = (p1 - p0) / np.linalg.norm(p1 - p0)
        d2 = (p2 - p1) / np.linalg.norm(p2 - p1)
        turn = math.atan2(d1[0] * d2[1] - d1[1] * d2[0], float(d1 @ d2))
        if abs(turn) < 1e-6:
            out.append(p1)
            continue
        cut = radius * math.tan(abs(turn) / 2)
        limit = 0.45 * min(np.linalg.norm(p1 - p0), np.linalg.norm(p2 - p1))
        cut = min(cut, limit)
        r = cut / math.tan(abs(turn) / 2)
        a, b = p1 - d1 * cut, p1 + d2 * cut
        normal = np.array([-d1[1], d1[0]]) * math.copysign(1.0, turn)
        centre = a + normal * r
        start = math.atan2(a[1] - centre[1], a[0] - centre[0])
        steps = max(2, int(abs(turn) * r / 0.05))
        for k in range(steps + 1):
            ang = start + turn * k / steps
            out.append(centre + r * np.array([math.cos(ang), math.sin(ang)]))
        _ = b
    if not closed:
        out.append(pts[-1])
    else:
        out.append(out[0])
    return np.array(out)


def resample(points: Arr, step: float) -> tuple[Arr, Arr]:
    seg = np.linalg.norm(np.diff(points, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(s[-1])
    n = max(2, int(total / step))
    s_new = np.linspace(0.0, total, n + 1)
    xy = np.column_stack([np.interp(s_new, s, points[:, 0]), np.interp(s_new, s, points[:, 1])])
    return xy, s_new


def _loop_skeleton(p: CorridorParams, rng: np.random.Generator) -> Arr:
    aspect = rng.uniform(0.2, 0.5)
    a = p.length_m / 2 / (1 + aspect)
    b = a * aspect
    pts: list[tuple[float, float]] = [(0.0, 0.0)]
    corners = [(a, 0.0), (a, b), (0.0, b), (0.0, 0.0)]
    x, y = 0.0, 0.0
    for cx, cy in corners:
        length = math.hypot(cx - x, cy - y)
        dx, dy = (cx - x) / length, (cy - y) / length
        n_jogs = rng.poisson(p.jog_rate_per_10m * length / 10)
        if length > 12 and n_jogs:
            pos = sorted(rng.uniform(4, length - 8, size=min(n_jogs, 2)))
            for s0 in pos:
                off = rng.choice([-1, 1]) * rng.uniform(0.4, min(1.2, b / 4))
                run = rng.uniform(1.5, 3.0)
                nx, ny = -dy, dx
                base = (x + dx * s0, y + dy * s0)
                pts += [
                    base,
                    (base[0] + dx * run + nx * off, base[1] + dy * run + ny * off),
                    (base[0] + dx * (run + 3) + nx * off, base[1] + dy * (run + 3) + ny * off),
                    (base[0] + dx * (2 * run + 3), base[1] + dy * (2 * run + 3)),
                ]
        pts.append((cx, cy))
        x, y = cx, cy
    return np.array(pts)


def _open_skeleton(p: CorridorParams, rng: np.random.Generator) -> Arr:
    pts = [np.zeros(2)]
    heading = 0.0
    remaining = p.length_m
    while remaining > 0:
        first = len(pts) == 1
        run = min(
            remaining, rng.uniform(9.0, 14.0) if first else rng.uniform(4.0, 12.0)
        )  # room for the grid
        pts.append(pts[-1] + run * np.array([math.cos(heading), math.sin(heading)]))
        remaining -= run
        heading += float(rng.choice([-1, 1])) * (
            math.pi / 2 if rng.random() < 0.6 else rng.uniform(0.25, 0.8)
        )
    return np.array(pts)


def normals_of(xy: Arr) -> Arr:
    t = np.gradient(xy, axis=0)
    t /= np.linalg.norm(t, axis=1, keepdims=True)
    return np.column_stack([-t[:, 1], t[:, 0]])


def _segments_intersect(a: Arr, b: Arr, skip_adjacent: bool) -> bool:
    """True if any segment of polyline ``a`` crosses any segment of polyline ``b``."""
    p, r = a[:-1], np.diff(a, axis=0)
    q, s = b[:-1], np.diff(b, axis=0)
    rxs = r[:, None, 0] * s[None, :, 1] - r[:, None, 1] * s[None, :, 0]
    qp = q[None, :, :] - p[:, None, :]
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (qp[..., 0] * s[None, :, 1] - qp[..., 1] * s[None, :, 0]) / rxs
        u = (qp[..., 0] * r[:, None, 1] - qp[..., 1] * r[:, None, 0]) / rxs
    hit = (np.abs(rxs) > 1e-12) & (t > 1e-9) & (t < 1 - 1e-9) & (u > 1e-9) & (u < 1 - 1e-9)
    if skip_adjacent:
        i, j = np.indices(hit.shape)
        n = hit.shape[0]
        hit &= (np.abs(i - j) > 2) & ~((i < 3) & (j > n - 4)) & ~((j < 3) & (i > n - 4))
    return bool(hit.any())


def pose2(xy: Arr, z: float, yaw: float) -> Pose:
    x, y = float(xy[0]), float(xy[1])
    h = yaw / 2
    return Pose(
        position=Vec3(x=x, y=y, z=z), orientation=Quat(w=math.cos(h), x=0.0, y=0.0, z=math.sin(h))
    )


def generate_corridor(params: CorridorParams, max_attempts: int = 20) -> Corridor:
    """Generate a valid corridor deterministically from ``params.seed``."""
    for attempt in range(max_attempts):
        rng = np.random.default_rng([params.seed, attempt])
        try:
            return _generate(params, rng)
        except GenerationError:
            continue
    raise GenerationError(f"no valid corridor for seed {params.seed} after {max_attempts} attempts")


def _generate(p: CorridorParams, rng: np.random.Generator) -> Corridor:
    skeleton = _loop_skeleton(p, rng) if p.loop else _open_skeleton(p, rng)
    radius = p.width_max_m / 2 + 0.4
    centre, s = resample(fillet(skeleton, radius, p.loop), SAMPLE_M)
    if p.loop:
        centre[-1] = centre[0]
    n = len(centre)
    normals = normals_of(centre)
    if p.loop:
        normals[-1] = normals[0]

    # Piecewise-constant widths with 1 m linear transitions.
    knots = np.arange(0.0, s[-1] + 8.0, rng.uniform(6.0, 14.0))
    widths_k = rng.uniform(p.width_min_m, p.width_max_m, size=len(knots))
    if p.loop:
        widths_k[-1] = widths_k[0]
    widths = np.interp(s, knots, widths_k)

    # Niches/doors: extra depth per side.
    depth = np.zeros((2, n))  # 0 = left, 1 = right
    objects: list[TrackObject] = []
    door_ids: list[str] = []
    glass: list[SurfaceRegion] = []
    curvature = np.abs(np.gradient(np.unwrap(np.arctan2(normals[:, 1], normals[:, 0])))) / SAMPLE_M
    straight = curvature < 0.05
    margin = 3.0

    occupied: list[tuple[float, float]] = []  # s-intervals used by doors/pillars/objects
    clearance = 1.5  # keep features apart along the corridor so it stays passable

    def free_spot(length: float) -> int | None:
        for _ in range(30):
            i = int(rng.integers(0, n))
            lo, hi = s[i] - length / 2, s[i] + length / 2
            if lo < margin or hi > s[-1] - margin:
                continue
            idx = (s >= lo) & (s <= hi)
            if not straight[idx].all() or (depth[:, idx] > 0).any():
                continue
            if any(lo - clearance < b and a < hi + clearance for a, b in occupied):
                continue
            occupied.append((lo, hi))
            return i
        return None

    def wall_point(i: int, side: int, extra: float = 0.0) -> Arr:
        sign = 1.0 if side == 0 else -1.0
        return centre[i] + sign * normals[i] * (widths[i] / 2 + extra)

    def heading(i: int) -> float:
        return math.atan2(-normals[i][0], normals[i][1])

    length_10 = s[-1] / 10
    for _ in range(rng.poisson(p.niche_rate_per_10m * length_10)):
        i = free_spot(rng.uniform(0.8, 2.0))
        if i is not None:
            span = np.abs(s - s[i]) <= rng.uniform(0.4, 1.0)
            depth[int(rng.integers(0, 2)), span] = rng.uniform(0.2, 0.8)
    for k in range(rng.poisson(p.door_rate_per_10m * length_10)):
        i = free_spot(1.1)
        if i is None:
            continue
        side = int(rng.integers(0, 2))
        span = np.abs(s - s[i]) <= 0.5
        depth[side, span] = 0.15
        hinge = wall_point(i, side, 0.15)
        oid = f"door-{k + 1}"
        objects.append(
            TrackObject(
                id=oid,
                class_id="door",
                pose=pose2(hinge, 1.0, heading(i)),
                size=Vec3(x=0.9, y=0.04, z=2.0),
                static=True,
            )
        )
        door_ids.append(oid)
    for k in range(rng.poisson(p.pillar_rate_per_10m * length_10)):
        i = free_spot(0.6)
        if i is None:
            continue
        side = int(rng.integers(0, 2))
        c = wall_point(i, side, -0.15)  # pillar centre 0.15 m inside the wall: protrudes 0.3 m
        objects.append(
            TrackObject(
                id=f"pillar-{k + 1}",
                class_id="pillar",
                pose=pose2(c, 1.25, heading(i)),
                size=Vec3(x=0.3, y=0.3, z=2.5),
            )
        )
    for k in range(rng.poisson(p.object_rate_per_10m * length_10)):
        i = free_spot(1.4)
        if i is None:
            continue
        side = int(rng.integers(0, 2))
        cls, size = (
            ("bin", (0.35, 0.35, 0.6)) if rng.random() < 0.6 else ("bench", (1.2, 0.4, 0.45))
        )
        c = centre[i] + (1.0 if side == 0 else -1.0) * normals[i] * (
            widths[i] / 2 - size[1] / 2 - 0.02
        )
        objects.append(
            TrackObject(
                id=f"{cls}-{k + 1}",
                class_id=cls,
                pose=pose2(c, size[2] / 2, heading(i)),
                size=Vec3(x=size[0], y=size[1], z=size[2]),
                static=False,
                randomisation=RandomRange(position_xy_m=0.3, yaw_rad=0.3, presence_prob=0.7),
            )
        )

    left = centre + normals * (widths / 2 + depth[0])[:, None]
    right = centre - normals * (widths / 2 + depth[1])[:, None]

    # Glass wall stretches (thin floor regions hugging the wall).
    for side, wall in ((0, left), (1, right)):
        i = 0
        while i < n - 1:
            if rng.random() < p.glass_prob * SAMPLE_M / 4:
                j = min(n - 1, i + int(rng.uniform(2.0, 5.0) / SAMPLE_M))
                sign = -1.0 if side == 0 else 1.0
                inner = wall[i : j + 1] + sign * normals[i : j + 1] * 0.08
                outer = wall[i : j + 1] - sign * normals[i : j + 1] * 0.08
                poly = np.vstack([outer, inner[::-1]])
                glass.append(
                    SurfaceRegion(
                        polygon=Polygon2D(points=[Vec2(x=float(a), y=float(b)) for a, b in poly]),
                        friction=1.0,
                        material="glass",
                        lidar_reflectivity=0.1,
                    )
                )
                i = j + int(4.0 / SAMPLE_M)
            i += 1

    friction = float(rng.uniform(p.friction_min, p.friction_max))
    return assemble_corridor(
        centre,
        s,
        normals,
        widths,
        depth,
        straight,
        objects=objects,
        glass=glass,
        door_ids=door_ids,
        loop=p.loop,
        width_min_m=p.width_min_m,
        wall_height_m=p.wall_height_m,
        friction=friction,
        note=f"procedural corridor seed={p.seed} loop={p.loop}",
    )


def assemble_corridor(
    centre: Arr,
    s: Arr,
    normals: Arr,
    widths: Arr,
    depth: Arr,
    straight: npt.NDArray[np.bool_],
    *,
    objects: list[TrackObject],
    glass: list[SurfaceRegion],
    door_ids: list[str],
    loop: bool,
    width_min_m: float,
    wall_height_m: float,
    friction: float,
    note: str,
    straight_start: bool = True,
) -> Corridor:
    """Walls, floor, checkpoints, start grid and race setup around a sampled centreline (shared by
    the procedural generator and drawn quick tracks). Raises :class:`GenerationError` if invalid."""
    n = len(centre)
    left = centre + normals * (widths / 2 + depth[0])[:, None]
    right = centre - normals * (widths / 2 + depth[1])[:, None]
    # Validation.
    if (widths < width_min_m - 1e-9).any():
        raise GenerationError("corridor narrower than width_min_m")
    if _segments_intersect(left, left, True) or _segments_intersect(right, right, True):
        raise GenerationError("wall self-intersection")
    if _segments_intersect(left, right, False):
        raise GenerationError("walls cross each other")

    walls = [
        Wall(points=[Vec2(x=float(x), y=float(y)) for x, y in left[::-1]], height_m=wall_height_m),
        Wall(points=[Vec2(x=float(x), y=float(y)) for x, y in right], height_m=wall_height_m),
    ]
    floor_pts = left[:-1] if loop else np.vstack([left, right[::-1]])
    floor = Polygon2D(points=[Vec2(x=float(x), y=float(y)) for x, y in floor_pts])

    cps = [i for i in range(n) if (s[i] % CHECKPOINT_M) < SAMPLE_M / 2 or i == n - 1]
    checkpoints = [
        Segment2D(
            a=Vec2(x=float(right[i][0]), y=float(right[i][1]))
            if depth[1, i] == 0
            else Vec2(
                x=float(centre[i][0] - normals[i][0] * widths[i] / 2),
                y=float(centre[i][1] - normals[i][1] * widths[i] / 2),
            ),
            b=Vec2(
                x=float(centre[i][0] + normals[i][0] * widths[i] / 2),
                y=float(centre[i][1] + normals[i][1] * widths[i] / 2),
            ),
        )
        for i in cps
    ]
    # Start on a straight with >= 2.5 m of straight corridor before and after the line.
    window = int(2.5 / SAMPLE_M)
    candidates = [
        i for i in cps if window <= i < n - window and straight[i - window : i + window].all()
    ]
    if not candidates and not straight_start:
        # Drawn tracks: the straightest checkpoint with room for the grid behind it.
        curv = np.abs(np.gradient(np.unwrap(np.arctan2(normals[:, 1], normals[:, 0]))))
        inner = [i for i in cps if window <= i < n - window]
        if inner:
            candidates = [min(inner, key=lambda i: float(curv[i - window : i + window].sum()))]
    if not candidates:
        raise GenerationError("no straight section for the start grid")
    i0 = candidates[0]
    start = checkpoints[cps.index(i0)]
    finish = start if loop else checkpoints[-4]
    t0 = np.gradient(centre, axis=0)[i0]
    t0 = t0 / np.linalg.norm(t0)
    grid: list[Pose2D] = []
    yaw = math.atan2(t0[1], t0[0])
    for row in range(3):
        for col in (-1, 1):
            back = 0.5 + 0.5 * row
            g = centre[i0] - t0 * back + normals[i0] * col * widths[i0] / 4
            grid.append(Pose2D(x=float(g[0]), y=float(g[1]), theta=yaw))
    setup = RaceSetup(
        id="main",
        name="Generated race",
        start_line=start,
        finish_line=finish,
        direction=Vec2(x=float(t0[0]), y=float(t0[1])),
        laps=3 if loop else 1,
        start_grid=grid,
        checkpoints=checkpoints,
        door_states=dict.fromkeys(door_ids, DoorState.RANDOM),
    )
    track = Track(
        frame_origin_note=note,
        floor=floor,
        walls=walls,
        objects=objects,
        surfaces=[
            SurfaceRegion(
                polygon=floor, friction=friction, material="floor", lidar_reflectivity=0.8
            ),
            *glass,
        ],
        race_setups=[setup],
        classes=BUILTIN_CLASSES,
    )
    return Corridor(track=track, centreline=centre, widths=widths, s=s)
