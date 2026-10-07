"""Primitive value types: vectors, quaternions, poses, money, time, ids, versions (spec 0001)."""

import math
from typing import Annotated, Any, Self

from pydantic import Field, StringConstraints, model_validator

from raceforge.core.base import CoreModel
from raceforge.core.ids import UUID7_PATTERN

QUAT_TOLERANCE = 1e-6
_EXACT_TOLERANCE = 1e-12

ObjectId = Annotated[str, StringConstraints(pattern=UUID7_PATTERN)]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")]
LocalId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
SemVer = Annotated[str, StringConstraints(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


class Vec2(CoreModel):
    x: float
    y: float

    def norm(self) -> float:
        return math.hypot(self.x, self.y)


class Vec3(CoreModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)

    def norm(self) -> float:
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)


def _normalise_components(values: list[float], what: str) -> list[float]:
    """Normalise a vector to unit length if within tolerance; reject otherwise."""
    norm = math.sqrt(sum(v * v for v in values))
    if norm == 0.0:
        raise ValueError(f"{what} must not be zero")
    if abs(norm - 1.0) <= _EXACT_TOLERANCE:
        return values
    if abs(norm - 1.0) > QUAT_TOLERANCE:
        raise ValueError(
            f"{what} must be unit length (norm={norm:.9f}, tolerance {QUAT_TOLERANCE})"
        )
    return [v / norm for v in values]


class UnitVec3(Vec3):
    """A direction vector; normalised on validation if within tolerance."""

    @model_validator(mode="after")
    def _check_unit(self) -> Self:
        normalised = _normalise_components([self.x, self.y, self.z], "unit vector")
        if normalised != [self.x, self.y, self.z]:
            object.__setattr__(self, "x", normalised[0])
            object.__setattr__(self, "y", normalised[1])
            object.__setattr__(self, "z", normalised[2])
        return self


class Quat(CoreModel):
    """Unit quaternion (w, x, y, z); normalised on validation if within tolerance."""

    w: float = 1.0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    @model_validator(mode="after")
    def _check_unit(self) -> Self:
        values = [self.w, self.x, self.y, self.z]
        normalised = _normalise_components(values, "quaternion")
        if normalised != values:
            for name, value in zip(("w", "x", "y", "z"), normalised, strict=True):
                object.__setattr__(self, name, value)
        return self


class Pose(CoreModel):
    """Position + orientation relative to the parent frame (right-handed, Z-up, metres)."""

    position: Vec3 = Field(default_factory=Vec3)
    orientation: Quat = Field(default_factory=Quat)


class Pose2D(CoreModel):
    x: float
    y: float
    theta: float = 0.0


class Money(CoreModel):
    """Amount in integer minor units (cents)."""

    cents: int = Field(ge=0)
    currency: Currency = "EUR"


class Timestamp(CoreModel):
    """Monotonic nanoseconds plus optional offset to wall-clock time (UTC epoch ns)."""

    mono_ns: int = Field(ge=0)
    wall_offset_ns: int | None = None

    @property
    def wall_ns(self) -> int | None:
        return None if self.wall_offset_ns is None else self.mono_ns + self.wall_offset_ns


class VersionRef(CoreModel):
    object_id: ObjectId
    semver: SemVer
    content_hash: Sha256


class BlobRef(CoreModel):
    sha256: Sha256
    size_bytes: int = Field(ge=0)
    media_type: Annotated[str, StringConstraints(min_length=1, max_length=255)]


def _not_identical(a: Any, b: Any, what: str) -> None:
    if a == b:
        raise ValueError(f"{what} endpoints must differ")


class Segment2D(CoreModel):
    a: Vec2
    b: Vec2

    @model_validator(mode="after")
    def _check(self) -> Self:
        _not_identical(self.a, self.b, "segment")
        return self
