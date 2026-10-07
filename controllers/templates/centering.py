"""Template: stay in the middle of the corridor with the 2D LiDAR.

Idea: every LiDAR return closer than `influence_m` pushes the car away from it (like a magnet),
and the car steers towards the most open direction ahead.
Speed drops when something is close in front.
"""

import math

from raceforge.control import (
    Command,
    Controller,
    ControllerParams,
    Ema,
    Observation,
    Tunable,
    clamp,
    sector_min,
    wrap_angle,
)


class CenteringParams(ControllerParams):
    speed_m_s: float = Tunable(0.35, 0.1, 1.5, description="cruise speed")
    influence_m: float = Tunable(
        0.9, 0.3, 2.0, description="obstacles closer than this push the car away"
    )
    k_push: float = Tunable(0.25, 0.0, 2.0, description="strength of the push away from walls")
    k_gap: float = Tunable(0.9, 0.0, 3.0, description="how strongly to steer towards open space")
    look_deg: float = Tunable(
        70.0, 20.0, 120.0, description="half-width of the forward search cone"
    )
    slow_front_m: float = Tunable(
        0.6, 0.2, 2.0, description="slow down when the front gap is below this"
    )
    max_turn_back_deg: float = Tunable(
        100.0, 60.0, 170.0, description="never turn further away than this"
    )


class Centering(Controller[CenteringParams]):
    """Never turns around: a slow "direction memory" (gyro heading) keeps the car going forward."""

    Params = CenteringParams

    def __init__(self, params: CenteringParams | None = None) -> None:
        super().__init__(params)
        self.direction = Ema(alpha=0.004)  # ~5 s memory at 50 Hz

    def step(self, obs: Observation) -> Command:
        p = self.params
        scan = obs.lidar
        if scan is None:  # no scan yet (first 0.1 s)
            return Command(0.0, 0.0)

        push = 0.0
        best_angle, best_range = 0.0, 0.0
        look = math.radians(p.look_deg)
        for a, r in zip(scan.angles_rad, scan.ranges_m, strict=True):
            if r is None:
                continue
            ang = math.remainder(a, 2 * math.pi)
            if abs(ang) > math.radians(120):
                continue
            if r < p.influence_m:
                push -= math.sin(ang) * (p.influence_m - r) / max(r, 0.05)
            # open space ahead, weighted towards straight on
            score = min(r, 4.0) * math.cos(ang / 2)
            if abs(ang) <= look and score > best_range:
                best_range, best_angle = score, ang

        steering = p.k_gap * best_angle + p.k_push * push
        if obs.heading_rad is not None:
            ref = self.direction.update(obs.heading_rad)
            off = wrap_angle(obs.heading_rad - ref)
            limit = math.radians(p.max_turn_back_deg)
            if (
                abs(off) > limit and steering * off > 0
            ):  # turning further away -> steer back instead
                steering = -math.copysign(abs(steering), off)
        front = sector_min(scan, -15, 15)
        speed = p.speed_m_s
        if front is not None and front < p.slow_front_m:
            speed *= clamp(front / p.slow_front_m, 0.4, 1.0)
        self.emit("ctl.push", push)
        self.emit("ctl.gap_deg", math.degrees(best_angle))
        return Command(steering_rad=steering, speed_m_s=speed)
