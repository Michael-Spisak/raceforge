"""Controller contracts (spec 0004): Observation, Command, RobotInfo, RobotIO.

These types are human-owned contracts: changing them needs an approved spec change.
All values use SI units (metres, seconds, radians). Steering: positive = left.
"""

from dataclasses import dataclass, field
from typing import Protocol

from raceforge.core.telemetry import Mode

__all__ = ["Command", "LidarScan", "Mode", "Observation", "PoseEstimate", "RobotIO", "RobotInfo"]


@dataclass(frozen=True)
class LidarScan:
    """One 360° LiDAR revolution; ``angles_rad`` are counter-clockwise from the forward axis."""

    angles_rad: tuple[float, ...]
    ranges_m: tuple[float | None, ...]  # None = no return (out of range or dropout)
    t_s: float  # time the revolution finished


@dataclass(frozen=True)
class PoseEstimate:
    x_m: float
    y_m: float
    heading_rad: float
    confidence: float  # 0..1


@dataclass(frozen=True)
class Observation:
    """Everything the controller may use at one control step."""

    t_s: float
    dt_s: float
    ultrasonic_m: dict[str, float | None] = field(default_factory=dict[str, float | None])
    lidar: LidarScan | None = None
    yaw_rate_rad_s: float | None = None
    heading_rad: float | None = None
    speed_m_s: float | None = None
    steering_rad: float | None = None
    bumper: dict[str, bool] = field(default_factory=dict[str, bool])
    battery_v: float | None = None
    pose_estimate: PoseEstimate | None = None
    mode: Mode = Mode.SIM


@dataclass(frozen=True)
class Command:
    """What the controller wants. The runtime clamps steering to the car's limit."""

    steering_rad: float = 0.0
    speed_m_s: float = 0.0


@dataclass(frozen=True)
class RobotInfo:
    car_name: str
    sensors: tuple[str, ...]  # e.g. ("front", "left", "right", "lidar", "gyro")
    max_steer_rad: float
    max_speed_m_s: float
    wheelbase_m: float
    track_m: float
    control_rate_hz: float


class RobotIO(Protocol):
    """Hardware abstraction: SimIO (simulation) and RealIO (car runtime, spec 0005)."""

    @property
    def info(self) -> RobotInfo: ...

    def read(self) -> Observation: ...

    def write(self, cmd: Command) -> None: ...

    def emit(self, channel: str, value: float | int | bool | str) -> None: ...

    def note(self, text: str, tags: list[str] | None = None) -> None: ...
