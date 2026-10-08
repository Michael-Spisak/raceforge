"""Spec 0007 AC8: the laptop keeps phone passes and relays them when the backend is fast."""

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from raceforge.api.service import Engine
from raceforge.api.workspace import WorkspaceApi
from raceforge.capture.inbox import RETRY_S, Inbox, OfferedPass, Relay
from raceforge.capture.rftx import tag
from raceforge.capture.usb import OUTBOX, STATUS, FolderFiles
from raceforge.parts.catalogue import Catalogue
from raceforge.server.app import create_app
from raceforge.workspace.sync import Workspace
from tests.backend.conftest import MEMBER_PW, Env, Team
from tests.backend.test_sync import Net, factory
from tests.capture.synth import make_pass


class Ticker:
    """Fake monotonic clock: every call advances by ``step`` seconds (= time per uploaded part)."""

    def __init__(self, step: float) -> None:
        self.t = 1000.0
        self.step = step

    def __call__(self) -> float:
        self.t += self.step
        return self.t


def _workspace(env: Env, team: Team, root: Path, net: Net) -> Workspace:
    ws = Workspace(root, factory(env.client.app, net))
    ws.login("http://b", "anna", MEMBER_PW, None)
    ws.select(team.ws)
    return ws


def _receive(inbox: Inbox, file: Path, pass_id: str = "p1") -> str:
    data = file.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    offer = OfferedPass(id=pass_id, size=len(data), sha256=sha, project="Corridor", tag="")
    offer.pass_type = "low"
    inbox.begin(offer, "usb")
    inbox.append(pass_id, data)
    inbox.complete(pass_id)
    return sha


def test_fast_backend_uploads_automatically(env: Env, team: Team, tmp_path: Path) -> None:
    ws = _workspace(env, team, tmp_path / "ws", Net())
    inbox = Inbox(tmp_path / "inbox")
    sha = _receive(inbox, make_pass(tmp_path / "pass.tscan"))
    relay = Relay(ws, inbox, clock=Ticker(1e-6))
    item = relay.upload("p1")
    assert item.state == "uploaded" and item.version == "1.0.0"
    assert not inbox.path("p1").exists()  # the workspace's blob cache holds it now
    obj = next(o for o in ws.objects() if o.slug == "scan-corridor")
    assert obj.kind == "capture" and obj.latest is not None and not obj.latest.pending
    assert ws.version_content(obj.latest.id)["files"][0]["sha256"] == sha


def test_slow_backend_waits_and_the_three_choices_work(
    env: Env, team: Team, tmp_path: Path
) -> None:
    net = Net()
    ws = _workspace(env, team, tmp_path / "ws", net)
    inbox = Inbox(tmp_path / "inbox")
    a = make_pass(tmp_path / "a.tscan")
    _receive(inbox, a, "a")
    b = tmp_path / "b.tscan"
    b.write_bytes(a.read_bytes() + b"other pass")  # the relay does not parse passes
    _receive(inbox, b, "b")
    clock = Ticker(30.0)  # 1 KiB parts at 30 s each: far below 1 MB/s
    relay = Relay(ws, inbox, clock=clock)

    item = relay.upload("a")
    assert item.state == "waiting" and item.note == "slow"
    assert item.rate_bps is not None and item.rate_bps < 100
    assert ws.objects() == [] or all(o.slug != "scan-corridor" for o in ws.objects())

    # "keep only on this laptop": never uploaded by the background loop
    assert relay.choose("b", "keep_local").state == "local_only"
    clock.t += RETRY_S
    clock.step = 1e-6
    relay.tick()
    assert inbox.get("b").state == "local_only" and inbox.path("b").exists()

    # the background re-check found a faster connection for "a" and resumed its upload
    assert inbox.get("a").state == "uploaded"

    # "upload now" ignores the speed
    clock.step = 30.0
    assert relay.choose("b", "upload_now").state == "uploaded"
    files = [f["path"] for f in ws.version_content(ws.objects()[0].latest.id)["files"]]  # type: ignore[union-attr]
    assert sorted(files) == ["a.tscan", "b.tscan"]


