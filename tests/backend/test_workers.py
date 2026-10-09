"""Spec 0020: worker registration, job queue, and the worker token's limits (security gate)."""

from pathlib import Path
from typing import cast

import httpx

from raceforge.workspace.client import ClientFactory
from tests.backend.conftest import Env, Team


def _direct(env: Env) -> ClientFactory:
    """The backend test client as client factory (TestClient may be httpx2-based, same API)."""
    return lambda _: cast(httpx.Client, env.client)


SOURCE = "from raceforge.control.controller import Controller\n"


def _job(env: Env, team: Team) -> dict[str, object]:
    r = env.client.post(
        f"/api/v1/workspaces/{team.ws}/jobs",
        headers=team.member,
        json={
            "kind": "benchmark",
            "request": {"tracks": 2},
            "controller_name": "mine.py",
            "controller_source": SOURCE,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_worker_token_can_only_use_worker_endpoints(env: Env, team: Team) -> None:
    r = env.client.post(
        "/api/v1/workers", headers=team.member, json={"workspace_id": team.ws, "name": "lab-pc"}
    )
    assert r.status_code == 201, r.text
    token = r.json()["token"]
    assert token.startswith("rfw_")
    w = {"Authorization": f"Bearer {token}"}
    assert env.client.get("/api/v1/workspaces", headers=w).status_code == 403
    assert env.client.get(f"/api/v1/workspaces/{team.ws}/jobs", headers=w).status_code == 403
    assert env.client.post(
        "/api/v1/tokens", headers=w, json={"name": "x", "scopes": ["read"]}
    ).status_code in (403, 422)
    # a normal login cannot act as a worker, and no API token can get the worker scope
    assert env.client.post("/api/v1/worker/claim", headers=team.member).status_code == 403
    r = env.client.post(
        "/api/v1/tokens",
        headers=team.member,
        json={"name": "w", "scopes": ["worker"], "client": "worker"},
    )
    assert r.status_code == 422


def test_queue_claim_progress_cancel_finish_remove(env: Env, team: Team) -> None:
    reg = env.client.post(
        "/api/v1/workers", headers=team.member, json={"workspace_id": team.ws, "name": "lab-pc"}
    ).json()
    w = {"Authorization": f"Bearer {reg['token']}"}
    assert env.client.post("/api/v1/worker/claim", headers=w).status_code == 204  # nothing queued
    job = _job(env, team)
    claimed = env.client.post("/api/v1/worker/claim", headers=w)
    assert claimed.status_code == 200 and claimed.json()["controller_source"] == SOURCE
    assert env.client.post("/api/v1/worker/claim", headers=w).status_code == 409  # one at a time
    jid = job["id"]
    ack = env.client.post(
        f"/api/v1/worker/jobs/{jid}/progress",
        headers=w,
        json={"progress": {"done": 1}, "log": ["run 1 ok"]},
    ).json()
    assert ack == {"cancel": False}
    info = env.client.get(f"/api/v1/jobs/{jid}", headers=team.member).json()
    assert (
        info["status"] == "running"
        and info["worker_name"] == "lab-pc"
        and info["log_tail"] == ["run 1 ok"]
    )
    env.client.post(f"/api/v1/jobs/{jid}/cancel", headers=team.member)
    ack = env.client.post(f"/api/v1/worker/jobs/{jid}/progress", headers=w, json={}).json()
    assert ack == {"cancel": True}
    done = env.client.post(
        f"/api/v1/worker/jobs/{jid}/finish", headers=w, json={"status": "cancelled"}
    )
    assert done.json()["status"] == "cancelled"

    second = _job(env, team)
    env.client.post("/api/v1/worker/claim", headers=w)
    result = {"score": 61.1, "finished_rate": 1.0}
    env.client.post(
        f"/api/v1/worker/jobs/{second['id']}/finish",
        headers=w,
        json={"status": "done", "result": result},
    )
    assert (
        env.client.get(f"/api/v1/jobs/{second['id']}", headers=team.member).json()["result"]
        == result
    )

    workers = env.client.get(f"/api/v1/workspaces/{team.ws}/workers", headers=team.member).json()
    assert workers[0]["online"] and not workers[0]["busy"]
    assert (
        env.client.delete(f"/api/v1/workers/{reg['worker']['id']}", headers=team.member).status_code
        == 204
    )
    assert env.client.post("/api/v1/worker/claim", headers=w).status_code == 401  # token revoked


def test_worker_runs_a_benchmark_job_end_to_end(env: Env, team: Team) -> None:
    from raceforge.api.service import TEMPLATES_DIR
    from raceforge.api.worker_runner import run_worker
    from raceforge.backend.models import JobCreate
    from raceforge.workspace.client import BackendClient

    member = BackendClient(
        "http://test",
        access=team.member["Authorization"][7:],
        factory=_direct(env),
    )
    reg = member.register_worker(team.ws, "lab-pc")
    job = member.create_job(
        team.ws,
        JobCreate(
            kind="benchmark",
            request={"race": {"tracks": 1, "length_m": 20, "max_time_s": 120}},
            controller_name="centering.py",
            controller_source=(TEMPLATES_DIR / "centering.py").read_text(encoding="utf-8"),
        ),
    )
    worker = BackendClient("http://test", access=reg.token, factory=_direct(env))
    lines: list[str] = []
    assert run_worker(worker, once=True, out=lines.append) == 1
    done = member.job(job.id)
    assert done.status == "done", (done.error, lines)
    assert done.result is not None and done.result["finished_rate"] == 1.0
    assert any("corridor" in line for line in done.log_tail)


def test_engine_queues_a_team_job_and_saves_tuned_params(
    env: Env, team: Team, tmp_path: Path
) -> None:
    from fastapi.testclient import TestClient

    from raceforge.api.service import TEMPLATES_DIR, Engine
    from raceforge.api.workspace import WorkspaceApi
    from raceforge.parts.catalogue import Catalogue
    from raceforge.server.app import create_app
    from raceforge.workspace.client import BackendClient
    from tests.backend.conftest import MEMBER_PW
    from tests.backend.test_sync import Net, factory

    cat = Catalogue.load()
    wsapi = WorkspaceApi(cat, tmp_path / "ws", factory(env.client.app, Net()))
    engine = TestClient(create_app(Engine(cat), frontend_dist=None, workspace=wsapi))
    w = "/api/v1/workspace"
    engine.post(
        f"{w}/login", json={"server_url": "http://b", "username": "anna", "password": MEMBER_PW}
    )
    race = {"tracks": 1, "length_m": 20, "max_time_s": 120}
    req = {
        "tune": {
            "controller": str(TEMPLATES_DIR / "centering.py"),
            "trials": 1,
            "train_tracks": 1,
            "race": race,
        }
    }
    job = engine.post(f"{w}/jobs", json=req)
    assert job.status_code == 200, job.text
    assert engine.get(f"{w}/jobs").json()[0]["status"] == "queued"

    reg = BackendClient(
        "http://t",
        access=team.member["Authorization"][7:],
        factory=_direct(env),
    )
    worker = BackendClient(
        "http://t",
        access=reg.register_worker(team.ws, "pc").token,
        factory=_direct(env),
    )
    from raceforge.api.worker_runner import run_worker

    assert run_worker(worker, once=True, out=lambda _: None) == 1
    done = engine.get(f"{w}/jobs").json()[0]
    assert done["status"] == "done", done["error"]
    assert [x["name"] for x in engine.get(f"{w}/workers").json()] == ["pc"]
    out = tmp_path / "centering.tuned.yaml"
    saved = engine.post(f"{w}/jobs/{done['id']}/save-params", json={"path": str(out)})
    assert saved.status_code == 200 and "speed_m_s" in out.read_text(encoding="utf-8")


def test_paused_benchmark_is_finished_by_another_worker_with_the_same_runs(
    env: Env, team: Team
) -> None:
    """Spec 0020 part C AC8: pause after the first race, resume elsewhere, identical result."""
    from dataclasses import asdict

    from raceforge.api.models import WorkerPolicy
    from raceforge.api.service import TEMPLATES_DIR
    from raceforge.api.worker_runner import run_worker
    from raceforge.backend.models import JobCreate
    from raceforge.train.benchmark import BenchConfig, benchmark
    from raceforge.workspace.client import BackendClient

    def client(token: str) -> BackendClient:
        return BackendClient("http://test", access=token, factory=_direct(env))

    member = client(team.member["Authorization"][7:])
    race = {"tracks": 2, "length_m": 20, "max_time_s": 120}
    job = member.create_job(
        team.ws,
        JobCreate(
            kind="benchmark",
            request={"race": race},
            controller_name="centering.py",
            controller_source=(TEMPLATES_DIR / "centering.py").read_text(encoding="utf-8"),
            raceforge_version="0.0.1",
        ),
    )
    calls = 0

    def laptop_policy() -> WorkerPolicy:  # free for the claim, then its owner comes back
        nonlocal calls
        calls += 1
        return WorkerPolicy(mode="always" if calls <= 2 else "paused", processes=1)

    laptop = client(member.register_worker(team.ws, "laptop").token)
    assert run_worker(laptop, once=True, policy=laptop_policy, out=lambda _: None) == 0
    paused = member.job(job.id)
    assert paused.status == "queued" and paused.attempt == 1
    assert paused.progress["done"] == 1 and "paused" in paused.log_tail[-1]

    pc = client(member.register_worker(team.ws, "pc").token)
    assert run_worker(pc, once=True, policy=WorkerPolicy(mode="always"), out=lambda _: None) == 1
    done = member.job(job.id)
    assert done.status == "done" and done.worker_name == "pc", done.error
    local = benchmark(
        TEMPLATES_DIR / "centering.py", None, BenchConfig(tracks=2, length_m=20, max_time_s=120)
    )
    assert done.result is not None
    # ``error`` may carry wall-clock deadline warnings; the races themselves are deterministic
    assert [{**r, "error": ""} for r in done.result["runs"]] == [
        {**asdict(r), "error": ""} for r in local.runs
    ]


def test_engine_switch_runs_this_computer_as_a_worker(env: Env, team: Team, tmp_path: Path) -> None:
    """Spec 0020 part C AC9: switch on → registered → runs a queued job → switch off."""
    import time

    from raceforge.api.local_worker import LocalWorker
    from raceforge.api.models import LocalWorkerUpdate, WorkerPolicy
    from raceforge.api.service import TEMPLATES_DIR
    from raceforge.backend.models import JobCreate
    from raceforge.workspace.client import BackendClient
    from raceforge.workspace.sync import Workspace
    from tests.backend.conftest import MEMBER_PW
    from tests.backend.test_sync import Net, factory

    fac = factory(env.client.app, Net())
    ws = Workspace(tmp_path / "ws", fac)
    ws.login("http://b", "anna", MEMBER_PW, None)
    ws.select(team.ws)
    local = LocalWorker(ws, fac)
    st = local.update(LocalWorkerUpdate(enabled=True, policy=WorkerPolicy(mode="always")))
    assert st.enabled and st.registered and st.available
    assert (tmp_path / "worker.json").stat().st_mode & 0o077 == 0  # token file owner-only

    member = BackendClient("http://b", access=team.member["Authorization"][7:], factory=fac)
    job = member.create_job(
        team.ws,
        JobCreate(
            kind="benchmark",
            request={"race": {"tracks": 1, "length_m": 20, "max_time_s": 120}},
            controller_name="centering.py",
            controller_source=(TEMPLATES_DIR / "centering.py").read_text(encoding="utf-8"),
        ),
    )
    deadline = time.monotonic() + 120
    while member.job(job.id).status != "done" and time.monotonic() < deadline:
        time.sleep(0.5)
    assert member.job(job.id).status == "done", local.status().log
    assert member.workers(team.ws)[0].id == st.worker_id

    off = local.update(LocalWorkerUpdate(enabled=False, policy=WorkerPolicy(mode="always")))
    local.stop(wait_s=30)
    assert not local.status().enabled and off.registered
