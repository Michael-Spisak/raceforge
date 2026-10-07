"""Sensor models with rate, latency, noise and dropouts (spec 0003).

Every model samples the simulator at its own rate, then delivers the value after its latency.
Randomness comes from a per-sensor ``numpy.random.Generator`` so runs are reproducible.
"""

import math
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from raceforge.core.devices import GyroParams, Lidar2DParams, RangeSensorParams
from raceforge.sim.mj import mujoco

type Arr = npt.NDArray[np.float64]
# Rays see the floor (0), car chassis (1) and track (2); wheels (3) are excluded.
GEOMGROUP = np.array([1, 1, 1, 0, 0, 0], dtype=np.uint8)


class Env(Protocol):
    """What sensors need from the simulation."""

    model: Any
    data: Any
    surface_of_geom: list[str]
    reflectivity_of_geom: list[float]


@dataclass(frozen=True)
class Stamped[T]:
    value: T
    t_sample: float  # when the physical quantity was measured


@dataclass(frozen=True)
class LidarScan:
    angles_rad: Arr  # in the sensor frame, counter-clockwise from the forward axis
    ranges_m: list[float | None]
    t_start: float
    t_end: float


class _Delayed[T]:
    def __init__(self, rate_hz: float, latency_s: float) -> None:
        self.period = 1.0 / rate_hz
        self.latency = latency_s
        self.next_sample = 0.0
        self.queue: deque[tuple[float, Stamped[T]]] = deque()
        self.latest: Stamped[T] | None = None

    def due(self, t: float) -> bool:
        if t + 1e-9 >= self.next_sample:
            self.next_sample += self.period * max(
                1, math.floor((t - self.next_sample) / self.period) + 1
            )
            return True
        return False

    def push(self, t: float, value: T) -> None:
        self.queue.append((t + self.latency, Stamped(value, t)))

    def deliver(self, t: float) -> Stamped[T] | None:
        while self.queue and self.queue[0][0] <= t + 1e-9:
            self.latest = self.queue.popleft()[1]
        return self.latest


def _fan(env: Env, site: str, body: int, angles: Arr) -> tuple[Arr, Arr]:
    """Ray origin and directions fanned around the car's up axis, centred on the sense axis."""
    s = env.data.site(site)
    fwd = np.array(s.xmat).reshape(3, 3)[:, 2]
    up = np.array(env.data.xmat[body]).reshape(3, 3)[:, 2]
    fwd = fwd - up * float(fwd @ up)
    fwd /= np.linalg.norm(fwd)
    left = np.cross(up, fwd)
    dirs = np.cos(angles)[:, None] * fwd + np.sin(angles)[:, None] * left
    return np.array(s.xpos), dirs


class Ultrasonic:
    RAYS = 7
    CONE_RAD = math.radians(15)
    DROPOUT_INCIDENCE_RAD = math.radians(50)

    def __init__(
        self,
        site: str,
        params: RangeSensorParams,
        rng: np.random.Generator,
        body_exclude: int,
        latency_s: float = 0.03,
        crosstalk_prob: float = 0.0,
    ) -> None:
        self.site, self.p, self.rng, self.body_exclude = site, params, rng, body_exclude
        self.crosstalk_prob = crosstalk_prob
        self.timing = _Delayed[float | None](params.rate_hz, latency_s)
        self.offsets = np.linspace(-self.CONE_RAD, self.CONE_RAD, self.RAYS)

    def sample(self, env: Env, t: float, others_firing: int = 0) -> None:
        if not self.timing.due(t):
            return
        pos, dirs = _fan(env, self.site, self.body_exclude, self.offsets)
        geomid = np.zeros(self.RAYS, dtype=np.int32)
        dist = np.zeros(self.RAYS)
        normal = np.zeros(self.RAYS * 3)
        mujoco.mj_multiRay(
            env.model,
            env.data,
            pos,
            dirs.flatten(),
            GEOMGROUP,
            1,
            self.body_exclude,
            geomid,
            dist,
            normal,
            self.RAYS,
            self.p.range_max_m + 0.5,
        )
        # First echo: the nearest hit among rays that meet their surface steeply enough to reflect
        # back (incidence <= 50°). Only if no ray qualifies the reading drops out.
        best: float | None = None
        for k in range(self.RAYS):
            if geomid[k] < 0 or dist[k] < 0 or dist[k] > self.p.range_max_m:
                continue
            n = normal[3 * k : 3 * k + 3]
            norm = float(np.linalg.norm(n)) or 1.0
            incidence = math.acos(min(1.0, abs(float(dirs[k] @ n)) / norm))
            if incidence <= self.DROPOUT_INCIDENCE_RAD and (best is None or dist[k] < best):
                best = float(dist[k])
        value: float | None = None
        if best is not None:
            noisy = max(self.p.range_min_m, best + self.rng.normal(0.0, self.p.noise_std_m))
            value = round(noisy, 3)
        if others_firing and self.rng.random() < self.crosstalk_prob * others_firing:
            value = round(float(self.rng.uniform(self.p.range_min_m, 0.5)), 3)
        self.timing.push(t, value)

    def read(self, t: float) -> Stamped[float | None] | None:
        return self.timing.deliver(t)


