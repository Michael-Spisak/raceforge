"""Template: drive the racing line with a map and a particle filter (spec 0029).

Layers (PLAN §5b), highest priority first:
1. SAFETY: something close in front -> slow down and steer to the open side.
2. RECOVER: stuck (commanded to move but not moving) -> reverse with opposite steering.
3. LINE: localised (confidence >= confidence_drive) -> pure pursuit on the racing line, speed from
   the speed profile x speed_scale.
4. REACTIVE: not sure where we are -> stay in the middle of the corridor on the LiDAR alone.

The map and the racing line come from `raceforge localise TRACK` (params YAML). The pose is
published as pose.x / pose.y / pose.yaw / pose.conf so the Live dashboard can draw it.
"""

import math

import numpy as np
from pydantic import Field

from raceforge.control import (
    Command,
    Controller,
    ControllerParams,
    Observation,
    RobotInfo,
    Tunable,
    clamp,
    sector_min,
)
from raceforge.control.localisation import DistanceMap, ParticleFilter, decode_field


class LocalisedParams(ControllerParams):
    track: str = ""
    map_b64: str = ""
    map_origin: tuple[float, float] = (0.0, 0.0)
    map_resolution_m: float = 0.05
    map_width: int = 0
    map_height: int = 0
    line: list[tuple[float, float, float]] = Field(default_factory=list)  # x, y, speed
    loop: bool = True
    start: tuple[float, float, float] = (0.0, 0.0, 0.0)
    particles: float = Tunable(300, 100, 1000, step=50, description="particles in the filter")
    beams: float = Tunable(30, 10, 60, step=5, description="LiDAR beams used per update")
    confidence_drive: float = Tunable(
        0.55, 0.3, 0.9, description="confidence needed to drive the line"
    )
    lookahead_m: float = Tunable(0.6, 0.2, 1.5, description="pure pursuit look-ahead")
    speed_scale: float = Tunable(0.8, 0.2, 1.5, description="x speed profile of the racing line")
    fallback_speed_m_s: float = Tunable(0.3, 0.1, 1.0, description="speed while not localised")
    obstacle_front_m: float = Tunable(0.45, 0.2, 1.0, description="front gap that slows down")
    k_center: float = Tunable(1.2, 0.0, 4.0, description="reactive: steering per metre off-centre")


class Localised(Controller[LocalisedParams]):
    Params = LocalisedParams

    def __init__(self, params: LocalisedParams | None = None) -> None:
        super().__init__(params)
        self.pf: ParticleFilter | None = None
        self.line = np.zeros((0, 3))
        self.idx = 0
        self.wheelbase = 0.18
        self.stuck_s = 0.0
        self.recover_s = 0.0
        self.last_scan_t: float | None = None

    def setup(self, info: RobotInfo) -> None:
        p = self.params
        self.wheelbase = info.wheelbase_m
        if not p.map_b64 or len(p.line) < 3:
            return  # no map: the controller drives reactively only
        cells = decode_field(p.map_b64, p.map_width, p.map_height)
        world = DistanceMap(cells, p.map_origin[0], p.map_origin[1], p.map_resolution_m)
        self.pf = ParticleFilter(world, particles=int(p.particles), beams=int(p.beams))
        self.pf.init_around(*p.start)
        self.line = np.array(p.line, dtype=np.float64)

    # ------------------------------------------------------------------ helpers
    def _nearest(self, x: float, y: float, conf: float) -> int:
        n = len(self.line)
        if conf < 0.5:  # unsure: search the whole line
            d = (self.line[:, 0] - x) ** 2 + (self.line[:, 1] - y) ** 2
            return int(np.argmin(d))
        window = [
            (self.idx + k) % n if self.params.loop else min(n - 1, self.idx + k)
            for k in range(-5, 40)
        ]
        best = min(window, key=lambda i: (self.line[i, 0] - x) ** 2 + (self.line[i, 1] - y) ** 2)
        return int(best)

    def _pursuit(self, x: float, y: float, yaw: float, conf: float) -> tuple[float, float]:
        p = self.params
        self.idx = self._nearest(x, y, conf)
        n = len(self.line)
        j, travelled = self.idx, 0.0
        while travelled < p.lookahead_m:
            k = (j + 1) % n if p.loop else min(n - 1, j + 1)
            if k == j:
                break
            travelled += math.hypot(
                self.line[k, 0] - self.line[j, 0], self.line[k, 1] - self.line[j, 1]
            )
            j = k
        tx, ty = self.line[j, 0] - x, self.line[j, 1] - y
        alpha = math.atan2(ty, tx) - yaw
        dist = max(math.hypot(tx, ty), 1e-3)
        steering = math.atan2(2 * self.wheelbase * math.sin(alpha), dist)
        return steering, float(self.line[self.idx, 2]) * p.speed_scale

    @staticmethod
    def _reactive(obs: Observation, k: float) -> float:
        assert obs.lidar is not None
        left = sector_min(obs.lidar, 60, 120)
        right = sector_min(obs.lidar, -120, -60)
        if left is None or right is None:
            return 0.0
        return k * (min(left, 1.5) - min(right, 1.5)) / 2

    # ------------------------------------------------------------------ step
    def step(self, obs: Observation) -> Command:
        p = self.params
        if obs.lidar is None:
            self.state = "no_lidar"
            return Command()
        conf = 0.0
        if self.pf is not None:
            self.pf.predict(obs.speed_m_s or 0.0, obs.yaw_rate_rad_s or 0.0, obs.dt_s)
            if obs.lidar.t_s != self.last_scan_t:  # one update per LiDAR revolution
                self.last_scan_t = obs.lidar.t_s
                self.pf.update(obs.lidar.angles_rad, obs.lidar.ranges_m)
            est = self.pf.estimate()
            conf = est.confidence
            self.emit("pose.x", round(est.x, 3))
            self.emit("pose.y", round(est.y, 3))
            self.emit("pose.yaw", round(est.yaw, 3))
            self.emit("pose.conf", round(conf, 3))

        moving = obs.speed_m_s is not None and abs(obs.speed_m_s) > 0.03
        self.stuck_s = 0.0 if moving else self.stuck_s + obs.dt_s
        if self.recover_s > 0 or self.stuck_s > 1.0:
            self.recover_s = 1.2 if self.recover_s <= 0 else self.recover_s - obs.dt_s
            self.stuck_s = 0.0
            self.state = "recover"
            self.emit("loc.mode", "recover")
            return Command(steering_rad=-self._reactive(obs, p.k_center), speed_m_s=-0.2)

        front = sector_min(obs.lidar, -20, 20)
        if self.pf is not None and conf >= p.confidence_drive:
            est = self.pf.estimate()
            steering, speed = self._pursuit(est.x, est.y, est.yaw, conf)
            self.state = "line"
        else:
            steering, speed = self._reactive(obs, p.k_center), p.fallback_speed_m_s
            self.state = "reactive"
        if front is not None and front < p.obstacle_front_m:
            speed = min(speed, p.fallback_speed_m_s * 0.5)
            steering += 0.5 * clamp(self._reactive(obs, 1.0), -0.4, 0.4)
            self.state = "safety"
        self.emit("loc.mode", self.state)
        return Command(steering_rad=steering, speed_m_s=speed)
