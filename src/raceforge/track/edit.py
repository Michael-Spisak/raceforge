"""Track editor v1 (spec 0020): non-destructive edit layer on a built corridor + validator.

The layer uses plain ``(x, y)`` tuples (m); :func:`apply_edit` converts them to the core models.
Removing the layer restores the unedited track exactly.
"""

import math
from typing import Annotated, Literal

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from raceforge.core.primitives import Pose2D, Segment2D, Vec2, Vec3
from raceforge.core.track import (
    CheckMeasurement,
    ClassDef,
    Polygon2D,
    RandomRange,
    SurfaceRegion,
    TrackObject,
)
from raceforge.track.procedural import Corridor, normals_of, pose2

XY = tuple[float, float]
Line = tuple[XY, XY]

# Object library (kind → default size x, y, z in m). Kinds without a builtin class get one on apply.
LIBRARY: dict[str, tuple[float, float, float]] = {
    "box": (0.4, 0.4, 0.4),
    "cone": (0.25, 0.25, 0.4),
    "bin": (0.35, 0.35, 0.6),
    "pillar": (0.3, 0.3, 2.5),
    "bench": (1.2, 0.4, 0.45),
    "opponent": (0.3, 0.2, 0.15),
}
EXTRA_CLASSES = {
    "box": ClassDef(
        id="box", name="Box", builtin=True, material="cardboard", lidar_reflectivity=0.6
    ),
    "cone": ClassDef(
        id="cone", name="Cone", builtin=True, material="plastic", lidar_reflectivity=0.5
    ),
    "opponent": ClassDef(
        id="opponent", name="Opponent car", builtin=True, material="plastic", lidar_reflectivity=0.5
    ),
}
GRID_SPACING_LONG_M = 0.7
GRID_SPACING_LAT_M = 0.5
GRID_FIRST_ROW_BACK_M = 0.5


