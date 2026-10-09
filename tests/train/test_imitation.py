"""Spec 0023: behaviour cloning from recorded drives → ONNX policy that drives."""

from pathlib import Path

import pytest

pytest.importorskip("torch")
pytest.importorskip("onnxruntime")

from raceforge.api.service import TEMPLATES_DIR
from raceforge.control.controller import load_controller
from raceforge.sim.record import read_frames
from raceforge.sim.simio import SimIO, run_race
from raceforge.train.benchmark import BenchConfig
from raceforge.train.common import TrackConfig, corridor_seeds, make_sim
from raceforge.train.imitation import BCConfig, demo_samples, summarise, train_bc


def test_clone_a_controller_from_its_recordings(tmp_path: Path) -> None:
    files = []
    for seed in corridor_seeds(0, 2, 20.0):
        path = tmp_path / f"demo-{seed}.mcap"
        sim = make_sim(TrackConfig(seed=seed, length_m=20.0))
        run_race(sim, load_controller(TEMPLATES_DIR / "centering.py"), max_time_s=90, record=path)
        files.append(path)
    info = SimIO(make_sim(TrackConfig()), "ego").info
    states = tuple(sorted({f.state for f in read_frames(files[0])}))
    rec = summarise(files[0], states)
    assert rec.frames > 100 and rec.demo_frames == rec.frames and rec.duration_s > 1
    x, y = demo_samples(files[0], info, states=states)
    assert x.shape == (rec.frames, 41) and y.shape == (rec.frames, 2) and abs(y).max() <= 1.0
    assert demo_samples(files[0], info)[0].shape[0] == 0  # no teleop frames in a controller run

    out = tmp_path / "bc.yaml"
    cfg = BCConfig(
        epochs=8,
        states=states,
        bench=BenchConfig(tracks=1, length_m=20.0, max_time_s=60.0, workers=1),
        out=out,
    )
    res = train_bc(files, info, cfg)
    assert res.samples > 100 and res.validation is not None and res.validation.runs[0].error == ""
    assert load_controller(TEMPLATES_DIR / "onnx_policy.py", out).params.policy_onnx_b64  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(ValueError, match="demonstration frames"):
        train_bc(files, info, BCConfig(epochs=1, bench=None))  # default: teleop frames only


def test_bc_job_in_the_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from fastapi.testclient import TestClient

    from raceforge.api.service import Engine
    from raceforge.server.app import create_app

    monkeypatch.setenv("RACEFORGE_WORKSPACE_DIR", str(tmp_path / "workspace"))
    runs = tmp_path / "runs"
    runs.mkdir()
    sim = make_sim(TrackConfig(seed=0, length_m=20.0))
    run_race(
        sim,
        load_controller(TEMPLATES_DIR / "centering.py"),
        max_time_s=30,
        record=runs / "drive.mcap",
    )
    client = TestClient(create_app(Engine(), frontend_dist=None))
    [rec] = client.get("/api/v1/train/recordings").json()
    assert rec["name"] == "drive.mcap" and rec["frames"] > 100 and rec["demo_frames"] == 0
    race = {"tracks": 1, "length_m": 20.0, "max_time_s": 10.0}
    no_demo = client.post("/api/v1/train/bc", json={"epochs": 2, "race": race})
    assert no_demo.status_code == 422  # only teleop frames by default
    job = client.post(
        "/api/v1/train/bc", json={"epochs": 2, "all_states": True, "race": race}
    ).json()
    deadline = time.monotonic() + 120
    while job["state"] == "running" and time.monotonic() < deadline:
        time.sleep(0.5)
        job = client.get(f"/api/v1/train/jobs/{job['id']}").json()
    assert job["state"] == "done", job["error"]
    assert job["kind"] == "bc" and job["val_loss"] is not None and Path(job["out"]).is_file()
