"""Spec 0008 AC1-AC3: engine API endpoints, OpenAPI contract snapshot, sim WebSocket."""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from raceforge.construct.derive import derive
from raceforge.construct.quickstart import QuickStartParams, generate, vehicle_spec
from raceforge.control.controller import load_controller
from raceforge.parts.catalogue import Catalogue
from raceforge.server.app import create_app
from raceforge.sim.engine import Simulation
from raceforge.sim.runner import SIM_SENSORS
from raceforge.sim.simio import run_race
from raceforge.sim.world import CarEntry, build_world
from raceforge.track.procedural import CorridorParams, generate_corridor

ROOT = Path(__file__).parents[2]
SNAPSHOT = ROOT / "tests" / "snapshots" / "openapi.json"
CENTERING = str(ROOT / "controllers" / "templates" / "centering.py")


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app(frontend_dist=None))


def test_health_and_parts(client: TestClient) -> None:
    assert client.get("/api/v1/health").json()["version"]
    parts = client.get("/api/v1/parts", params={"query": "beam"}).json()
    assert parts and all("beam" in p["name"].lower() for p in parts)
    sensors = client.get("/api/v1/parts", params={"category": "sensor"}).json()
    assert {p["key"] for p in sensors} >= {"95652", "ld06"}


def test_quickstart_matches_cli_derivation(client: TestClient, cat: Catalogue) -> None:
    body = {"wheelbase_studs": 13, "layout": "awd"}
    r = client.post("/api/v1/quickstart", json=body)
    assert r.status_code == 200
    data = r.json()
    res = generate(QuickStartParams.model_validate(body), cat)
    expected = derive(res.assembly, cat, vehicle_spec(res, cat))
    assert data["derived"]["mass_kg"] == pytest.approx(expected.mass_kg)
    assert data["derived"]["turning_radius_m"] == pytest.approx(expected.turning_radius_m)
    assert sum(len(b["parts"]) for b in data["car"]["bodies"]) > 40


def test_quickstart_validation_errors_carry_hints(client: TestClient) -> None:
    r = client.post("/api/v1/quickstart", json={"wheelbase_studs": 30})
    assert r.status_code == 422
    assert "nearest valid: [20, 21]" in r.text


def test_schema_exports_and_corridor(client: TestClient) -> None:
    schema = client.get("/api/v1/quickstart/schema").json()
    assert schema["options"]["wheelbase_studs"][0] == 11 and "layout" in schema["defaults"]
    mpd = client.post("/api/v1/quickstart/export/mpd", json={})
    assert mpd.status_code == 200 and mpd.text.startswith("0 FILE car.ldr")
    assert client.post("/api/v1/quickstart/export/nope", json={}).status_code == 404
    cor = client.post("/api/v1/corridor", json={"seed": 3, "length_m": 30}).json()
    assert cor["primitives"] and len(cor["centreline"]) > 20
    names = {c["name"] for c in client.get("/api/v1/controllers").json()}
    assert {"centering", "wall_follow", "state_machine"} <= names


def test_openapi_snapshot(client: TestClient) -> None:
    """Contract guard: regenerate with UPDATE_SNAPSHOTS=1 only after an approved spec change."""
    current = json.dumps(client.get("/openapi.json").json(), indent=2, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        SNAPSHOT.write_text(current, encoding="utf-8")
    assert SNAPSHOT.read_text(encoding="utf-8") == current, "engine API contract changed"


def test_sim_websocket_matches_headless_run(client: TestClient, cat: Catalogue) -> None:
    start = {
        "controller": CENTERING,
        "corridor": {"seed": 2, "length_m": 25},
        "laps": 1,
        "seed": 4,
        "speed": 1000,
    }
    frames = 0
    with client.websocket_connect("/api/v1/sim") as ws:
        ws.send_json(start)
        scene = ws.receive_json()
        assert scene["type"] == "scene" and scene["cars"][0]["name"] == "ego"
        while True:
            msg = ws.receive_json()
            if msg["type"] != "frame":
                result = msg
                break
            frames += 1
            assert "ego" in msg["bodies"] and "chassis" in msg["bodies"]["ego"]
    assert result["type"] == "result" and result["finished"] and frames > 5

    car = generate(QuickStartParams(drive_gears="20-28", sensors=SIM_SENSORS), cat)
    track = generate_corridor(CorridorParams(seed=2, length_m=25)).track
    setup = track.race_setups[0].model_copy(update={"laps": 1})
    track = track.model_copy(update={"race_setups": [setup]})
    sim = Simulation(
        build_world(track, [CarEntry("ego", car.assembly, vehicle_spec(car, cat), 0)], cat, seed=4),
        seed=4,
    )
    headless = run_race(sim, load_controller(Path(CENTERING)), max_time_s=1e9)
    assert result["lap_times_s"] == [round(t, 2) for t in headless.progress.lap_times_s]


def test_sim_websocket_stop_and_errors(client: TestClient) -> None:
    with client.websocket_connect("/api/v1/sim") as ws:
        ws.send_json(
            {
                "controller": CENTERING,
                "corridor": {"seed": 1, "length_m": 25},
                "laps": 1,
                "speed": 1,
            }
        )
        assert ws.receive_json()["type"] == "scene"
        assert ws.receive_json()["type"] == "frame"
        ws.send_json({"type": "pause"})
        ws.send_json({"type": "stop"})
        while (msg := ws.receive_json())["type"] == "frame":
            pass
        assert msg["type"] == "result" and not msg["finished"]
    with client.websocket_connect("/api/v1/sim") as ws:
        ws.send_json({"controller": "/nope/missing.py"})
        assert ws.receive_json()["type"] == "error"
