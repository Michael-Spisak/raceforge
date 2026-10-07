"""Telemetry frames and run logs (spec 0001)."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from raceforge.core.base import CoreModel
from raceforge.core.primitives import (
    BlobRef,
    LocalId,
    ObjectId,
    Pose2D,
    Timestamp,
    Vec3,
    VersionRef,
)
from raceforge.core.registry import register_document

MAX_CHANNELS = 64
ChannelKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.]{0,63}$")]
ChannelValue = bool | int | float | str
Fraction = Annotated[float, Field(ge=0, le=1)]
NonNeg = Annotated[float, Field(ge=0)]


class Mode(StrEnum):
    TEST = "test"
    RACE = "race"
    SIM = "sim"
    HIL = "hil"


class Command(CoreModel):
    steering_rad: float
    speed_m_s: float


class RangeReading(CoreModel):
    kind: Literal["range"] = "range"
    distance_m: NonNeg | None  # None = no echo / out of range


class RangeArray(CoreModel):
    kind: Literal["range_array"] = "range_array"
    angle_min_rad: float
    angle_increment_rad: float
    ranges: list[NonNeg | None]


class ImuSample(CoreModel):
    kind: Literal["imu"] = "imu"
    yaw_rate_rad_s: float
    accel_m_s2: Vec3 | None = None


class BoolReading(CoreModel):
    kind: Literal["bool"] = "bool"
    value: bool


class CameraFrameRef(CoreModel):
    kind: Literal["camera_frame"] = "camera_frame"
    frame_index: int = Field(ge=0)
    blob: BlobRef | None = None


SensorReading = Annotated[
    RangeReading | RangeArray | ImuSample | BoolReading | CameraFrameRef,
    Field(discriminator="kind"),
]


class Measured(CoreModel):
    steering_rad: float | None = None
    speed_m_s: float | None = None
    yaw_rate_rad_s: float | None = None
    sensors: dict[LocalId, SensorReading] = Field(default_factory=dict[LocalId, SensorReading])


class Pose2DEstimate(CoreModel):
    pose: Pose2D
    confidence: Fraction


class PowerStatus(CoreModel):
    ev3_battery_v: NonNeg | None = None
    board_battery_v: NonNeg | None = None
    motor_battery_v: NonNeg | None = None
    board_cpu_temp_c: float | None = None
    board_cpu_load: Fraction | None = None


class LoopStats(CoreModel):
    rate_hz: NonNeg
    jitter_ms: NonNeg = 0.0
    last_tick_ms: NonNeg = 0.0
    deadline_misses: int = Field(default=0, ge=0)


@register_document("telemetry", 1)
class TelemetryFrame(CoreModel):
    schema_: Literal["telemetry"] = Field(default="telemetry", alias="schema")
    schema_version: Literal[1] = 1
    t: Timestamp
    seq: int = Field(ge=0)
    mode: Mode
    state: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    faults: list[str] = Field(default_factory=list[str])
    cmd: Command
    meas: Measured = Field(default_factory=Measured)
    pose_est: Pose2DEstimate | None = None
    power: PowerStatus = Field(default_factory=PowerStatus)
    loop: LoopStats
    channels: dict[ChannelKey, ChannelValue] = Field(
        default_factory=dict[ChannelKey, ChannelValue], max_length=MAX_CHANNELS
    )


class RunKind(StrEnum):
    SIM = "sim"
    REAL = "real"
    HIL = "hil"


class Note(CoreModel):
    t: Timestamp
    text: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    tags: list[str] = Field(default_factory=list[str])
    author: str = Field(min_length=1)


@register_document("runlog", 1)
class RunLog(CoreModel):
    schema_: Literal["runlog"] = Field(default="runlog", alias="schema")
    schema_version: Literal[1] = 1
    id: ObjectId
    kind: RunKind
    car: ObjectId | None = None
    assembly: VersionRef
    track: VersionRef | None = None
    race_setup_id: LocalId | None = None
    controller: VersionRef
    bundle: VersionRef | None = None
    started: Timestamp
    notes: list[Note] = Field(default_factory=list[Note])
    file: BlobRef

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.race_setup_id is not None and self.track is None:
            raise ValueError("race_setup_id requires a track")
        return self
