"""Spec 0010 AC4: the engine relays a car runtime's WebSocket (spec 0005) to the UI."""

import asyncio
import json
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from websockets.asyncio.server import ServerConnection, serve

from raceforge.api.car_link import car_url
from raceforge.api.service import Engine
from raceforge.server.app import create_app

TOKEN = "t0ken-0123456789abcdef"


class FakeCar:
    """Speaks the car runtime's protocol (hello, telemetry, acks) and records what it receives."""

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.paths: list[str] = []
        self.loop = asyncio.new_event_loop()
        self.port = 0
        ready = threading.Event()

        async def handler(conn: ServerConnection) -> None:
            self.paths.append(conn.request.path if conn.request else "")
            await conn.send(
                json.dumps({"type": "hello", "car": "car-1", "mode": "test", "rate_hz": 20})
            )
            await conn.send(json.dumps({"type": "telemetry", "frame": {"state": "run", "seq": 1}}))
            async for raw in conn:
                msg = json.loads(raw)
                self.received.append(msg)
                if msg["type"] == "stop":
                    await conn.send(
                        json.dumps({"type": "ack", "cmd": "stop", "ok": True, "detail": ""})
                    )
                if msg["type"] == "radio_check":
                    reply = {"type": "radio_check", "ok": False, "violations": ["wlan0 is up"]}
                    await conn.send(json.dumps(reply))

        async def main() -> None:
            async with serve(handler, "127.0.0.1", 0) as server:
                self.port = server.sockets[0].getsockname()[1]
                ready.set()
                await asyncio.Future()

        self.thread = threading.Thread(
            target=self.loop.run_until_complete, args=(main(),), daemon=True
        )
        self.thread.start()
        ready.wait(5)

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"


@pytest.fixture(scope="module")
def car() -> Iterator[FakeCar]:
    yield FakeCar()


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app(Engine(), frontend_dist=None))


def _until(ws: Any, kind: str, state: str | None = None) -> dict[str, Any]:
    for _ in range(50):
        msg = ws.receive_json()
        if msg["type"] == kind and (state is None or msg.get("state") == state):
            return msg
    raise AssertionError(f"no {kind} {state}")


def test_relay_both_ways_with_rtt(client: TestClient, car: FakeCar) -> None:
    with client.websocket_connect("/api/v1/car/live") as ws:
        ws.send_json({"type": "connect", "url": car.url, "token": TOKEN})
        _until(ws, "link", "connected")
        assert _until(ws, "hello")["car"] == "car-1"
        assert _until(ws, "telemetry")["frame"]["state"] == "run"
        ws.send_json({"type": "teleop", "steer": 0.1, "speed": 0.5})
        ws.send_json(
            {"type": "mode", "mode": "race"}
        )  # not forwarded: the mode comes from the bundle
        ws.send_json({"type": "stop"})
        assert _until(ws, "ack")["cmd"] == "stop"
        rtt = _until(ws, "link", "connected")
        assert "rtt_ms" in rtt and rtt["rtt_ms"] < 1000
    assert [m["type"] for m in car.received[-2:]] == ["teleop", "stop"]
    assert all(m["type"] != "mode" for m in car.received)
    assert car.paths[-1] == f"/?token={TOKEN}"


def test_unreachable_car_and_bad_address(client: TestClient) -> None:
    with client.websocket_connect("/api/v1/car/live") as ws:
        ws.send_json({"type": "connect", "url": "ws://127.0.0.1:1"})
        msg = _until(ws, "link", "error")
        assert "not reachable" in msg["detail"]
    with client.websocket_connect("/api/v1/car/live") as ws:
        ws.send_json({"type": "connect", "url": "http://x"})
        assert _until(ws, "link", "error")["state"] == "error"


def test_car_url() -> None:
    assert car_url("raceforge-car.local:8765", None) == "ws://raceforge-car.local:8765/"
    assert car_url("ws://10.0.0.7:8765", "abc") == "ws://10.0.0.7:8765/?token=abc"
    with pytest.raises(ValueError):
        car_url("ftp://x", None)


def test_pairing_code_for_trackscout(client: TestClient) -> None:
    """Spec 0010 AC7: the same golden code is parsed by TrackScoutKit (DriveTests)."""
    r = client.post(
        "/api/v1/car/pairing-code",
        json={"url": "ws://10.0.0.7:8765", "token": "tok-1234567890abcdef"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == (
        "raceforge://car?v=1&d=eyJ1cmwiOiAid3M6Ly8xMC4wLjAuNzo4NzY1IiwgInRva2VuIjogInRvay0xMj"
        "M0NTY3ODkwYWJjZGVmIn0"
    )
    assert body["qr_svg"].startswith("<svg")
    assert client.post("/api/v1/car/pairing-code", json={"url": "ftp://x"}).status_code == 422


def test_radio_pre_check_is_forwarded(client: TestClient, car: FakeCar) -> None:
    """Spec 0030 AC3."""
    with client.websocket_connect("/api/v1/car/live") as ws:
        ws.send_json({"type": "connect", "url": car.url, "token": TOKEN, "share": False})
        _until(ws, "link", "connected")
        ws.send_json({"type": "radio_check"})
        reply = _until(ws, "radio_check")
        assert reply["ok"] is False and reply["violations"] == ["wlan0 is up"]
    assert any(m["type"] == "radio_check" for m in car.received)
