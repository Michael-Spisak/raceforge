"""Spec 0007 AC5/AC6: pairing TrackScout via the engine; capture passes in the team workspace."""

import base64
import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from raceforge.api.service import Engine
from raceforge.api.workspace import WorkspaceApi
from raceforge.capture.tscan import TscanPass
from raceforge.parts.catalogue import Catalogue
from raceforge.server.app import create_app
from tests.backend.conftest import MEMBER_PW, Env, Team
from tests.backend.test_sync import Net, factory
from tests.capture.synth import make_pass


def _engine(env: Env, tmp_path: Path, cat: Catalogue) -> tuple[TestClient, WorkspaceApi]:
    wsapi = WorkspaceApi(cat, tmp_path / "ws", factory(env.client.app, Net()))
    engine = TestClient(create_app(Engine(cat), frontend_dist=None, workspace=wsapi))
    r = engine.post(
        "/api/v1/workspace/login",
        json={"server_url": "http://b", "username": "anna", "password": MEMBER_PW},
    )
    assert r.status_code == 200
    return engine, wsapi


def test_pair_trackscout(env: Env, team: Team, tmp_path: Path, cat: Catalogue) -> None:
    engine, wsapi = _engine(env, tmp_path, cat)
    r = engine.post("/api/v1/workspace/pair-trackscout").json()
    assert r["qr_svg"].startswith("<svg") and r["url"].startswith("raceforge://pair?v=1&d=")
    data = r["url"].split("d=", 1)[1]
    payload = json.loads(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))
    assert payload["server"] == "http://b" and payload["workspace_id"] == team.ws
    assert payload["laptop_key"] == wsapi.ws.laptop_key()  # stable per laptop
    tokens = env.client.get("/api/v1/tokens", headers=team.member).json()
    [tok] = [t for t in tokens if t["id"] == r["token_id"]]
    assert tok["client"] == "trackscout" and sorted(tok["scopes"]) == ["edit", "read"]
    # the phone's token works for uploads but not for admin things
    h = {"Authorization": f"Bearer {payload['token']}"}
    assert env.client.get("/api/v1/workspaces", headers=h).status_code == 200
    assert env.client.get("/api/v1/audit", headers=h).status_code == 403


def test_capture_versions_list_all_passes(
    env: Env, team: Team, tmp_path: Path, cat: Catalogue
) -> None:
    _engine_client, wsapi = _engine(env, tmp_path, cat)
    p1 = make_pass(tmp_path / "pass-1.tscan")
    p2 = tmp_path / "pass-2.tscan"
    shutil.copy(p1, p2)  # same bytes: deduplicated blob, separate pass entry
    v1 = wsapi.ws.save_files("capture", "scan-corridor-test", [p1], "first pass", merge=True)
    v2 = wsapi.ws.save_files("capture", "scan-corridor-test", [p2], "second pass", merge=True)
    assert v1.semver == "1.0.0" and v2.semver == "1.0.1"
    content = wsapi.ws.version_content(v2.id)
    assert [f["path"] for f in content["files"]] == ["pass-1.tscan", "pass-2.tscan"]
    assert content["files"][0]["sha256"] == content["files"][1]["sha256"]
    # another laptop downloads the pass on demand and opens it
    other = WorkspaceApi(cat, tmp_path / "other", factory(env.client.app, Net()))
    other.ws.login("http://b", "anna", MEMBER_PW, None)
    other.ws.select(team.ws)
    other.ws.sync()
    blob = other.ws.blob(content["files"][1]["sha256"])
    with TscanPass(blob) as p:
        assert p.summary()["frames"] == 20
