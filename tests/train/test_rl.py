"""Spec 0022: PPO policy → ONNX gives the same actions; the onnx_policy template drives with it."""

import base64
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("stable_baselines3")
pytest.importorskip("onnxruntime")

import onnxruntime as ort
from stable_baselines3 import PPO

from raceforge.control.controller import load_controller
from raceforge.control.policy_io import OBS_SIZE
from raceforge.train.benchmark import BenchConfig, benchmark
from raceforge.train.env import EnvConfig, RaceForgeEnv
from raceforge.train.rl import TEMPLATE, RLConfig, export_onnx, train_ppo


def test_onnx_export_matches_the_policy() -> None:
    env = RaceForgeEnv(EnvConfig(length_m=20.0))
    model = PPO(
        "MlpPolicy",
        env,
        seed=0,
        n_steps=64,
        batch_size=32,
        policy_kwargs={"net_arch": [32, 32]},
        device="cpu",
    )
    model.learn(total_timesteps=128)
    session = ort.InferenceSession(export_onnx(model), providers=["CPUExecutionProvider"])
    obs = np.random.default_rng(0).uniform(-1, 1, (8, OBS_SIZE)).astype(np.float32)
    onnx_actions = np.asarray(session.run(None, {"obs": obs})[0], dtype=np.float32)
    sb3_actions, _ = model.predict(obs, deterministic=True)
    np.testing.assert_allclose(onnx_actions, np.clip(sb3_actions, -1, 1), atol=1e-5)


def test_train_writes_a_deployable_policy(tmp_path: Path) -> None:
    out = tmp_path / "policy.yaml"
    cfg = RLConfig(
        steps=512,
        train_tracks=1,
        n_envs=1,
        net=(32, 32),
        env=EnvConfig(length_m=20.0, max_time_s=30.0),
        bench=BenchConfig(tracks=1, length_m=20.0, max_time_s=20.0, workers=1),
        out=out,
    )
    res = train_ppo(cfg)
    assert res.steps >= 512 and res.validation is not None and len(res.validation.runs) == 1
    ctrl = load_controller(TEMPLATE, out)
    assert base64.b64decode(ctrl.params.policy_onnx_b64)[:2] != b""  # pyright: ignore[reportAttributeAccessIssue]
    # same params also drive in a normal benchmark (as on the car)
    assert (
        benchmark(TEMPLATE, out, BenchConfig(tracks=1, length_m=20.0, max_time_s=10.0, workers=1))
        .runs[0]
        .error
        == ""
    )


def test_rl_job_in_the_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from fastapi.testclient import TestClient

    from raceforge.api.service import Engine
    from raceforge.server.app import create_app

    monkeypatch.setenv("RACEFORGE_WORKSPACE_DIR", str(tmp_path / "workspace"))
    client = TestClient(create_app(Engine(), frontend_dist=None))
    race = {"tracks": 1, "length_m": 20.0, "max_time_s": 10.0}
    job = client.post(
        "/api/v1/train/rl", json={"steps": 256, "train_tracks": 1, "race": race}
    ).json()
    assert job["kind"] == "rl" and job["total"] == 256
    deadline = time.monotonic() + 120
    while job["state"] == "running" and time.monotonic() < deadline:
        time.sleep(0.5)
        job = client.get(f"/api/v1/train/jobs/{job['id']}").json()
    assert job["state"] == "done", job["error"]
    assert job["steps_done"] >= 256 and Path(job["out"]).is_file()
    assert Path(job["out"]).parent == tmp_path / "policies"
