"""Reinforcement learning v1 (spec 0022): PPO on `RaceForgeEnv`, ONNX export, benchmark.

Needs the optional extra ``rl`` (stable-baselines3, torch, onnx, onnxruntime; ADR-0030). The
trained policy is exported as ONNX (deterministic actor, actions clipped to [-1, 1]) and written
into a params YAML for the `onnx_policy` template, so it deploys and benchmarks like any controller.
"""

import base64
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from raceforge.train.benchmark import BenchConfig, BenchResult, benchmark
from raceforge.train.common import corridor_seeds
from raceforge.train.env import EnvConfig, RaceForgeEnv

TEMPLATE = Path(__file__).resolve().parents[3] / "controllers" / "templates" / "onnx_policy.py"


class RLCancelledError(Exception):
    pass


@dataclass(frozen=True)
class RLConfig:
    steps: int = 200_000
    train_tracks: int = 8
    env: EnvConfig = field(default_factory=EnvConfig)
    net: tuple[int, ...] = (64, 64)
    n_envs: int = 0  # 0: CPU count - 1 (subprocesses)
    seed: int = 0
    bench: BenchConfig = field(default_factory=BenchConfig)  # held-out evaluation
    out: Path | None = None  # params YAML for controllers/templates/onnx_policy.py


@dataclass(frozen=True)
class RLProgress:
    steps: int
    total: int
    mean_reward: float | None


@dataclass(frozen=True)
class RLResult:
    steps: int
    params: dict[str, Any]
    validation: BenchResult | None
    out: Path | None


def export_onnx(model: Any) -> bytes:
    """The PPO actor as ONNX: input ``obs`` (N, 41) float32 → ``action`` (N, 2) in [-1, 1]."""
    import torch  # pyright: ignore[reportMissingImports]

    policy = model.policy

    class Actor(torch.nn.Module):  # pyright: ignore[reportUntypedBaseClass]
        def __init__(self) -> None:
            super().__init__()
            self.extractor = policy.mlp_extractor
            self.action_net = policy.action_net

        def forward(self, obs: Any) -> Any:
            latent_pi, _ = self.extractor(obs)
            return torch.clamp(self.action_net(latent_pi), -1.0, 1.0)

    actor = Actor().eval()
    tmp = tempfile.mkdtemp(prefix="raceforge-onnx-")
    path = Path(tmp) / "policy.onnx"
    dummy = torch.zeros((1, policy.observation_space.shape[0]), dtype=torch.float32)
    torch.onnx.export(
        actor,
        (dummy,),
        str(path),
        input_names=["obs"],
        output_names=["action"],
        dynamic_axes={"obs": {0: "n"}, "action": {0: "n"}},
        opset_version=17,
        dynamo=False,
    )
    data = path.read_bytes()
    shutil.rmtree(tmp, ignore_errors=True)
    return data


def train_ppo(cfg: RLConfig, progress: Callable[[RLProgress], None] | None = None) -> RLResult:
    """Train PPO on procedural corridors (training seeds from 0) and evaluate on held-out ones."""
    from stable_baselines3 import PPO  # pyright: ignore[reportMissingImports]
    from stable_baselines3.common.callbacks import (
        BaseCallback,  # pyright: ignore[reportMissingImports]
    )
    from stable_baselines3.common.env_util import (
        make_vec_env,  # pyright: ignore[reportMissingImports]
    )
    from stable_baselines3.common.vec_env import (
        SubprocVecEnv,  # pyright: ignore[reportMissingImports]
    )

    seeds = (
        tuple(range(cfg.train_tracks))
        if cfg.env.quick is not None
        else corridor_seeds(0, cfg.train_tracks, cfg.env.length_m)
    )
    env_cfg = replace(cfg.env, seeds=seeds)
    n_envs = cfg.n_envs or max(1, (os.cpu_count() or 2) - 1)
    env = make_vec_env(
        lambda: RaceForgeEnv(env_cfg),
        n_envs=n_envs,
        seed=cfg.seed,
        vec_env_cls=SubprocVecEnv if n_envs > 1 else None,
    )

    class Report(BaseCallback):  # pyright: ignore[reportUntypedBaseClass]
        def _on_step(self) -> bool:
            return True

        def _on_rollout_end(self) -> None:
            if progress is None:
                return
            infos = list(self.model.ep_info_buffer or [])
            mean = sum(e["r"] for e in infos) / len(infos) if infos else None
            progress(RLProgress(self.num_timesteps, cfg.steps, mean))  # may raise to cancel

    model = PPO(
        "MlpPolicy",
        env,
        seed=cfg.seed,
        verbose=0,
        n_steps=max(64, 1024 // n_envs),
        batch_size=64,
        policy_kwargs={"net_arch": list(cfg.net)},
        device="cpu",
    )
    try:
        model.learn(total_timesteps=cfg.steps, callback=Report())
    finally:
        env.close()
    io_cfg = env_cfg.policy_io
    params = {
        "policy_onnx_b64": base64.b64encode(export_onnx(model)).decode(),
        "lidar_max_m": io_cfg.lidar_max_m,
        "reverse_factor": io_cfg.reverse_factor,
        "max_steer_rate_rad_s": io_cfg.max_steer_rate_rad_s,
    }
    out = cfg.out
    validation = None
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        header = f"# PPO policy, {model.num_timesteps} steps, raceforge train rl (spec 0022)\n"
        out.write_text(header + yaml.safe_dump(params, sort_keys=False), encoding="utf-8")
        validation = benchmark(TEMPLATE, out, cfg.bench)
    return RLResult(model.num_timesteps, params, validation, out)
