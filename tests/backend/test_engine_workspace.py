"""Spec 0006 AC7 + AC10 (API part): the engine's workspace endpoints, and the engine keeps working
while the backend is unreachable."""

from pathlib import Path

from fastapi.testclient import TestClient

from raceforge.api.service import Engine
from raceforge.api.workspace import WorkspaceApi, invite_token
from raceforge.parts.catalogue import Catalogue
from raceforge.server.app import create_app
from tests.backend.conftest import MEMBER_PW, Env, Team
from tests.backend.test_sync import Net, factory


def test_engine_workspace_flow(env: Env, team: Team, tmp_path: Path, cat: Catalogue) -> None:
    net = Net()
    wsapi = WorkspaceApi(cat, tmp_path / "ws", factory(env.client.app, net))
    engine = TestClient(create_app(Engine(cat), frontend_dist=None, workspace=wsapi))
    w = "/api/v1/workspace"
    assert engine.get(f"{w}/status").json()["logged_in"] is False
    bad = engine.post(
        f"{w}/login",
        json={"server_url": "http://b", "username": "anna", "password": "nope-nope-nope"},
    )
    assert bad.status_code == 401
    st = engine.post(
        f"{w}/login", json={"server_url": "http://b", "username": "anna", "password": MEMBER_PW}
    ).json()
    assert st["online"] and st["workspace"]["id"] == team.ws  # only one workspace: auto-selected
    saved = engine.post(
        f"{w}/save/quickstart",
        json={"slug": "car-a", "message": "first", "params": {"wheelbase_studs": 13}},
    ).json()
    assert saved["semver"] == "1.0.0" and not saved["pending"]
    [obj] = engine.get(f"{w}/objects", params={"kind": "assembly"}).json()
    hist = engine.get(f"{w}/objects/{obj['id']}/versions").json()
    assert [v["message"] for v in hist] == ["first"]
    assert engine.get(f"{w}/versions/{saved['id']}").json()["schema"] == "assembly"
    # members cannot manage invites; the backend's answer is passed through
    assert engine.get(f"{w}/invites").status_code == 403
    tok = engine.post(f"{w}/tokens", json={"name": "cli", "scopes": ["read"]}).json()
    assert tok["token"].startswith("rft_")
    assert engine.delete(f"{w}/tokens/{tok['id']}").status_code == 204
    # offline: everything local keeps working, saves are queued
    net.online = False
    assert engine.get(f"{w}/status", params={"probe": True}).json()["online"] is False
    assert engine.post("/api/v1/quickstart", json={}).status_code == 200
    queued = engine.post(f"{w}/save/quickstart", json={"slug": "car-a", "params": {}}).json()
    assert queued["pending"] and queued["semver"] is None
    assert len(engine.get(f"{w}/objects/{obj['id']}/versions").json()) == 2
    assert engine.post(f"{w}/sync").status_code == 503
    assert engine.get(f"{w}/workspaces").json()[0]["id"] == team.ws  # cached list
    net.online = True
    assert engine.post(f"{w}/sync").json()["pushed"] == 1
    assert engine.get(f"{w}/status").json()["pending"] == 0


def test_register_through_engine(env: Env, team: Team, tmp_path: Path, cat: Catalogue) -> None:
    inv = env.client.post("/api/v1/invites", headers=team.admin, json={}).json()
    wsapi = WorkspaceApi(cat, tmp_path / "ws", factory(env.client.app, Net()))
    engine = TestClient(create_app(Engine(cat), frontend_dist=None, workspace=wsapi))
    r = engine.post(
        "/api/v1/workspace/register",
        json={
            "server_url": "http://b",
            "invite": inv["link"],
            "username": "carl",
            "display_name": "Carl",
            "password": "carl-password-1",
        },
    )
    assert r.status_code == 200 and r.json()["username"] == "carl"


def test_invite_token_parsing() -> None:
    assert invite_token("https://rf.example.org/invite#rfi_abc-DEF_1") == "rfi_abc-DEF_1"
    assert invite_token("  rfi_x  ") == "rfi_x"
