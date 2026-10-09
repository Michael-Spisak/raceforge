"""Quick tracks (spec 0014): a corridor drawn as a centreline in the app → core.Track.

The drawn polyline is rounded and sampled like the procedural corridors, then walls, checkpoints,
start grid and race setup come from the same :func:`assemble_corridor`, so quick tracks follow the
same conventions (walls with the free space on their left, checkpoints every 0.5 m).
"""

import math
from itertools import pairwise
from typing import Annotated, Literal

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from raceforge.core.primitives import Vec3
from raceforge.core.track import SurfaceRegion, TrackObject
from raceforge.track.edit import TrackEdit, apply_edit
from raceforge.track.procedural import (
    BUILTIN_CLASSES,
    SAMPLE_M,
    Corridor,
    GenerationError,
    assemble_corridor,
    fillet,
    normals_of,
    pose2,
    resample,
)

OBSTACLES: dict[str, tuple[str, tuple[float, float, float]]] = {
    "bin": ("bin", (0.35, 0.35, 0.6)),
    "pillar": ("pillar", (0.3, 0.3, 2.5)),
    "bench": ("bench", (1.2, 0.4, 0.45)),
}
MIN_POINT_GAP_M = 0.3


class QuickObstacle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["bin", "pillar", "bench"]
    x: float
    y: float
    yaw_deg: float = 0.0


class QuickTrack(BaseModel):
    """What the user draws: centreline points (m), one corridor width, loop or point-to-point."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, StringConstraints(min_length=1, max_length=60)] = "quick track"
    points: list[tuple[float, float]] = Field(min_length=2, max_length=500)
    width_m: float = Field(default=1.6, ge=0.6, le=4.0)
    loop: bool = True
    laps: int = Field(default=3, ge=1, le=20)
    corner_radius_m: float | None = Field(default=None, gt=0, le=10.0)  # default: width/2 + 0.4
    obstacles: list[QuickObstacle] = Field(default_factory=list[QuickObstacle], max_length=200)
    wall_height_m: float = Field(default=2.5, gt=0, le=10.0)
    friction: float = Field(default=0.75, gt=0, le=2.0)
    edit: TrackEdit | None = None  # track editor layer (spec 0025)

    @model_validator(mode="after")
    def _check(self) -> "QuickTrack":
        if self.loop and len(self.points) < 3:
            raise ValueError("points: a loop needs at least 3 points")
        pts = [*self.points, self.points[0]] if self.loop else self.points
        for (x0, y0), (x1, y1) in pairwise(pts):
            if math.hypot(x1 - x0, y1 - y0) < MIN_POINT_GAP_M:
                raise ValueError(f"points: neighbours must be at least {MIN_POINT_GAP_M} m apart")
        return self


def build_quick_track(q: QuickTrack) -> Corridor:
    """The drawn track as a corridor; raises :class:`GenerationError` with a readable reason."""
    skeleton = np.array([*q.points, q.points[0]] if q.loop else q.points, dtype=np.float64)
    radius = q.corner_radius_m or q.width_m / 2 + 0.4
    centre, s = resample(fillet(skeleton, radius, q.loop), SAMPLE_M)
    if q.loop:
        centre[-1] = centre[0]
    if s[-1] < 5.0:
        raise GenerationError("track shorter than 5 m")
    _check_overlap(centre, s, q.width_m, q.loop)
    normals = normals_of(centre)
    if q.loop:
        normals[-1] = normals[0]
    n = len(centre)
    widths = np.full(n, q.width_m)
    depth = np.zeros((2, n))
    curvature = np.abs(np.gradient(np.unwrap(np.arctan2(normals[:, 1], normals[:, 0])))) / SAMPLE_M
    objects = [
        TrackObject(
            id=f"{o.kind}-{k + 1}",
            class_id=OBSTACLES[o.kind][0],
            pose=pose2(np.array([o.x, o.y]), OBSTACLES[o.kind][1][2] / 2, math.radians(o.yaw_deg)),
            size=Vec3(
                x=OBSTACLES[o.kind][1][0], y=OBSTACLES[o.kind][1][1], z=OBSTACLES[o.kind][1][2]
            ),
            static=o.kind == "pillar",
        )
        for k, o in enumerate(q.obstacles)
    ]
    glass: list[SurfaceRegion] = []
    corridor = assemble_corridor(
        centre,
        s,
        normals,
        widths,
        depth,
        curvature < 0.05,
        objects=objects,
        glass=glass,
        door_ids=[],
        loop=q.loop,
        width_min_m=q.width_m,
        wall_height_m=q.wall_height_m,
        friction=q.friction,
        note=f"quick track {q.name!r}",
        straight_start=False,
    )
    setup = corridor.track.race_setups[0]
    track = corridor.track.model_copy(
        update={
            "race_setups": [setup.model_copy(update={"laps": q.laps if q.loop else 1})],
            "classes": BUILTIN_CLASSES,
        }
    )
    return apply_edit(
        Corridor(track=track, centreline=corridor.centreline, widths=corridor.widths, s=corridor.s),
        q.edit,
    )


def _check_overlap(
    centre: npt.NDArray[np.float64], s: npt.NDArray[np.float64], width: float, loop: bool
) -> None:
    """Parts of the corridor that are far apart along the track must not come closer than its width
    (the wall checks miss a corridor folded onto itself)."""
    step = max(1, int(0.5 / SAMPLE_M))
    c, a = centre[::step], s[::step]
    dist = np.linalg.norm(c[:, None, :] - c[None, :, :], axis=2)
    along = np.abs(a[:, None] - a[None, :])
    if loop:
        along = np.minimum(along, s[-1] - along)
    far = along > width * math.pi
    if (dist[far] < width + 0.1).any():
        raise GenerationError("corridor overlaps itself: move the points further apart")
