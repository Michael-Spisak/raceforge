"""Layered track model: 2D floor/walls, 3D refs, objects, surfaces, race setups (spec 0001)."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, PositiveFloat, PositiveInt, StringConstraints, model_validator

from raceforge.core.base import CoreModel
from raceforge.core.primitives import BlobRef, LocalId, Pose, Pose2D, Segment2D, Vec2, Vec3
from raceforge.core.registry import register_document

Fraction = Annotated[float, Field(ge=0, le=1)]


class Polygon2D(CoreModel):
    points: list[Vec2] = Field(min_length=3)


class Wall(CoreModel):
    points: list[Vec2] = Field(min_length=2)
    height_m: PositiveFloat = 1.0


class OccupancyGridRef(CoreModel):
    blob: BlobRef
    resolution_m: PositiveFloat
    origin: Pose2D
    width: PositiveInt
    height: PositiveInt


class LabelLayerRef(CoreModel):
    blob: BlobRef


class RandomRange(CoreModel):
    position_xy_m: float = Field(default=0.0, ge=0)
    yaw_rad: float = Field(default=0.0, ge=0)
    scale: float = Field(default=0.0, ge=0, le=1)
    presence_prob: Fraction = 1.0


class ClassDef(CoreModel):
    id: LocalId
    name: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    builtin: bool = False
    material: str | None = None
    lidar_reflectivity: Fraction | None = None
    camera_hint: str | None = None


class TrackObject(CoreModel):
    id: LocalId
    class_id: LocalId
    pose: Pose
    size: Vec3
    static: bool = True
    randomisation: RandomRange | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if min(self.size.as_tuple()) <= 0:
            raise ValueError("object size components must be positive")
        return self


class SurfaceRegion(CoreModel):
    polygon: Polygon2D
    friction: PositiveFloat
    material: str
    lidar_reflectivity: Fraction = 0.8


class CheckMeasurement(CoreModel):
    a: Vec2
    b: Vec2
    measured_m: PositiveFloat


class DoorState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    RANDOM = "random"


class RaceSetup(CoreModel):
    id: LocalId
    name: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    start_line: Segment2D
    finish_line: Segment2D
    direction: Vec2
    laps: int = Field(default=3, ge=1)
    start_grid: list[Pose2D] = Field(default_factory=list[Pose2D])
    checkpoints: list[Segment2D] = Field(default_factory=list[Segment2D])
    no_go_zones: list[Polygon2D] = Field(default_factory=list[Polygon2D])
    door_states: dict[LocalId, DoorState] = Field(default_factory=dict[LocalId, DoorState])

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.direction.norm() == 0:
            raise ValueError("race direction must be non-zero")
        return self


def _unique(ids: list[str], what: str) -> None:
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate {what} ids")


@register_document("track", 1)
class Track(CoreModel):
    schema_: Literal["track"] = Field(default="track", alias="schema")
    schema_version: Literal[1] = 1
    frame_origin_note: str = ""
    floor: Polygon2D
    walls: list[Wall] = Field(default_factory=list[Wall])
    mesh: BlobRef | None = None
    splat: BlobRef | None = None
    occupancy: OccupancyGridRef | None = None
    labels: LabelLayerRef | None = None
    objects: list[TrackObject] = Field(default_factory=list[TrackObject])
    surfaces: list[SurfaceRegion] = Field(default_factory=list[SurfaceRegion])
    race_setups: list[RaceSetup] = Field(default_factory=list[RaceSetup])
    check_measurements: list[CheckMeasurement] = Field(default_factory=list[CheckMeasurement])
    classes: list[ClassDef] = Field(default_factory=list[ClassDef])

    @model_validator(mode="after")
    def _check(self) -> Self:
        _unique([c.id for c in self.classes], "class")
        _unique([o.id for o in self.objects], "object")
        _unique([r.id for r in self.race_setups], "race setup")
        known = {c.id for c in self.classes}
        for obj in self.objects:
            if obj.class_id not in known:
                raise ValueError(f"object {obj.id!r} uses undefined class {obj.class_id!r}")
        return self
