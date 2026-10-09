"""Spec 0027: team live relay (AC1) and the engine's share throttle + run log (AC2, unit level)."""

import asyncio
import gzip
import json
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.backend.conftest import MEMBER_PW, Env, Team


def _frame(seq: int) -> str:
    return json.dumps({"type": "telemetry", "frame": {"seq": seq, "state": "run"}})


def test_publisher_frames_reach_watchers_and_end(env: Env, team: Team) -> None:
    c = env.client
    with c.websocket_connect(
        f"/api/v1/workspaces/{team.ws}/live/publish", headers=team.member
    ) as pub:
        pub.send_json({"type": "start", "car": "car-1"})
        sid = pub.receive_json()["id"]
        pub.send_text(json.dumps({"type": "hello", "car": "car-1", "mode": "test"}))
        pub.send_text(_frame(1))
        sessions = c.get(f"/api/v1/workspaces/{team.ws}/live", headers=team.member).json()
        assert [s["car"] for s in sessions] == ["car-1"] and sessions[0]["publisher"] == "anna"

        with c.websocket_connect(f"/api/v1/live/{sid}/watch", headers=team.admin) as late:
            assert late.receive_json()["type"] == "session"
            assert late.receive_json()["type"] == "hello"  # a late watcher catches up …
            assert late.receive_json()["frame"]["seq"] == 1  # … with the last frame
            with c.websocket_connect(f"/api/v1/live/{sid}/watch", headers=team.member) as w2:
                w2.receive_json(), w2.receive_json(), w2.receive_json()
                for seq in (2, 3):
                    pub.send_text(_frame(seq))
                assert [late.receive_json()["frame"]["seq"] for _ in range(2)] == [2, 3]
                assert [w2.receive_json()["frame"]["seq"] for _ in range(2)] == [2, 3]
                pub.close()
                assert late.receive_json() == {"type": "end"}
    assert c.get(f"/api/v1/workspaces/{team.ws}/live", headers=team.member).json() == []


def test_relay_permissions(env: Env, team: Team) -> None:
    c = env.client
    token = c.post(
        "/api/v1/tokens", headers=team.member, json={"name": "viewer", "scopes": ["read"]}
    ).json()["token"]
    read_only = {"Authorization": f"Bearer {token}"}
    with (
        pytest.raises(WebSocketDisconnect) as e,
        c.websocket_connect(f"/api/v1/workspaces/{team.ws}/live/publish", headers=read_only),
    ):
        pass
    assert e.value.code == 4403
    worker = c.post(
        "/api/v1/workers", headers=team.member, json={"workspace_id": team.ws, "name": "pc"}
    ).json()["token"]
    with c.websocket_connect(
        f"/api/v1/workspaces/{team.ws}/live/publish", headers=team.member
    ) as pub:
        pub.send_json({"type": "start", "car": "car-1"})
        sid = pub.receive_json()["id"]
        with (
            pytest.raises(WebSocketDisconnect) as e,
            c.websocket_connect(
                f"/api/v1/live/{sid}/watch", headers={"Authorization": f"Bearer {worker}"}
            ),
        ):
            pass
        assert e.value.code == 4403
    with (
        pytest.raises(WebSocketDisconnect) as e,
        c.websocket_connect("/api/v1/live/nope/watch", headers=team.member),
    ):
        pass
    assert e.value.code == 4404


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        return json.dumps({"type": "session", "id": "s1"})

    async def close(self) -> None:
        pass


def test_share_throttles_frames_and_recorder_saves_a_run(
    env: Env, team: Team, tmp_path: Path
) -> None:
    from raceforge.api.live_share import LiveShare, RunRecorder
    from raceforge.backend.models import FileSetContent
    from raceforge.workspace.sync import Workspace
    from tests.backend.test_sync import Net, factory

    ws = Workspace(tmp_path / "ws", factory(env.client.app, Net()))
    ws.login("http://b", "anna", MEMBER_PW, None)
    ws.select(team.ws)
    sock = FakeSocket()

    async def connect(url: str, headers: dict[str, str]) -> FakeSocket:
        assert url.endswith(f"/workspaces/{team.ws}/live/publish")
        assert headers["Authorization"].startswith("Bearer ")
        return sock

    share = LiveShare(ws, rate_hz=10, connect=connect)
    recorder = RunRecorder(tmp_path)

    async def drive() -> None:
        assert await share.open("car-1") == "sharing"
        for seq in range(50):  # one second of a 50 Hz car
            text = _frame(seq)
            recorder.add(json.loads(text))
            await share.send(text, "telemetry")
            await asyncio.sleep(0.02)
        await share.send(json.dumps({"type": "event", "kind": "fault"}), "event")

    asyncio.run(drive())
    frames = [m for m in sock.sent if '"telemetry"' in m]
    assert 6 <= len(frames) <= 14  # ≈ 10 Hz
    assert '"event"' in sock.sent[-1]  # events are never throttled
    recorder.note("scrapes the wall at door 3")
    slug = recorder.save(ws)
    assert slug is not None and slug.startswith("live-car-")
    run = next(o for o in ws.objects("run") if o.slug == slug)
    assert run.latest is not None
    files = FileSetContent.model_validate(ws.version_content(run.latest.id)).files
    blob = next(f for f in files if f.path == "telemetry.jsonl.gz")
    lines = gzip.decompress(ws.blob_path(blob.sha256).read_bytes()).decode().splitlines()
    assert len(lines) == 50  # the run log keeps the full rate
