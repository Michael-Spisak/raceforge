"""`RaceForgeEnv`: the Gymnasium environment for RL and imitation learning (spec 0013)."""

import math
from dataclasses import dataclass, field
from typing import Any

import gymnasium as gym
import numpy as np
from numpy.typing import NDArray

from raceforge.sim.engine import CarCommand, Simulation, centreline_follower
from raceforge.sim.simio import SimIO
from raceforge.train.common import TrackConfig, make_sim

SECTORS = 36
OBS_SIZE = SECTORS + 5  # sectors, speed, steering, yaw rate, last action (2)
EGO = "ego"


@dataclass(frozen=True)
class RewardWeights:
    progress: float = 1.0  # per metre along the centreline
    wall: float = 2.0  # per new wall/object contact
    car: float = 1.0  # per new car contact
    smooth: float = 0.05  # · |Δaction|²
    lap: float = 5.0
    finish: float = 20.0
    stuck: float = 20.0


@dataclass(frozen=True)
class EnvConfig:
    seeds: tuple[int, ...] = (0,)  # corridors, one per episode in turn
    length_m: float = 25.0
    laps: int = 1
    opponents: int = 0
    max_time_s: float = 120.0
    lidar_max_m: float = 4.0
    reverse_factor: float = 0.5
    max_steer_rate_rad_s: float = 3.0  # LEGO steering motor; protects the gears (docs/PLAN.md §4)
    opponent_speed_m_s: float = 0.25
    reward: RewardWeights = field(default_factory=RewardWeights)


def lidar_sectors(
    angles: NDArray[np.float64], ranges: list[float | None], max_m: float
) -> NDArray[np.float32]:
    """Min distance per 10° sector (sector 0 starts at the forward axis, counter-clockwise),
    divided by ``max_m``; sectors without a return read 1."""
    out = np.ones(SECTORS, dtype=np.float32)
    idx = (np.mod(angles, 2 * math.pi) / (2 * math.pi) * SECTORS).astype(int) % SECTORS
    for i, r in zip(idx, ranges, strict=True):
        if r is not None:
            out[i] = min(out[i], min(r, max_m) / max_m)
    return out


class RaceForgeEnv(gym.Env[NDArray[np.float32], NDArray[np.float32]]):
    """One car on a procedural corridor; the action is (steering, speed) in [-1, 1]."""

    metadata = {"render_modes": []}  # noqa: RUF012 - gymnasium API

    def __init__(self, config: EnvConfig | None = None) -> None:
        self.cfg = config or EnvConfig()
        self.observation_space = gym.spaces.Box(-1.0, 1.0, (OBS_SIZE,), np.float32)
        self.action_space = gym.spaces.Box(-1.0, 1.0, (2,), np.float32)
        self.sim: Simulation | None = None
        self.episode = 0

    # ------------------------------------------------------------------ gym API
    def reset(
        self, *, seed: int | None = None, options: dict[str, Any] | None = None
    ) -> tuple[NDArray[np.float32], dict[str, Any]]:
        super().reset(seed=seed)
        seeds = self.cfg.seeds
        track_seed = seeds[(seed if seed is not None else self.episode) % len(seeds)]
        self.episode += 1
        self.sim = make_sim(
            TrackConfig(
                seed=track_seed,
                length_m=self.cfg.length_m,
                laps=self.cfg.laps,
                opponents=self.cfg.opponents,
            )
        )
        info = SimIO(self.sim, EGO).info
        self.max_steer, self.top_speed = info.max_steer_rad, info.max_speed_m_s
        self.steer = 0.0
        self.last_action = np.zeros(2, dtype=np.float32)
        self.seen_events = 0
        self.laps = 0
        self.distance = self.sim.progress(EGO).distance_m
        return self._obs(), {"track_seed": track_seed}

    def step(
        self, action: NDArray[np.float32]
    ) -> tuple[NDArray[np.float32], float, bool, bool, dict[str, Any]]:
        sim = self.sim
        assert sim is not None, "call reset() first"
        a = np.clip(np.nan_to_num(np.asarray(action, dtype=np.float32)), -1.0, 1.0)
        target = float(a[0]) * self.max_steer
        rate = self.cfg.max_steer_rate_rad_s * sim.control_dt
        self.steer += max(-rate, min(rate, target - self.steer))
        speed = float(a[1]) * self.top_speed
        if speed < 0:
            speed *= self.cfg.reverse_factor
        sim.command(EGO, CarCommand(self.steer, speed))
        for i in range(self.cfg.opponents):
            name = f"opp{i + 1}"
            offset = 0.25 if i % 2 else -0.25
            sim.command(name, centreline_follower(sim, name, self.cfg.opponent_speed_m_s, offset))
        sim.step()

        w = self.cfg.reward
        pr = sim.progress(EGO)
        reward = w.progress * (pr.distance_m - self.distance)
        self.distance = pr.distance_m
        new = [e for e in sim.events[self.seen_events :] if e.car == EGO]
        self.seen_events = len(sim.events)
        stuck = any(e.kind == "stuck" for e in new)
        reward -= w.wall * sum(1 for e in new if e.kind in ("wall", "object"))
        reward -= w.car * sum(1 for e in new if e.kind == "car")
        reward -= w.smooth * float(np.sum((a - self.last_action) ** 2))
        reward += w.lap * (pr.laps - self.laps)
        self.laps = pr.laps
        if pr.finished:
            reward += w.finish
        if stuck:
            reward -= w.stuck
        self.last_action = a
        terminated = pr.finished
        truncated = not terminated and (stuck or sim.t >= self.cfg.max_time_s)
        info = {"distance_m": pr.distance_m, "laps": pr.laps, "t": sim.t, "stuck": stuck}
        return self._obs(), float(reward), terminated, truncated, info

    # ------------------------------------------------------------------ observation
    def _obs(self) -> NDArray[np.float32]:
        assert self.sim is not None
        r = self.sim.readings(EGO)
        obs = np.zeros(OBS_SIZE, dtype=np.float32)
        if r.lidar is not None:
            obs[:SECTORS] = lidar_sectors(
                np.asarray(r.lidar.angles_rad, dtype=np.float64),
                r.lidar.ranges_m,
                self.cfg.lidar_max_m,
            )
        else:
            obs[:SECTORS] = 1.0
        speed = r.speed_m_s.value if r.speed_m_s is not None else 0.0
        yaw_rate = r.yaw_rate_rad_s.value if r.yaw_rate_rad_s is not None else 0.0
        obs[SECTORS] = speed / self.top_speed
        obs[SECTORS + 1] = r.steering_rad / self.max_steer
        obs[SECTORS + 2] = yaw_rate / math.pi
        obs[SECTORS + 3 :] = self.last_action
        return np.clip(obs, -1.0, 1.0)
