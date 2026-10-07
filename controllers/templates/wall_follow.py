"""Template: follow both corridor walls with the EV3 ultrasonic sensors and the EV3 gyro (no LiDAR).

- Cascade: the sideways offset (left vs. right distance) gives a desired heading relative to the
  corridor direction (limited to ±`max_angle_deg`); the gyro heading is then steered to it.
- Side distances are capped at `cap_m`: niches and door recesses then barely pull the car sideways.
- If only one wall is visible, keep `single_wall_m` from it.
- Something close in front (a corner): turn towards the side with more space.
- A slow "direction memory" from the gyro never lets the car turn around.
"""

import math

from raceforge.control import (
    Command,
    Controller,
    ControllerParams,
    Ema,
    Median,
    Observation,
    Tunable,
    clamp,
    wrap_angle,
)


class WallFollowParams(ControllerParams):
    speed_m_s: float = Tunable(0.3, 0.1, 1.0, description="cruise speed")
    cap_m: float = Tunable(1.1, 0.5, 2.5, description="side distances are capped at this value")
    single_wall_m: float = Tunable(
        0.6, 0.2, 1.2, description="distance to keep if only one wall is seen"
    )
    k_offset: float = Tunable(
        1.2, 0.0, 5.0, description="desired heading (rad) per metre of offset"
    )
    max_angle_deg: float = Tunable(25.0, 5.0, 45.0, description="max angle towards the centre line")
    k_heading: float = Tunable(1.5, 0.1, 5.0, description="steering per radian of heading error")
    corridor_tau_s: float = Tunable(
        2.0, 0.5, 10.0, description="how fast the corridor direction adapts"
    )
    front_turn_m: float = Tunable(
        0.8, 0.3, 2.0, description="turn away when the front gap is below this"
    )
    max_steer_deg: float = Tunable(20.0, 5.0, 30.0, description="steering limit while centering")
    max_turn_back_deg: float = Tunable(
        100.0, 60.0, 170.0, description="never turn further away than this"
    )


class WallFollow(Controller[WallFollowParams]):
    Params = WallFollowParams

    def __init__(self, params: WallFollowParams | None = None) -> None:
        super().__init__(params)
        self.left = Median(3)
        self.right = Median(3)
        self.front = Median(3)
        self.direction = Ema(alpha=0.004)  # ~5 s heading memory at 50 Hz (anti U-turn)
        self.corridor: float | None = None  # estimated corridor direction (unwrapped heading)

    def step(self, obs: Observation) -> Command:
        p = self.params
        us = obs.ultrasonic_m
        front = self.front.update(us["front"]) if us.get("front") is not None else math.inf
        left = min(self.left.update(us["left"]), p.cap_m) if us.get("left") is not None else None
        right = (
            min(self.right.update(us["right"]), p.cap_m) if us.get("right") is not None else None
        )

        heading = obs.heading_rad
        if heading is not None:
            alpha = clamp(obs.dt_s / p.corridor_tau_s, 0.0, 1.0)
            self.corridor = (
                heading
                if self.corridor is None
                else self.corridor + alpha * wrap_angle(heading - self.corridor)
            )

        speed = p.speed_m_s
        raw_left, raw_right = us.get("left"), us.get("right")
        if front < p.front_turn_m:
            self.state = "avoid_front"
            towards_left = (raw_left or 3.0) >= (raw_right or 3.0)
            strength = clamp((p.front_turn_m - front) / p.front_turn_m * 2.5, 0.4, 1.0)
            steering = math.copysign(math.radians(30) * strength, 1.0 if towards_left else -1.0)
            speed *= 0.6
        else:
            if left is not None and right is not None:
                self.state = "center"
                offset = (left - right) / 2  # > 0: closer to the right wall -> head left
            elif right is not None:
                self.state = "right_wall"
                offset = p.single_wall_m - right
            elif left is not None:
                self.state = "left_wall"
                offset = left - p.single_wall_m
            else:
                self.state = "blind"
                offset = 0.0
            limit = math.radians(p.max_angle_deg)
            desired = clamp(p.k_offset * offset, -limit, limit)
            if heading is not None and self.corridor is not None:
                steering = p.k_heading * (desired - wrap_angle(heading - self.corridor))
            else:
                steering = desired
            steering = clamp(
                steering, -math.radians(p.max_steer_deg), math.radians(p.max_steer_deg)
            )

        if obs.heading_rad is not None:  # direction memory: never turn around
            ref = self.direction.update(obs.heading_rad)
            off = wrap_angle(obs.heading_rad - ref)
            if abs(off) > math.radians(p.max_turn_back_deg) and steering * off > 0:
                self.state = "keep_direction"
                steering = -math.copysign(math.radians(15), off)
        self.emit("ctl.left_m", left if left is not None else -1.0)
        self.emit("ctl.right_m", right if right is not None else -1.0)
        return Command(steering_rad=steering, speed_m_s=speed)
