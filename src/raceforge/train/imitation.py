"""Imitation learning v1 (spec 0023): behaviour cloning from recorded drives.

Demonstrations are run logs (MCAP telemetry, spec 0003/0005) — teleop drives in the simulator or on
the real car. Each frame becomes (observation, action): the observation is built with the same
``raceforge.control.policy_io`` code the car controller uses, the action is the driven command
normalised like a policy action. A small MLP learns the mapping (torch, optional extra ``rl``), is
exported to ONNX and written into a params YAML for ``controllers/templates/onnx_policy.py``.
"""

import base64
import math
import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from numpy.typing import NDArray

from raceforge.control.policy_io import DEFAULT_IO, OBS_SIZE, PolicyIO, observation_vector
from raceforge.control.types import LidarScan, Observation, RobotInfo
from raceforge.core.telemetry import RangeArray, TelemetryFrame
from raceforge.sim.record import read_frames
from raceforge.train.benchmark import BenchConfig, BenchResult, benchmark
from raceforge.train.rl import TEMPLATE

DEMO_STATES = ("teleop",)


@dataclass(frozen=True)
class Recording:
    path: Path
    frames: int
    demo_frames: int  # frames in a demonstration state (teleop)
    duration_s: float


def summarise(path: Path, states: Sequence[str] = DEMO_STATES) -> Recording:
    frames = read_frames(path)
    t = [f.t.mono_ns / 1e9 for f in frames]
    return Recording(
        path=path,
        frames=len(frames),
        demo_frames=sum(1 for f in frames if f.state in states),
        duration_s=(t[-1] - t[0]) if len(t) > 1 else 0.0,
    )


def _observation(frame: TelemetryFrame) -> Observation:
    lidar = None
    raw = frame.meas.sensors.get("lidar")
    if isinstance(raw, RangeArray):
        angles = tuple(
            raw.angle_min_rad + i * raw.angle_increment_rad for i in range(len(raw.ranges))
        )
        lidar = LidarScan(angles, tuple(raw.ranges), frame.t.mono_ns / 1e9)
    return Observation(
        t_s=frame.t.mono_ns / 1e9,
        dt_s=1 / frame.loop.rate_hz if frame.loop.rate_hz else 0.02,
        lidar=lidar,
        speed_m_s=frame.meas.speed_m_s,
        steering_rad=frame.meas.steering_rad,
        yaw_rate_rad_s=frame.meas.yaw_rate_rad_s,
    )


def _action(frame: TelemetryFrame, info: RobotInfo, cfg: PolicyIO) -> tuple[float, float]:
    """Inverse of ``policy_io.action_to_command`` (without the steering rate limit)."""
    steer = frame.cmd.steering_rad / info.max_steer_rad
    speed = frame.cmd.speed_m_s / info.max_speed_m_s
    if speed < 0:
        speed /= cfg.reverse_factor
    return (max(-1.0, min(1.0, steer)), max(-1.0, min(1.0, speed)))


def demo_samples(
    path: Path, info: RobotInfo, cfg: PolicyIO = DEFAULT_IO, states: Sequence[str] = DEMO_STATES
) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
    """(observations N x 41, actions N x 2) of the demonstration frames of one recording."""
    xs: list[list[float]] = []
    ys: list[tuple[float, float]] = []
    last = (0.0, 0.0)
    for frame in read_frames(path):
        action = _action(frame, info, cfg)
        if frame.state in states:
            xs.append(observation_vector(_observation(frame), info, last, cfg))
            ys.append(action)
        last = action
    x = np.asarray(xs, dtype=np.float32).reshape(-1, OBS_SIZE)
    return x, np.asarray(ys, dtype=np.float32).reshape(-1, 2)


def sim_robot_info() -> RobotInfo:
    """Limits of the simulator car used to normalise recorded commands."""
    from raceforge.sim.simio import SimIO
    from raceforge.train.common import TrackConfig, make_sim

    return SimIO(make_sim(TrackConfig()), "ego").info


@dataclass(frozen=True)
class BCConfig:
    epochs: int = 60
    hidden: tuple[int, ...] = (64, 64)
    lr: float = 1e-3
    batch: int = 256
    val_fraction: float = 0.15  # the end of each recording (later frames) validates
    seed: int = 0
    io: PolicyIO = field(default_factory=PolicyIO)
    states: tuple[str, ...] = DEMO_STATES
    bench: BenchConfig | None = field(default_factory=BenchConfig)
    out: Path | None = None


