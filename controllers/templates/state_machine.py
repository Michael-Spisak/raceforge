"""Template: a state machine driver with the 2D LiDAR (+ gyro).

States:
- STRAIGHT: stay in the middle (cascade: sideways offset -> desired heading -> gyro steering),
  full speed.
- CURVE: the corridor bends (front gap shrinking): steer towards the most open direction, slower.
- OBSTACLE: something close in front: slow down, steer hard towards the open side.
- RECOVER: stuck (commanded to move but not moving) or bumped: reverse with opposite steering.
The current state is published to telemetry automatically (live state diagram).
"""

import math
from enum import Enum, auto

from raceforge.control import (
    Command,
    Controller,
    ControllerParams,
    Observation,
    StateMachine,
    Tunable,
    clamp,
    sector_min,
    wrap_angle,
)


class S(Enum):
    STRAIGHT = auto()
    CURVE = auto()
    OBSTACLE = auto()
    RECOVER = auto()


class StateMachineParams(ControllerParams):
    speed_m_s: float = Tunable(0.35, 0.1, 1.5, description="speed on straights")
    curve_speed_m_s: float = Tunable(0.25, 0.1, 1.0, description="speed in curves")
    curve_front_m: float = Tunable(1.4, 0.5, 3.0, description="front gap that starts CURVE")
    obstacle_front_m: float = Tunable(0.45, 0.2, 1.0, description="front gap that starts OBSTACLE")
    k_offset: float = Tunable(1.2, 0.0, 5.0, description="desired heading per metre of offset")
    k_heading: float = Tunable(1.5, 0.1, 5.0, description="steering per radian of heading error")
    k_gap: float = Tunable(0.9, 0.0, 3.0, description="steering towards open space in curves")
    recover_s: float = Tunable(1.2, 0.3, 3.0, description="how long to reverse when stuck")


class StateMachineDriver(Controller[StateMachineParams]):
    Params = StateMachineParams

    def __init__(self, params: StateMachineParams | None = None) -> None:
        super().__init__(params)
        p = self.params
        self.front = math.inf
        self.stuck_s = 0.0
        self.bumped = False
        self.corridor: float | None = None
        self.sm = StateMachine(S.STRAIGHT)
        self.sm.transition(None, S.RECOVER, lambda: self.stuck_s > 1.0 or self.bumped)
        self.sm.transition(S.RECOVER, S.STRAIGHT, lambda: self.sm.time_in_state_s > p.recover_s)
        self.sm.transition(S.STRAIGHT, S.OBSTACLE, lambda: self.front < p.obstacle_front_m)
        self.sm.transition(S.CURVE, S.OBSTACLE, lambda: self.front < p.obstacle_front_m)
        self.sm.transition(S.STRAIGHT, S.CURVE, lambda: self.front < p.curve_front_m)
        self.sm.transition(S.OBSTACLE, S.CURVE, lambda: self.front > p.obstacle_front_m * 1.5)
        self.sm.transition(S.CURVE, S.STRAIGHT, lambda: self.front > p.curve_front_m * 1.3)
        self.sm.on_enter(S.RECOVER, self._reset_stuck)

    def _reset_stuck(self) -> None:
        self.stuck_s = 0.0

    def step(self, obs: Observation) -> Command:
        p = self.params
        scan = obs.lidar
        if scan is None:
            return Command()
        self.front = sector_min(scan, -20, 20) or math.inf
        left = sector_min(scan, 70, 110)
        right = sector_min(scan, -110, -70)
        self.bumped = obs.bumper.get("any", False) and self.sm.state is not S.RECOVER
        moving = obs.speed_m_s is not None and abs(obs.speed_m_s) > 0.03
        self.stuck_s = 0.0 if moving or self.sm.state is S.RECOVER else self.stuck_s + obs.dt_s
        if obs.heading_rad is not None:
            a = clamp(obs.dt_s / 2.0, 0.0, 1.0)
            h = obs.heading_rad
            self.corridor = (
                h if self.corridor is None else self.corridor + a * wrap_angle(h - self.corridor)
            )

        state = self.sm.update(obs.dt_s)
        self.state = state.name.lower()
        if state is S.RECOVER:
            return Command(steering_rad=-self._gap_angle(scan), speed_m_s=-0.2)

        if (
            state is S.STRAIGHT
            and left is not None
            and right is not None
            and obs.heading_rad is not None
        ):
            offset = (min(left, 1.5) - min(right, 1.5)) / 2
            desired = clamp(p.k_offset * offset, -0.45, 0.45)
            assert self.corridor is not None
            steering = p.k_heading * (desired - wrap_angle(obs.heading_rad - self.corridor))
            return Command(steering_rad=steering, speed_m_s=p.speed_m_s)

        gap = self._gap_angle(scan)
        push = self._push(scan)
        speed = p.curve_speed_m_s if state is not S.OBSTACLE else p.curve_speed_m_s * 0.6
        return Command(steering_rad=p.k_gap * gap + 0.25 * push, speed_m_s=speed)

    @staticmethod
    def _gap_angle(scan: object) -> float:
        """Direction of the most open space within ±70°, weighted towards straight on."""
        from raceforge.control import LidarScan

        assert isinstance(scan, LidarScan)
        best, best_angle = 0.0, 0.0
        for a, r in zip(scan.angles_rad, scan.ranges_m, strict=True):
            ang = math.remainder(a, 2 * math.pi)
            if r is None or abs(ang) > math.radians(70):
                continue
            score = min(r, 4.0) * math.cos(ang / 2)
            if score > best:
                best, best_angle = score, ang
        return best_angle

    @staticmethod
    def _push(scan: object, influence: float = 0.9) -> float:
        from raceforge.control import LidarScan

        assert isinstance(scan, LidarScan)
        push = 0.0
        for a, r in zip(scan.angles_rad, scan.ranges_m, strict=True):
            ang = math.remainder(a, 2 * math.pi)
            if r is not None and r < influence and abs(ang) < math.radians(120):
                push -= math.sin(ang) * (influence - r) / max(r, 0.05)
        return push
