"""Spec 0012: build a bundle and deploy it to a USB stick from the app's engine API."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from raceforge.api.service import TEMPLATES_DIR, Engine
from raceforge.server.app import create_app

CAR_YAML = Path(__file__).resolve().parents[2] / "controllers" / "car.example.yaml"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("RACEFORGE_WORKSPACE_DIR", str(tmp_path / "data" / "workspace"))
    return TestClient(create_app(Engine(), frontend_dist=None))


def test_build_and_usb_deploy(client: TestClient, tmp_path: Path) -> None:
    body = {"controller": str(TEMPLATES_DIR / "wall_follow.py"), "car_config": str(CAR_YAML)}
    r = client.post("/api/v1/car/bundle", json=body)
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["mode"] == "test" and info["car_name"] == "car-1" and len(info["digest"]) == 64
    assert Path(info["path"]) == tmp_path / "data" / "bundles" / "wall_follow"

    stick = tmp_path / "stick"
    stick.mkdir()
    r = client.post(
        "/api/v1/car/deploy", json={"bundle": info["path"], "target": "usb", "stick": str(stick)}
    )
    assert r.status_code == 200, r.text
    assert (Path(r.json()["usb_path"]) / "bundle.json").is_file()

    r = client.get("/api/v1/car/deploy/usb-result", params={"stick": str(stick)})
    assert r.status_code == 200 and r.json() is None  # not plugged into the car yet
    result = {"ok": True, "name": "wall_follow", "digest": info["digest"], "service": "running"}
    (stick / "raceforge" / "result.json").write_text(json.dumps(result))
    r = client.get("/api/v1/car/deploy/usb-result", params={"stick": str(stick)})
    assert r.json()["ok"] is True and r.json()["service"] == "running"


def test_errors_are_reported(client: TestClient, tmp_path: Path) -> None:
    bad = tmp_path / "car.yaml"
    bad.write_text("robot: {}\n")
    r = client.post(
        "/api/v1/car/bundle",
        json={"controller": str(TEMPLATES_DIR / "wall_follow.py"), "car_config": str(bad)},
    )
    assert r.status_code == 422 and "robot" in r.json()["detail"]
    r = client.post("/api/v1/car/deploy", json={"bundle": str(tmp_path), "target": "ssh"})
    assert r.status_code == 422  # no host
    r = client.post(
        "/api/v1/car/deploy",
        json={"bundle": str(tmp_path), "target": "usb", "stick": str(tmp_path / "missing")},
    )
    assert r.status_code == 502 and "not a bundle" in r.json()["detail"]


def test_race_bundle_from_the_app(client: TestClient) -> None:
    """Spec 0030 AC2: race mode is in the manifest (the car arms it only after the radio check)."""
    body = {
        "controller": str(TEMPLATES_DIR / "wall_follow.py"),
        "car_config": str(CAR_YAML),
        "name": "race-day",
        "race": True,
    }
    r = client.post("/api/v1/car/bundle", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "race"
    manifest = json.loads((Path(r.json()["path"]) / "bundle.json").read_text(encoding="utf-8"))
    assert manifest["runtime"]["mode"] == "race"
