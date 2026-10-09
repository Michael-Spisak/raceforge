"""Spec 0020 part C: claim order, targeting, version matching, leases and pause/requeue."""

from typing import Any

from tests.backend.conftest import Env, Team

SOURCE = "from raceforge.control.controller import Controller\n"


def _worker(env: Env, team: Team, name: str, version: str = "0.0.1") -> dict[str, str]:
    reg = env.client.post(
        "/api/v1/workers", headers=team.member, json={"workspace_id": team.ws, "name": name}
    ).json()
    h = {"Authorization": f"Bearer {reg['token']}", "X-Worker-Id": reg["worker"]["id"]}
    _beat(env, h, version)
    return h


def _beat(env: Env, w: dict[str, str], version: str = "0.0.1") -> None:
    r = env.client.post(
        "/api/v1/worker/heartbeat", headers=w, json={"info": {"raceforge": version}}
    )
    assert r.status_code == 200, r.text


def _job(env: Env, team: Team, headers: dict[str, str] | None = None, **extra: Any) -> str:
    body = {
        "kind": "benchmark",
        "request": {"race": {"tracks": 3}},
        "controller_name": "mine.py",
        "controller_source": SOURCE,
        **extra,
    }
    r = env.client.post(
        f"/api/v1/workspaces/{team.ws}/jobs", headers=headers or team.member, json=body
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _claim(env: Env, w: dict[str, str]) -> dict[str, Any] | None:
    r = env.client.post("/api/v1/worker/claim", headers=w)
    assert r.status_code in (200, 204), r.text
    return r.json() if r.status_code == 200 else None


def _finish(env: Env, w: dict[str, str], job_id: str, status: str = "done", **kw: Any) -> None:
    r = env.client.post(
        f"/api/v1/worker/jobs/{job_id}/finish", headers=w, json={"status": status, **kw}
    )
    assert r.status_code == 200, r.text


def _get(env: Env, team: Team, job_id: str) -> dict[str, Any]:
    return env.client.get(f"/api/v1/jobs/{job_id}", headers=team.member).json()


def test_priority_target_and_version_decide_who_gets_which_job(env: Env, team: Team) -> None:
    w1 = _worker(env, team, "pc-1")
    w2 = _worker(env, team, "pc-2")
    targeted = _job(env, team, target_worker_id=w2["X-Worker-Id"])
    other_version = _job(env, team, raceforge_version="9.9.9")
    normal = _job(env, team, raceforge_version="0.0.1")
    high = _job(env, team, priority="high")
    r = env.client.post(
        f"/api/v1/workspaces/{team.ws}/jobs",
        headers=team.member,
        json={
            "kind": "benchmark",
            "request": {},
            "controller_name": "a.py",
            "controller_source": SOURCE,
            "priority": "critical",
        },
    )
    assert r.status_code == 403  # race-critical is admin only
    critical = _job(env, team, headers=team.admin, priority="critical")
    r = env.client.post(
        f"/api/v1/workspaces/{team.ws}/jobs",
        headers=team.member,
        json={
            "kind": "benchmark",
            "request": {},
            "controller_name": "a.py",
            "controller_source": SOURCE,
            "target_worker_id": "nope",
        },
    )
    assert r.status_code == 422

    order: list[str] = []
    for _ in range(3):
        got = _claim(env, w1)
        assert got is not None
        order.append(got["id"])
        _finish(env, w1, got["id"])
    assert order == [critical, high, normal]
    assert _claim(env, w1) is None  # neither the job for pc-2 nor the one for another version
    got = _claim(env, w2)
    assert got is not None and got["id"] == targeted
    assert _get(env, team, targeted)["target_worker_id"] == w2["X-Worker-Id"]
    assert _get(env, team, other_version)["status"] == "queued"


def test_lost_worker_requeues_with_partial_result_then_errors(env: Env, team: Team) -> None:
    w1 = _worker(env, team, "pc-1")
    w2 = _worker(env, team, "pc-2")
    jid = _job(env, team)
    assert (_claim(env, w1) or {})["id"] == jid
    partial = {"runs": [{"seed": 1000, "finished": True}]}
    r = env.client.post(
        f"/api/v1/worker/jobs/{jid}/progress",
        headers=w1,
        json={"progress": {"done": 1, "total": 3}, "partial": partial},
    )
    assert r.json() == {"cancel": False}

    env.clock.advance(minutes=3)  # pc-1 vanished (no heartbeat within the lease)
    job = _get(env, team, jid)
    assert job["status"] == "queued" and job["attempt"] == 2 and job["worker_id"] is None
    assert "lost" in job["log_tail"][-1]
    _beat(env, w1)
    assert _claim(env, w1) is None  # the lost worker is skipped for a while …
    _beat(env, w2)
    got = _claim(env, w2)  # … so another worker continues from the partial result
    assert got is not None and got["resume"] == partial and got["attempt"] == 2
    # pc-1 came back: its old job is gone, it cannot report into it
    r = env.client.post(f"/api/v1/worker/jobs/{jid}/progress", headers=w1, json={})
    assert r.status_code == 404

    env.clock.advance(minutes=3)  # pc-2 lost too → attempt 3
    _beat(env, w1)
    got = _claim(env, w1)  # pc-1 was lost long enough ago: it may take it again
    assert got is not None and got["attempt"] == 3
    env.clock.advance(minutes=3)  # third loss ends the job
    job = _get(env, team, jid)
    assert job["status"] == "error" and "lost 3 times" in job["error"]


def test_paused_job_goes_back_to_the_queue_without_counting_an_attempt(
    env: Env, team: Team
) -> None:
    w = _worker(env, team, "laptop")
    jid = _job(env, team)
    assert _claim(env, w) is not None
    partial = {"runs": [{"seed": 1000}]}
    _finish(env, w, jid, "paused", result=partial)
    job = _get(env, team, jid)
    assert job["status"] == "queued" and job["attempt"] == 1 and job["result"] is None
    got = _claim(env, w)  # pausing is not a loss: the same worker may continue later
    assert got is not None and got["resume"] == partial


def test_a_cancelled_job_is_not_requeued(env: Env, team: Team) -> None:
    w = _worker(env, team, "laptop")
    jid = _job(env, team)
    assert _claim(env, w) is not None
    env.client.post(f"/api/v1/jobs/{jid}/cancel", headers=team.member)
    _finish(env, w, jid, "paused")
    assert _get(env, team, jid)["status"] == "cancelled"
    assert _claim(env, w) is None
