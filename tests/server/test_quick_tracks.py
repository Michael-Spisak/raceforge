"""Spec 0014: drawn quick tracks — preview, save/list, and a sim race on one."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from raceforge.api.service import TEMPLATES_DIR, Engine
from raceforge.server.app import create_app
from raceforge.track.quick import QuickTrack, build_quick_track

RECT = {"points": [[0, 0], [10, 0], [10, 5], [0, 5]], "width_m": 1.6, "laps": 1}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("RACEFORGE_WORKSPACE_DIR", str(tmp_path / "data" / "workspace"))
    return TestClient(create_app(Engine(), frontend_dist=None))


def test_preview_valid_and_folded_tracks(client: TestClient) -> None:
    ok = client.post("/api/v1/tracks/quick/preview", json=RECT).json()
    assert ok["ok"] and 25 < ok["length_m"] < 30 and len(ok["walls"]) == 2 and ok["start_line"]
    folded = {"points": [[0, 0], [10, 0], [0, 0.5]], "width_m": 1.6}
    bad = client.post("/api/v1/tracks/quick/preview", json=folded).json()
    assert not bad["ok"] and "overlaps" in bad["error"]


def test_save_list_and_race(client: TestClient) -> None:
    assert client.put("/api/v1/tracks/quick/Gang EG", json=RECT).json()["ok"]
    names = [t["name"] for t in client.get("/api/v1/tracks/quick").json()]
    assert names == ["Gang EG"]
    assert client.get("/api/v1/tracks/quick/Gang EG").json()["width_m"] == 1.6
    start = {
        "controller": str(TEMPLATES_DIR / "centering.py"),
        "quick_track": "Gang EG",
        "speed": 1000,
    }
    with client.websocket_connect("/api/v1/sim") as ws:
        ws.send_json(start)
        assert ws.receive_json()["type"] == "scene"
        msg = ws.receive_json()
        while msg["type"] != "result":
            msg = ws.receive_json()
    assert msg["finished"], msg
    assert client.delete("/api/v1/tracks/quick/Gang EG").status_code == 200
    assert client.get("/api/v1/tracks/quick").json() == []


def test_laps_and_obstacles() -> None:
    q = QuickTrack.model_validate(
        {**RECT, "laps": 2, "obstacles": [{"kind": "bin", "x": 5, "y": 0.4}]}
    )
    c = build_quick_track(q)
    assert c.track.race_setups[0].laps == 2 and c.track.objects[0].class_id == "bin"


def test_edited_track_roundtrip_and_race(client: TestClient) -> None:
    """Spec 0020: edited setup is saved, validated and raced on."""
    edit = {
        "race_setup": {"grid_cars": 2},
        "checks": [{"a": [0, 0], "b": [10, 0], "measured_m": 10}],
    }
    body = {**RECT, "edit": edit}
    pv = client.put("/api/v1/tracks/quick/edited", json=body).json()
    assert pv["ok"] and len(pv["start_grid"]) == 2 and pv["checks"][0]["error_m"] == 0
    assert client.get("/api/v1/tracks/quick/edited").json()["edit"]["race_setup"]["grid_cars"] == 2
    val = client.post("/api/v1/tracks/quick/validate", json=body).json()
    assert val["ok"]
    start = {
        "controller": str(TEMPLATES_DIR / "centering.py"),
        "quick_track": "edited",
        "speed": 1000,
    }
    with client.websocket_connect("/api/v1/sim") as ws:
        ws.send_json(start)
        msg = ws.receive_json()
        while msg["type"] != "result":
            msg = ws.receive_json()
    assert msg["finished"], msg


def test_sim_start_with_battery(client: TestClient) -> None:
    """Spec 0021: the battery option reaches the sim and the car still finishes."""
    start = {"controller": str(TEMPLATES_DIR / "centering.py"), "battery": True, "speed": 1000}
    client.put("/api/v1/tracks/quick/b", json=RECT)
    start["quick_track"] = "b"
    with client.websocket_connect("/api/v1/sim") as ws:
        ws.send_json(start)
        msg = ws.receive_json()
        while msg["type"] != "result":
            msg = ws.receive_json()
    assert msg["finished"], msg