def test_offline_backend_waits_then_uploads_in_background(
    env: Env, team: Team, tmp_path: Path
) -> None:
    net = Net()
    ws = _workspace(env, team, tmp_path / "ws", net)
    inbox = Inbox(tmp_path / "inbox")
    _receive(inbox, make_pass(tmp_path / "pass.tscan"))
    clock = Ticker(1e-6)
    relay = Relay(ws, inbox, clock=clock)
    net.online = False
    assert relay.upload("p1").note == "offline"
    relay.tick()  # too early for a retry
    assert inbox.get("p1").state == "waiting"
    net.online = True
    clock.t += RETRY_S
    relay.tick()
    assert inbox.get("p1").state == "uploaded"


def test_switching_back_resumes_without_resending_parts(
    env: Env, team: Team, tmp_path: Path
) -> None:
    net = Net()
    ws = _workspace(env, team, tmp_path / "ws", net)
    inbox = Inbox(tmp_path / "inbox")
    file = make_pass(tmp_path / "pass.tscan")
    _receive(inbox, file)
    parts = -(-file.stat().st_size // 1024)
    relay = Relay(ws, inbox, clock=Ticker(30.0))
    relay.upload("p1")  # stops after the first part (too slow)
    before = net.requests
    assert relay.choose("p1", "upload_now").state == "uploaded"
    # every remaining part once, plus a handful of control requests — never the first part again
    assert net.requests - before <= (parts - 1) + 10


def test_engine_receive_by_cable_and_team_tab_endpoints(
    env: Env, team: Team, tmp_path: Path, cat: Catalogue
) -> None:
    phone = tmp_path / "phone"
    wsapi = WorkspaceApi(cat, tmp_path / "ws", factory(env.client.app, Net()))

    async def open_usb() -> FolderFiles:
        return FolderFiles(phone)

    wsapi.open_usb = open_usb
    wsapi.relay.min_rate_bps = 0  # the in-process test backend has no realistic timing
    wsapi.relay.max_eta_s = float("inf")
    engine = TestClient(create_app(Engine(cat), frontend_dist=None, workspace=wsapi))
    r = engine.post(
        "/api/v1/workspace/login",
        json={"server_url": "http://b", "username": "anna", "password": MEMBER_PW},
    )
    assert r.status_code == 200
    key = wsapi.ws.laptop_key()
    (phone / "Projects" / "x").mkdir(parents=True)
    data = make_pass(phone / "Projects" / "x" / "p1.tscan").read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    entry = {
        "id": "p1",
        "file": "Projects/x/p1.tscan",
        "size": len(data),
        "sha256": sha,
        "project": "Corridor",
        "pass_type": "walkthrough",
        "tag": tag(key, "p1", sha),
    }
    (phone / OUTBOX).parent.mkdir(parents=True)
    (phone / OUTBOX).write_text(json.dumps({"v": 1, "passes": [entry]}))

    r = engine.post("/api/v1/workspace/trackscout/receive", json={"source": "usb"})
    assert r.status_code == 200, r.text
    [item] = r.json()
    assert item["id"] == "p1" and item["state"] == "uploaded" and item["version"] == "1.0.0"
    assert json.loads((phone / STATUS).read_text())["passes"]["p1"]["done"] is True
    assert engine.get("/api/v1/workspace/trackscout/inbox").json()[0]["state"] == "uploaded"
    r = engine.post("/api/v1/workspace/trackscout/inbox/nope", json={"action": "keep_local"})
    assert r.status_code == 404


def test_engine_receive_reports_device_errors(tmp_path: Path, cat: Catalogue) -> None:
    wsapi = WorkspaceApi(cat, tmp_path / "ws")

    async def no_phone() -> FolderFiles:
        raise RuntimeError("TrackScout is not installed on the connected device")

    wsapi.open_usb = no_phone
    engine = TestClient(create_app(Engine(cat), frontend_dist=None, workspace=wsapi))
    r = engine.post("/api/v1/workspace/trackscout/receive", json={"source": "usb"})
    assert r.status_code == 502 and "not installed" in r.json()["detail"]


@pytest.mark.parametrize("pass_id", ["../evil", "a/b", ""])
def test_inbox_rejects_unsafe_pass_ids(tmp_path: Path, pass_id: str) -> None:
    inbox = Inbox(tmp_path)
    with pytest.raises(ValueError):
        inbox.begin(OfferedPass(id=pass_id, size=1, sha256="0", project="x", tag=""), "usb")
