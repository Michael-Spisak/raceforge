"""Observation vector and action mapping of learned policies (spec 0022).

Shared by the training environment (``raceforge.train.env``) and the ONNX policy controller that
runs on the car, so a policy sees exactly the same inputs in the simulator and on the real car.
Pure Python (no NumPy): it runs inside the car's control loop.

Observation (41 values, each in [-1, 1]): 36 LiDAR sectors of 10° (min distance / ``lidar_max_m``,
1 = nothing seen; sector 0 starts at the forward axis, counter-clockwise), speed / top speed,
steering / max steering, yaw rate / π, last action (steering, speed).
Action (2 values in [-1, 1]): steering (times max steering, + = left) and speed (>= 0: times top
speed; < 0: times top speed times ``reverse_factor``); steering changes at most
``max_steer_rate`` rad/s.
"""

import math
from dataclasses import dataclass

from raceforge.control.types import Command, Observation, RobotInfo

SECTORS = 36
OBS_SIZE = SECTORS + 5


@dataclass(frozen=True)
class PolicyIO:
    lidar_max_m: float = 4.0
    reverse_factor: float = 0.5
    max_steer_rate_rad_s: float = 3.0  # LEGO steering motor; protects the gears (docs/PLAN.md §4)


DEFAULT_IO = PolicyIO()


def _clip(v: float) -> float:
    return -1.0 if v < -1.0 else 1.0 if v > 1.0 else v


def lidar_sectors(
    angles: tuple[float, ...] | list[float],
    ranges: tuple[float | None, ...] | list[float | None],
    max_m: float,
) -> list[float]:
    out = [1.0] * SECTORS
    two_pi = 2 * math.pi
    for a, r in zip(angles, ranges, strict=True):
        if r is None:
            continue
        i = int((a % two_pi) / two_pi * SECTORS) % SECTORS
        out[i] = min(out[i], min(r, max_m) / max_m)
    return out


def observation_vector(
    obs: Observation, info: RobotInfo, last_action: tuple[float, float], cfg: PolicyIO = DEFAULT_IO
) -> list[float]:
    if obs.lidar is not None:
        vec = lidar_sectors(obs.lidar.angles_rad, obs.lidar.ranges_m, cfg.lidar_max_m)
    else:
        vec = [1.0] * SECTORS
    vec.append(_clip((obs.speed_m_s or 0.0) / info.max_speed_m_s))
    vec.append(_clip((obs.steering_rad or 0.0) / info.max_steer_rad))
    vec.append(_clip((obs.yaw_rate_rad_s or 0.0) / math.pi))
    vec.append(_clip(last_action[0]))
    vec.append(_clip(last_action[1]))
    return vec


def action_to_command(
    action: tuple[float, float] | list[float],
    info: RobotInfo,
    steer_now: float,
    dt_s: float,
    cfg: PolicyIO = DEFAULT_IO,
) -> Command:
    """``steer_now``: the steering command sent last step (the rate limit starts from it)."""
    a0 = _clip(float(action[0])) if math.isfinite(action[0]) else 0.0
    a1 = _clip(float(action[1])) if math.isfinite(action[1]) else 0.0
    target = a0 * info.max_steer_rad
    rate = cfg.max_steer_rate_rad_s * dt_s
    steer = steer_now + max(-rate, min(rate, target - steer_now))
    speed = a1 * info.max_speed_m_s
    if speed < 0:
        speed *= cfg.reverse_factor
    return Command(steering_rad=steer, speed_m_s=speed)