class EditRandom(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    position_xy_m: float = Field(default=0.0, ge=0, le=2.0)
    yaw_deg: float = Field(default=0.0, ge=0, le=180)
    presence_prob: float = Field(default=1.0, ge=0, le=1)


class EditObject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Annotated[str, StringConstraints(min_length=1, max_length=40)]
    kind: Literal["box", "cone", "bin", "pillar", "bench", "opponent"]
    x: float
    y: float
    yaw_deg: float = 0.0
    size: tuple[float, float, float] | None = None  # default: library size
    static: bool = True
    randomisation: EditRandom | None = None


class EditSurface(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    polygon: list[XY] = Field(min_length=3, max_length=100)
    friction: float = Field(default=0.5, gt=0, le=2.0)
    material: Annotated[str, StringConstraints(min_length=1, max_length=40)] = "floor"
    lidar_reflectivity: float = Field(default=0.8, ge=0, le=1)


class EditCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    a: XY
    b: XY
    measured_m: float = Field(gt=0, le=500)


class RaceSetupEdit(BaseModel):
    """Missing fields fall back to the automatic values of spec 0014."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start_line: Line | None = None
    finish_line: Line | None = None  # different from the start line → point-to-point (1 lap)
    direction: XY | None = None
    laps: int | None = Field(default=None, ge=1, le=20)
    grid_cars: int | None = Field(default=None, ge=1, le=8)
    grid_poses: list[tuple[float, float, float]] | None = Field(
        default=None, max_length=8
    )  # x, y, yaw_deg
    checkpoints: list[Line] | None = Field(default=None, max_length=1000)
    no_go_zones: list[list[XY]] = Field(default_factory=list[list[XY]], max_length=50)


class TrackEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    race_setup: RaceSetupEdit | None = None
    objects: list[EditObject] | None = Field(default=None, max_length=200)  # replaces the obstacles
    surfaces: list[EditSurface] = Field(default_factory=list[EditSurface], max_length=50)
    checks: list[EditCheck] = Field(default_factory=list[EditCheck], max_length=100)
    max_car_width_m: float = Field(default=0.35, gt=0.05, le=1.5)


class CheckResult(BaseModel):
    a: XY
    b: XY
    measured_m: float
    model_m: float
    error_m: float


class ValidationItem(BaseModel):
    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    where: XY | None = None


class ValidationReport(BaseModel):
    ok: bool = True
    items: list[ValidationItem] = []


def _seg(line: Line) -> Segment2D:
    return Segment2D(a=Vec2(x=line[0][0], y=line[0][1]), b=Vec2(x=line[1][0], y=line[1][1]))


def _poly(points: list[XY]) -> Polygon2D:
    return Polygon2D(points=[Vec2(x=x, y=y) for x, y in points])


def auto_grid(start: Line, direction: XY, cars: int) -> list[Pose2D]:
    """Two columns behind the start line, first row 0.5 m back."""
    (ax, ay), (bx, by) = start
    cx, cy = (ax + bx) / 2, (ay + by) / 2
    tx, ty = direction
    norm = math.hypot(tx, ty) or 1.0
    tx, ty = tx / norm, ty / norm
    nx, ny = -ty, tx
    yaw = math.atan2(ty, tx)
    half = GRID_SPACING_LAT_M / 2
    out: list[Pose2D] = []
    for k in range(cars):
        back = GRID_FIRST_ROW_BACK_M + GRID_SPACING_LONG_M * (k // 2)
        col = -1 if k % 2 == 0 else 1
        out.append(
            Pose2D(
                x=cx - tx * back + nx * col * half, y=cy - ty * back + ny * col * half, theta=yaw
            )
        )
    return out


def apply_edit(corridor: Corridor, edit: TrackEdit | None) -> Corridor:
    """The corridor with the edit layer applied (the base drawing is never mutated)."""
    if edit is None:
        return corridor
    track = corridor.track
    setup = track.race_setups[0]
    update: dict[str, object] = {}
    rs = edit.race_setup
    if rs is not None:
        if rs.start_line is not None:
            update["start_line"] = _seg(rs.start_line)
            if rs.finish_line is None and setup.start_line == setup.finish_line:
                update["finish_line"] = _seg(rs.start_line)
        if rs.finish_line is not None:
            update["finish_line"] = _seg(rs.finish_line)
        if rs.direction is not None:
            update["direction"] = Vec2(x=rs.direction[0], y=rs.direction[1])
        start = update.get("start_line", setup.start_line)
        finish = update.get("finish_line", setup.finish_line)
        looped = start == finish
        if rs.laps is not None:
            update["laps"] = rs.laps if looped else 1
        elif not looped:
            update["laps"] = 1
        d = update.get("direction", setup.direction)
        assert isinstance(start, Segment2D) and isinstance(d, Vec2)
        if rs.grid_poses is not None:
            update["start_grid"] = [
                Pose2D(x=x, y=y, theta=math.radians(yaw)) for x, y, yaw in rs.grid_poses
            ]
        elif rs.grid_cars is not None or rs.start_line is not None or rs.direction is not None:
            cars = rs.grid_cars or len(setup.start_grid)
            update["start_grid"] = auto_grid(
                ((start.a.x, start.a.y), (start.b.x, start.b.y)), (d.x, d.y), cars
            )
        if rs.checkpoints is not None:
            update["checkpoints"] = [_seg(c) for c in rs.checkpoints]
        if rs.no_go_zones:
            update["no_go_zones"] = [_poly(z) for z in rs.no_go_zones]
    new_setup = setup.model_copy(update=update) if update else setup

    objects = track.objects
    classes = list(track.classes)
    if edit.objects is not None:
        objects = []
        for o in edit.objects:
            sx, sy, sz = o.size or LIBRARY[o.kind]
            rr = o.randomisation
            objects.append(
                TrackObject(
                    id=o.id,
                    class_id=o.kind,
                    pose=pose2(np.array([o.x, o.y]), sz / 2, math.radians(o.yaw_deg)),
                    size=Vec3(x=sx, y=sy, z=sz),
                    static=o.static,
                    randomisation=None
                    if rr is None
                    else RandomRange(
                        position_xy_m=rr.position_xy_m,
                        yaw_rad=math.radians(rr.yaw_deg),
                        presence_prob=rr.presence_prob,
                    ),
                )
            )
    known = {c.id for c in classes}
    for o in objects:
        if o.class_id not in known and o.class_id in EXTRA_CLASSES:
            classes.append(EXTRA_CLASSES[o.class_id])
            known.add(o.class_id)
    surfaces = [
        *track.surfaces,
        *(
            SurfaceRegion(
                polygon=_poly(s.polygon),
                friction=s.friction,
                material=s.material,
                lidar_reflectivity=s.lidar_reflectivity,
            )
            for s in edit.surfaces
        ),
    ]
    checks = [
        CheckMeasurement(
            a=Vec2(x=c.a[0], y=c.a[1]), b=Vec2(x=c.b[0], y=c.b[1]), measured_m=c.measured_m
        )
        for c in edit.checks
    ]
    new_track = track.model_copy(
        update={
            "race_setups": [new_setup, *track.race_setups[1:]],
            "objects": objects,
            "classes": classes,
            "surfaces": surfaces,
            "check_measurements": checks,
        }
    )
    return Corridor(
        track=new_track, centreline=corridor.centreline, widths=corridor.widths, s=corridor.s
    )


def check_results(corridor: Corridor) -> list[CheckResult]:
    out: list[CheckResult] = []
    for c in corridor.track.check_measurements:
        model = math.hypot(c.b.x - c.a.x, c.b.y - c.a.y)
        out.append(
            CheckResult(
                a=(c.a.x, c.a.y),
                b=(c.b.x, c.b.y),
                measured_m=c.measured_m,
                model_m=round(model, 3),
                error_m=round(model - c.measured_m, 3),
            )
        )
    return out


# ------------------------------------------------------------------ validation
def _in_polygon(p: XY, poly: list[XY]) -> bool:
    x, y = p
    inside = False
    for (x0, y0), (x1, y1) in zip(poly, [*poly[1:], poly[0]], strict=True):
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            inside = not inside
    return inside


class _Free:
    """Distance of points to the free space of the corridor (negative = inside, by margin)."""

    def __init__(self, c: Corridor) -> None:
        self.centre: npt.NDArray[np.float64] = c.centreline
        self.half: npt.NDArray[np.float64] = c.widths / 2
        self.normals = normals_of(c.centreline)

    def nearest(self, p: XY) -> int:
        return int(np.argmin(np.hypot(self.centre[:, 0] - p[0], self.centre[:, 1] - p[1])))

    def margin(self, p: XY) -> float:
        """Distance to the nearest wall (positive inside the corridor, negative outside)."""
        i = self.nearest(p)
        return float(self.half[i] - math.hypot(self.centre[i, 0] - p[0], self.centre[i, 1] - p[1]))


def _tangent_forward(c: Corridor, i: int) -> npt.NDArray[np.float64]:
    n = len(c.centreline)
    j0, j1 = max(0, i - 1), min(n - 1, i + 1)
    t = c.centreline[j1] - c.centreline[j0]
    return t / (np.hypot(*t) or 1.0)


def validate(corridor: Corridor, edit: TrackEdit | None = None) -> ValidationReport:
    """Race-readiness report (errors block "ok"; warnings do not)."""
    car_w = edit.max_car_width_m if edit else 0.35
    track = corridor.track
    setup = track.race_setups[0]
    free = _Free(corridor)
    items: list[ValidationItem] = []

    def add(
        sev: Literal["error", "warning", "info"], code: str, msg: str, at: XY | None = None
    ) -> None:
        items.append(
            ValidationItem(
                severity=sev,
                code=code,
                message=msg,
                where=None if at is None else (round(at[0], 3), round(at[1], 3)),
            )
        )

    # 1. start/finish lines inside the corridor, direction along the corridor.
    for name, line in (("start", setup.start_line), ("finish", setup.finish_line)):
        for end in ((line.a.x, line.a.y), (line.b.x, line.b.y)):
            if free.margin(end) < -0.05:
                add("error", f"{name}_line_outside", f"{name} line ends outside the corridor", end)
                break
    mid = (
        (setup.start_line.a.x + setup.start_line.b.x) / 2,
        (setup.start_line.a.y + setup.start_line.b.y) / 2,
    )
    i0 = free.nearest(mid)
    tan = _tangent_forward(corridor, i0)
    if float(tan[0] * setup.direction.x + tan[1] * setup.direction.y) < 0:
        add("error", "direction_reversed", "driving direction points against the corridor", mid)

    # 2. start grid.
    grid = [(g.x, g.y) for g in setup.start_grid]
    for k, g in enumerate(grid):
        if free.margin(g) < car_w / 2:
            add("error", "grid_slot_in_wall", f"start slot {k + 1} is too close to a wall", g)
        for o in track.objects:
            r = max(o.size.x, o.size.y) / 2 + car_w / 2
            if math.hypot(g[0] - o.pose.position.x, g[1] - o.pose.position.y) < r:
                add(
                    "error",
                    "grid_slot_on_object",
                    f"start slot {k + 1} overlaps object {o.id!r}",
                    g,
                )
        for j in range(k):
            if math.hypot(g[0] - grid[j][0], g[1] - grid[j][1]) < car_w:
                add("error", "grid_slots_overlap", f"start slots {j + 1} and {k + 1} overlap", g)
    if not grid:
        add("error", "no_start_grid", "no start grid")

    # 3. connected loop.
    if (
        setup.start_line == setup.finish_line
        and float(np.hypot(*(corridor.centreline[0] - corridor.centreline[-1]))) > 0.5
    ):
        add("warning", "loop_open", "lap race on a corridor that is not closed")

    # 4. width for the car.
    w = corridor.widths
    i = int(np.argmin(w))
    at = (float(corridor.centreline[i, 0]), float(corridor.centreline[i, 1]))
    if w[i] < car_w * 1.1:
        add(
            "error",
            "too_narrow",
            f"corridor {w[i]:.2f} m wide: the car ({car_w:.2f} m) does not fit",
            at,
        )
    elif w[i] < car_w * 1.5:
        add("warning", "narrow", f"narrowest spot {w[i]:.2f} m: less than 1.5 x the car width", at)

    # 5. no-go zones.
    for z in setup.no_go_zones:
        poly = [(p.x, p.y) for p in z.points]
        for g in grid:
            if _in_polygon(g, poly):
                add("error", "no_go_on_grid", "a no-go zone covers a start slot", g)
                break
        for k in range(len(corridor.centreline)):
            c = corridor.centreline[k]
            nrm = free.normals[k]
            a = (float(c[0] + nrm[0] * w[k] / 2), float(c[1] + nrm[1] * w[k] / 2))
            b = (float(c[0] - nrm[0] * w[k] / 2), float(c[1] - nrm[1] * w[k] / 2))
            if _in_polygon(a, poly) and _in_polygon(b, poly):
                add(
                    "error",
                    "no_go_blocks",
                    "a no-go zone covers the whole corridor width",
                    (float(c[0]), float(c[1])),
                )
                break

    # 6. objects.
    for o in track.objects:
        p = (o.pose.position.x, o.pose.position.y)
        r = max(o.size.x, o.size.y) / 2
        k = free.nearest(p)
        dist = math.hypot(corridor.centreline[k, 0] - p[0], corridor.centreline[k, 1] - p[1])
        width = float(w[k])
        if dist + r > width / 2 + 0.02 and o.class_id != "door":
            add("error", "object_in_wall", f"object {o.id!r} overlaps a wall", p)
        elif 2 * r > width - car_w:
            add("error", "object_blocks", f"object {o.id!r} blocks the corridor", p)
        elif 2 * r > 0.7 * width:
            add("warning", "object_narrows", f"object {o.id!r} leaves little room", p)

    # 7. check distances (plan target +-1-2 cm).
    for res in check_results(corridor):
        e = abs(res.error_m)
        mid_c = ((res.a[0] + res.b[0]) / 2, (res.a[1] + res.b[1]) / 2)
        if e > 0.05:
            add(
                "error",
                "check_off",
                f"model differs {res.error_m * 100:+.1f} cm from the tape measure",
                mid_c,
            )
        elif e > 0.02:
            add(
                "warning",
                "check_off",
                f"model differs {res.error_m * 100:+.1f} cm from the tape measure",
                mid_c,
            )

    # 8. surfaces outside the corridor (the base floor surface is the whole floor).
    for s in track.surfaces[1:]:
        for p in s.polygon.points:
            if free.margin((p.x, p.y)) < -0.05:
                add(
                    "warning",
                    "surface_outside",
                    "a surface region reaches outside the corridor",
                    (p.x, p.y),
                )
                break

    ok = not any(it.severity == "error" for it in items)
    if ok and not items:
        add("info", "race_ready", "no problems found")
    return ValidationReport(ok=ok, items=items)
