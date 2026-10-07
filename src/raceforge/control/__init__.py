"""raceforge.control — controller SDK shared by the simulator and the real car (spec 0004).

Quick reference::

    from raceforge.control import Command, Controller, ControllerParams, Observation, Tunable

    class MyParams(ControllerParams):
        speed: float = Tunable(0.3, 0.1, 1.0)

    class MyController(Controller[MyParams]):
        Params = MyParams

        def step(self, obs: Observation) -> Command:
            return Command(steering_rad=0.0, speed_m_s=self.params.speed)
"""

from raceforge.control.controller import Controller, ControllerHost, load_controller
from raceforge.control.filters import PID, Ema, Median, RateLimiter, clamp
from raceforge.control.geometry import (
    centering_error,
    deg,
    rad,
    sector_mean,
    sector_min,
    wall_angle,
    wrap_angle,
)
from raceforge.control.params import ControllerParams, Tunable
from raceforge.control.state_machine import StateMachine
from raceforge.control.types import (
    Command,
    LidarScan,
    Mode,
    Observation,
    PoseEstimate,
    RobotInfo,
    RobotIO,
)

__all__ = [
    "PID",
    "Command",
    "Controller",
    "ControllerHost",
    "ControllerParams",
    "Ema",
    "LidarScan",
    "Median",
    "Mode",
    "Observation",
    "PoseEstimate",
    "RateLimiter",
    "RobotIO",
    "RobotInfo",
    "StateMachine",
    "Tunable",
    "centering_error",
    "clamp",
    "deg",
    "load_controller",
    "rad",
    "sector_mean",
    "sector_min",
    "wall_angle",
    "wrap_angle",
]
