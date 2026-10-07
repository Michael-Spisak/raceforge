"""Vehicle mechanics that the assembly graph alone does not encode yet (spec 0002).

The quick-start fills this from its parameters; the 3D editor will later derive it
from gears and joints.
"""

import math
from dataclasses import dataclass, field

from raceforge.core.devices import MotorParams


@dataclass(frozen=True)
class DriveSpec:
    axle_x_m: float  # wheels within half a stud of this x are driven
    motor: MotorParams
    gear_ratio: float  # motor revolutions per wheel revolution
    differential: bool


@dataclass(frozen=True)
class VehicleSpec:
    max_steer_rad: float
    ackermann_pct: float
    steering_play_rad: float
    steering_motor: MotorParams
    drives: list[DriveSpec]
    tyre_friction: float = 0.8
    corridor_min_width_m: float = 1.5
    target_curve_radius_m: float = 1.0
    measured_mass_kg: float | None = None
    measured_cog_m: tuple[float, float, float] | None = None
    extra: dict[str, float] = field(default_factory=dict[str, float])

    @property
    def max_steer_deg(self) -> float:
        return math.degrees(self.max_steer_rad)
