"""Spec 0013 Train tab: benchmark jobs run in the engine and report progress."""

import time

from fastapi.testclient import TestClient

from raceforge.api.service import TEMPLATES_DIR, Engine
from raceforge.server.app import create_app

RACE = {"tracks": 2, "length_m": 20.0, "max_time_s": 120.0}


def test_benchmark_job_runs_to_done() -> None:
    client = TestClient(create_app(Engine(), frontend_dist=None))
    body = {"controller": str(TEMPLATES_DIR / "centering.py"), "race": RACE}
    r = client.post("/api/v1/train/benchmark", json=body)
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["state"] == "running" and job["total"] == 2
    assert client.post("/api/v1/train/benchmark", json=body).status_code == 409  # one at a time
    deadline = time.monotonic() + 120
    while job["state"] == "running" and time.monotonic() < deadline:
        time.sleep(0.5)
        job = client.get(f"/api/v1/train/jobs/{job['id']}").json()
    assert job["state"] == "done", job
    assert len(job["runs"]) == 2 and job["score"] > 0 and job["finished_rate"] > 0
    assert client.get("/api/v1/train/jobs").json()[0]["id"] == job["id"]


def test_bad_controller_is_rejected() -> None:
    client = TestClient(create_app(Engine(), frontend_dist=None))
    r = client.post("/api/v1/train/benchmark", json={"controller": "/nope/missing.py"})
    assert r.status_code == 422