@dataclass(frozen=True)
class BCProgress:
    epoch: int
    epochs: int
    train_loss: float
    val_loss: float


@dataclass(frozen=True)
class BCResult:
    samples: int
    best_epoch: int
    val_loss: float
    params: dict[str, Any]
    validation: BenchResult | None
    out: Path | None


def train_bc(
    recordings: Sequence[Path],
    info: RobotInfo,
    cfg: BCConfig | None = None,
    progress: Callable[[BCProgress], None] | None = None,
) -> BCResult:
    import torch  # pyright: ignore[reportMissingImports]

    cfg = cfg or BCConfig()
    xs_train, ys_train, xs_val, ys_val = [], [], [], []
    for path in recordings:
        x, y = demo_samples(path, info, cfg.io, cfg.states)
        cut = len(x) - max(1, int(len(x) * cfg.val_fraction)) if len(x) > 4 else len(x)
        xs_train.append(x[:cut])
        ys_train.append(y[:cut])
        xs_val.append(x[cut:])
        ys_val.append(y[cut:])
    x_tr, y_tr = np.concatenate(xs_train), np.concatenate(ys_train)
    x_va, y_va = np.concatenate(xs_val), np.concatenate(ys_val)
    if len(x_tr) < 20:
        raise ValueError(
            f"only {len(x_tr)} demonstration frames: drive with teleop and record the run first"
        )
    if len(x_va) == 0:
        x_va, y_va = x_tr, y_tr

    torch.manual_seed(cfg.seed)
    layers: list[Any] = []
    width = OBS_SIZE
    for h in cfg.hidden:
        layers += [torch.nn.Linear(width, h), torch.nn.Tanh()]
        width = h
    layers += [torch.nn.Linear(width, 2), torch.nn.Tanh()]
    net = torch.nn.Sequential(*layers)
    opt = torch.optim.Adam(net.parameters(), lr=cfg.lr)
    xt, yt = torch.from_numpy(x_tr), torch.from_numpy(y_tr)
    xv, yv = torch.from_numpy(x_va), torch.from_numpy(y_va)
    gen = torch.Generator().manual_seed(cfg.seed)
    best = (math.inf, 0, {k: v.clone() for k, v in net.state_dict().items()})
    for epoch in range(1, cfg.epochs + 1):
        net.train()
        order = torch.randperm(len(xt), generator=gen)
        total = 0.0
        for i in range(0, len(xt), cfg.batch):
            idx = order[i : i + cfg.batch]
            opt.zero_grad()
            loss = torch.nn.functional.mse_loss(net(xt[idx]), yt[idx])
            loss.backward()
            opt.step()
            total += loss.item() * len(idx)
        net.eval()
        with torch.no_grad():
            val = float(torch.nn.functional.mse_loss(net(xv), yv))
        if val < best[0]:
            best = (val, epoch, {k: v.clone() for k, v in net.state_dict().items()})
        if progress:
            progress(BCProgress(epoch, cfg.epochs, total / len(xt), val))  # may raise to cancel
    net.load_state_dict(best[2])
    net.eval()

    tmp = tempfile.mkdtemp(prefix="raceforge-bc-")
    onnx_path = Path(tmp) / "policy.onnx"
    torch.onnx.export(
        net,
        (torch.zeros((1, OBS_SIZE)),),
        str(onnx_path),
        input_names=["obs"],
        output_names=["action"],
        dynamic_axes={"obs": {0: "n"}, "action": {0: "n"}},
        opset_version=17,
        dynamo=False,
    )
    params = {
        "policy_onnx_b64": base64.b64encode(onnx_path.read_bytes()).decode(),
        "lidar_max_m": cfg.io.lidar_max_m,
        "reverse_factor": cfg.io.reverse_factor,
        "max_steer_rate_rad_s": cfg.io.max_steer_rate_rad_s,
    }
    shutil.rmtree(tmp, ignore_errors=True)
    validation = None
    if cfg.out is not None:
        cfg.out.parent.mkdir(parents=True, exist_ok=True)
        header = (
            f"# Behaviour cloning from {len(recordings)} recording(s), {len(x_tr)} frames, "
            f"val loss {best[0]:.4f} (raceforge train bc, spec 0023)\n"
        )
        cfg.out.write_text(header + yaml.safe_dump(params, sort_keys=False), encoding="utf-8")
        if cfg.bench is not None:
            validation = benchmark(TEMPLATE, cfg.out, cfg.bench)
    return BCResult(len(x_tr), best[1], best[0], params, validation, cfg.out)
