"""Spec 0009 AC1-AC4: the engine lists TrackScout passes and serves summary, trajectory and mesh."""

import base64
import hashlib
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from raceforge.api.service import Engine
from raceforge.api.workspace import WorkspaceApi
from raceforge.capture.inbox import OfferedPass
from raceforge.capture.tscan import TscanPass
from raceforge.parts.catalogue import Catalogue
from raceforge.server.app import create_app
from tests.backend.conftest import MEMBER_PW, Env, Team
from tests.backend.test_sync import Net, factory
from tests.capture.synth import make_pass


def _engine(tmp_path: Path, cat: Catalogue, wsapi: WorkspaceApi | None = None) -> TestClient:
    wsapi = wsapi or WorkspaceApi(cat, tmp_path / "ws")
    return TestClient(create_app(Engine(cat), frontend_dist=None, workspace=wsapi))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_open_file_detail_and_mesh(tmp_path: Path, cat: Catalogue) -> None:
    engine = _engine(tmp_path, cat)
    file = make_pass(tmp_path / "corridor.tscan")
    r = engine.post("/api/v1/scans/open", json={"path": str(file)})
    assert r.status_code == 200, r.text
    ref = r.json()
    assert ref["sha256"] == _sha(file) and ref["source"] == "file" and ref["pass_type"]
    assert engine.get("/api/v1/scans").json()[-1]["passes"][0]["name"] == "corridor.tscan"

    d = engine.get(f"/api/v1/scans/{ref['sha256']}").json()
    assert d["summary"]["frames"] == 20 and len(d["segments"]) == 2
    assert d["segments"][1]["discarded"] == [[2.6, 2.9]]
    # golden: frame 4 is at ARKit (0.4, 1.4, -0.2) → RaceForge (0.4, 0.2, 1.4)
    assert np.allclose(d["trajectory"][4], [0.4, 0.2, 1.4], atol=1e-6)
    assert d["trajectory_kept"].count(False) == 4  # 2.6-2.9 lie in the discarded range
    assert d["trajectory_segment"][:10] == [0] * 10 and d["trajectory_segment"][10:] == [1] * 10
    lo, hi = np.array(d["bounds"][0]), np.array(d["bounds"][1])
    assert np.all(lo <= np.array(d["trajectory"]).min(axis=0)) and np.all(hi >= [3, 2, 1.4])

    m = engine.get(f"/api/v1/scans/{ref['sha256']}/mesh").json()
    with TscanPass(file) as tp:
        mesh = tp.mesh(0)
    assert mesh is not None
    pos = np.frombuffer(base64.b64decode(m["positions_b64"]), "<f4").reshape(-1, 3)
    idx = np.frombuffer(base64.b64decode(m["indices_b64"]), "<u4").reshape(-1, 3)
    cls = np.frombuffer(base64.b64decode(m["classes_b64"]), "u1")
    assert np.allclose(pos, mesh.vertices) and np.array_equal(idx, mesh.faces)
    assert list(cls) == list(mesh.classification) and m["classes"][1] == "wall"
    sub = engine.get(f"/api/v1/scans/{ref['sha256']}/mesh", params={"max_faces": 2}).json()
    assert sub["faces"] == 2 and sub["total_faces"] == 3


def test_errors(tmp_path: Path, cat: Catalogue) -> None:
    engine = _engine(tmp_path, cat)
    assert engine.get(f"/api/v1/scans/{'0' * 64}").status_code == 404
    (tmp_path / "x.txt").write_text("hi")
    assert (
        engine.post("/api/v1/scans/open", json={"path": str(tmp_path / "x.txt")}).status_code == 422
    )
    bad = make_pass(tmp_path / "bad.tscan")
    data = bytearray(bad.read_bytes())
    data[len(data) // 2] ^= 0xFF
    bad.write_bytes(bytes(data))
    r = engine.post("/api/v1/scans/open", json={"path": str(bad)})
    assert r.status_code == 422


def test_laptop_inbox_passes_are_listed(tmp_path: Path, cat: Catalogue) -> None:
    wsapi = WorkspaceApi(cat, tmp_path / "ws")
    engine = _engine(tmp_path, cat, wsapi)
    file = make_pass(tmp_path / "p.tscan")
    sha = _sha(file)
    offer = OfferedPass(id="p1", size=file.stat().st_size, sha256=sha, project="Gang", tag="")
    offer.pass_type = "low"
    wsapi.inbox.begin(offer, "usb")
    wsapi.inbox.append("p1", file.read_bytes())
    wsapi.inbox.complete("p1")
    [track] = engine.get("/api/v1/scans").json()
    assert track["name"] == "Gang" and track["passes"][0]["source"] == "laptop"
    assert engine.get(f"/api/v1/scans/{sha}").json()["summary"]["frames"] == 20


def test_workspace_capture_passes(env: Env, team: Team, tmp_path: Path, cat: Catalogue) -> None:
    wsapi = WorkspaceApi(cat, tmp_path / "ws", factory(env.client.app, Net()))
    engine = _engine(tmp_path, cat, wsapi)
    login = {"server_url": "http://b", "username": "anna", "password": MEMBER_PW}
    assert engine.post("/api/v1/workspace/login", json=login).status_code == 200
    file = make_pass(tmp_path / "pass-1.tscan")
    wsapi.ws.save_files("capture", "scan-gang", [file], "pass", merge=True)
    [track] = engine.get("/api/v1/scans").json()
    assert track["slug"] == "scan-gang" and track["version"] == "1.0.0"
    ref = track["passes"][0]
    assert ref == {**ref, "name": "pass-1.tscan", "source": "workspace", "sha256": _sha(file)}
    # another laptop: the blob is downloaded on demand
    other = WorkspaceApi(cat, tmp_path / "other", factory(env.client.app, Net()))
    other.ws.login("http://b", "anna", MEMBER_PW, None)
    other.ws.select(team.ws)
    other.ws.sync()
    e2 = _engine(tmp_path, cat, other)
    assert e2.get(f"/api/v1/scans/{ref['sha256']}").json()["summary"]["segments"] == 2


def test_floorplan_and_width_endpoints(tmp_path: Path, cat: Catalogue) -> None:
    """Spec 0024: the synthetic pass has a floor and one wall triangle at y = 2 m."""
    engine = _engine(tmp_path, cat)
    file = make_pass(tmp_path / "corridor.tscan")
    sha = engine.post("/api/v1/scans/open", json={"path": str(file)}).json()["sha256"]
    r = engine.get(f"/api/v1/scans/{sha}/floorplan")
    assert r.status_code == 200, r.text
    fp = r.json()
    assert fp["width"] > 10 and fp["height"] > 10 and fp["resolution"] == 0.05
    assert base64.b64decode(fp["png_b64"])[:4] == b"\x89PNG" and abs(fp["floor_z"]) < 0.01
    assert len(fp["trajectory"]) > 0
    w = engine.post(f"/api/v1/scans/{sha}/corridor-width", json={"points": [[0, 1], [2, 1]]}).json()
    assert w["median_m"] is None and w["samples"] == 0  # one wall only: no closed cross-section