class Gyro:
    def __init__(
        self,
        params: GyroParams,
        rng: np.random.Generator,
        gyro_sensor: str,
        latency_s: float = 0.01,
    ) -> None:
        self.p, self.rng, self.sensor = params, rng, gyro_sensor
        self.timing = _Delayed[tuple[float, float]](params.rate_hz, latency_s)
        self.bias = float(rng.normal(0.0, params.drift_rad_s))
        self.heading = 0.0
        self.last_t: float | None = None

    def sample(self, env: Env, t: float) -> None:
        if not self.timing.due(t):
            return
        true_rate = float(env.data.sensor(self.sensor).data[2])
        dt = 0.0 if self.last_t is None else t - self.last_t
        self.last_t = t
        self.bias += float(
            self.rng.normal(0.0, self.p.drift_rad_s * 0.1 * math.sqrt(max(dt, 1e-9)))
        )
        raw = true_rate + self.bias + float(self.rng.normal(0.0, self.p.noise_std_rad_s))
        quantised = math.radians(round(math.degrees(raw)))  # EV3 gyro: integer °/s
        self.heading += quantised * dt
        self.timing.push(t, (quantised, self.heading))

    def read(self, t: float) -> Stamped[tuple[float, float]] | None:
        return self.timing.deliver(t)


class Lidar:
    """Rotating 2D LiDAR; each call casts the slice of the revolution since the last call."""

    def __init__(
        self,
        site: str,
        params: Lidar2DParams,
        rng: np.random.Generator,
        body_exclude: int,
        latency_s: float = 0.02,
    ) -> None:
        self.site, self.p, self.rng, self.body_exclude = site, params, rng, body_exclude
        self.n = round(2 * math.pi / params.angular_res_rad)
        self.angles = np.arange(self.n) * (2 * math.pi / self.n)
        self.period = 1.0 / params.rate_hz
        self.latency = latency_s
        self.cursor = 0
        self.scan_start = 0.0
        self.ranges: list[float | None] = [None] * self.n
        self.queue: deque[tuple[float, LidarScan]] = deque()
        self.latest: LidarScan | None = None

    def sample(self, env: Env, t: float) -> None:
        phase = (t - self.scan_start) / self.period
        target = min(self.n, round(phase * self.n))
        if target <= self.cursor and phase < 1.0:
            return
        idx = np.arange(self.cursor, target)
        if len(idx):
            pos, dirs = _fan(env, self.site, self.body_exclude, self.angles[idx])
            geomid = np.zeros(len(idx), dtype=np.int32)
            dist = np.zeros(len(idx))
            mujoco.mj_multiRay(
                env.model,
                env.data,
                pos,
                dirs.flatten(),
                GEOMGROUP,
                1,
                self.body_exclude,
                geomid,
                dist,
                None,
                len(idx),
                self.p.range_max_m,
            )
            for k, i in enumerate(idx):
                g, d = int(geomid[k]), float(dist[k])
                value: float | None = None
                if g >= 0 and self.p.range_min_m <= d <= self.p.range_max_m:
                    reflect = env.reflectivity_of_geom[g]
                    drop = (
                        max(0.0, 0.5 - reflect) * 1.6
                    )  # glass (0.1) -> 64 % dropout, normal walls -> 0
                    if self.rng.random() >= drop:
                        value = round(
                            d + float(self.rng.normal(0.0, self.p.noise_std_m * (0.5 + d / 4))), 4
                        )
                self.ranges[i] = value
        self.cursor = target
        if phase >= 1.0:
            scan = LidarScan(
                np.asarray(self.angles, dtype=np.float64), list(self.ranges), self.scan_start, t
            )
            self.queue.append((t + self.latency, scan))
            self.scan_start, self.cursor = t, 0
            self.ranges = [None] * self.n

    def read(self, t: float) -> LidarScan | None:
        while self.queue and self.queue[0][0] <= t + 1e-9:
            self.latest = self.queue.popleft()[1]
        return self.latest


class Encoders:
    """EV3 tacho emulation: 360 counts per motor revolution, speed from count differences."""

    COUNTS_PER_REV = 360

    def __init__(
        self, joints: list[str], gear_ratio: float, wheel_radius: float, rate_hz: float = 100.0
    ) -> None:
        self.joints, self.ratio, self.r = joints, gear_ratio, wheel_radius
        self.timing = _Delayed[float](rate_hz, 0.0)
        self.counts: int | None = None
        self.last_t = 0.0

    def sample(self, env: Env, t: float) -> None:
        if not self.timing.due(t):
            return
        wheel_angle = float(np.mean([env.data.joint(j).qpos[0] for j in self.joints]))
        counts = math.floor(wheel_angle * self.ratio / (2 * math.pi) * self.COUNTS_PER_REV)
        if self.counts is None:
            self.counts, self.last_t = counts, t
            self.timing.push(t, 0.0)
            return
        dt = max(t - self.last_t, 1e-9)
        speed = (
            (counts - self.counts) / self.COUNTS_PER_REV * 2 * math.pi / self.ratio * self.r / dt
        )
        self.counts, self.last_t = counts, t
        self.timing.push(t, speed)

    def read(self, t: float) -> Stamped[float] | None:
        return self.timing.deliver(t)
