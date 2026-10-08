"""Spec 0005 deploy bundle: build, hash check, manifest shape (read by rf-runtime)."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from raceforge.car.bundle import (
    MANIFEST,
    Ev3Spec,
    LidarSpec,
    RobotSpec,
    RuntimeSpec,
    TelemetrySpec,
    build_bundle,
    load_manifest,
    verify_bundle,
)

TEMPLATES = Path(__file__).parents[2] / "controllers" / "templates"

ROBOT = RobotSpec(
    car_name="car",
    sensors=["front", "left", "right", "gyro"],
    max_steer_rad=0.45,
    max_speed_m_s=1.5,
    wheelbase_m=0.2,
    track_m=0.15,
    control_rate_hz=50,
)
EV3 = Ev3Spec(
    steer_motor_deg_per_rad=171.9,
    drive_counts_per_m=2046.0,
    ultrasonic={"front": "1", "left": "2", "right": "3"},
)


def test_build_copies_files_and_hashes(tmp_path: Path) -> None:
    m = build_bundle(tmp_path / "b", TEMPLATES / "wall_follow.py", ROBOT, EV3)
    b = tmp_path / "b"
    assert (b / "controller.py").read_bytes() == (TEMPLATES / "wall_follow.py").read_bytes()
    assert (b / "controller.yaml").is_file() and m.params is not None
    assert verify_bundle(b) == []
    assert load_manifest(b) == m
    raw = json.loads((b / MANIFEST).read_text())
    assert raw["schema"] == "car_bundle" and raw["schema_version"] == 1
    assert raw["ev3"]["ultrasonic"] == {"front": "1", "left": "2", "right": "3"}
    assert raw["runtime"] == {
        "mode": "test",
        "deadline_ms": 15.0,
        "test_speed_limit_m_s": None,
        "radio_usb_ids": [],
    }


def test_tampered_or_missing_file_is_detected(tmp_path: Path) -> None:
    b = tmp_path / "b"
    build_bundle(b, TEMPLATES / "wall_follow.py", ROBOT, EV3)
    with (b / "controller.py").open("a") as f:
        f.write("\n# edited on the car\n")
    (b / "controller.yaml").unlink()
    assert verify_bundle(b) == ["hash mismatch: controller.py", "missing controller.yaml"]


def test_broken_controller_fails_at_build_time(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("raise SyntaxError('nope')\n")
    with pytest.raises(SyntaxError):
        build_bundle(tmp_path / "b", bad, ROBOT, EV3)
    assert not (tmp_path / "b").exists()


def test_invalid_specs_rejected() -> None:
    with pytest.raises(ValidationError):
        Ev3Spec(steer_motor_deg_per_rad=1, drive_counts_per_m=1, steer_motor="E")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        RuntimeSpec(deadline_ms=100)
    with pytest.raises(ValidationError):
        RuntimeSpec(radio_usb_ids=["0BDA-8179"])
    with pytest.raises(ValidationError):
        RobotSpec(**{**ROBOT.model_dump(), "control_rate_hz": 500})


def test_race_mode_and_speed_limit_round_trip(tmp_path: Path) -> None:
    rt = RuntimeSpec(mode="race", test_speed_limit_m_s=0.8)
    build_bundle(tmp_path / "b", TEMPLATES / "centering.py", ROBOT, EV3, runtime=rt, name="quali")
    m = load_manifest(tmp_path / "b")
    assert m.runtime == rt and m.name == "quali"


def test_lidar_section(tmp_path: Path) -> None:
    lidar = LidarSpec(device="/dev/ttyAMA0", mount_offset_rad=3.14159, policy="optional")
    build_bundle(tmp_path / "b", TEMPLATES / "centering.py", ROBOT, EV3, lidar=lidar)
    raw = json.loads((tmp_path / "b" / MANIFEST).read_text())
    assert raw["lidar"] == {
        "device": "/dev/ttyAMA0",
        "mount_offset_rad": 3.14159,
        "policy": "optional",
        "timeout_ms": 300,
    }
    build_bundle(tmp_path / "c", TEMPLATES / "centering.py", ROBOT, EV3)
    assert json.loads((tmp_path / "c" / MANIFEST).read_text())["lidar"] is None
    with pytest.raises(ValidationError):
        LidarSpec(timeout_ms=50)


def test_telemetry_section_and_token_rule(tmp_path: Path) -> None:
    tel = TelemetrySpec(token="Xq3_kL9-vB2nM7pR")
    build_bundle(tmp_path / "b", TEMPLATES / "centering.py", ROBOT, EV3, telemetry=tel)
    raw = json.loads((tmp_path / "b" / MANIFEST).read_text())
    assert raw["telemetry"] == {
        "bind": "0.0.0.0:8765",
        "rate_hz": 20.0,
        "token": "Xq3_kL9-vB2nM7pR",
        "max_clients": 4,
    }
    assert TelemetrySpec(bind="127.0.0.1:8765").token is None  # loopback: no token needed
    assert TelemetrySpec(bind="[::1]:8765").token is None
    bad_cases: list[dict[str, Any]] = [
        {},  # default bind 0.0.0.0 without a token
        {"bind": "10.0.0.5:8765"},
        {"token": "too-short"},
        {"token": "has spaces in it, sixteen+"},
        {"bind": "car.local:8765", "token": "Xq3_kL9-vB2nM7pR"},
        {"bind": "127.0.0.1:70000"},
        {"bind": "127.0.0.1:8765", "rate_hz": 100},
    ]
    for bad in bad_cases:
        with pytest.raises(ValidationError):
            TelemetrySpec(**bad)
