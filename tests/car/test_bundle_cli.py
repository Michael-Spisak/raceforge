"""`raceforge bundle` (spec 0005 "Deploy": bundle = controller + params + car config + versions)."""

import json
from pathlib import Path

import pytest
import yaml

from raceforge.car.bundle import TOKEN_ENV, load_car_config, load_manifest, verify_bundle
from raceforge.cli import main as cli

ROOT = Path(__file__).parents[2]
EXAMPLE = ROOT / "controllers" / "car.example.yaml"
CONTROLLER = ROOT / "controllers" / "templates" / "wall_follow.py"


def car_file(tmp: Path, **changes: object) -> Path:
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    for key, value in changes.items():
        if value is None:
            raw.pop(key, None)
        else:
            raw[key] = value
    path = tmp / "car.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_example_car_config_is_valid() -> None:
    car = load_car_config(EXAMPLE)
    assert car.robot.control_rate_hz == 50 and car.runtime.mode == "test"
    assert set(car.ev3.ultrasonic) <= set(car.robot.sensors)


def test_bundle_from_controller_and_car_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "bundle"
    rc = cli(["bundle", str(CONTROLLER), "--car", str(EXAMPLE), "--out", str(out)])
    assert rc == 0, capsys.readouterr().err
    m = load_manifest(out)
    car = load_car_config(EXAMPLE)
    assert (m.name, m.robot, m.ev3, m.lidar, m.runtime) == (
        "wall_follow",
        car.robot,
        car.ev3,
        car.lidar,
        car.runtime,
    )
    assert m.params is not None  # wall_follow.yaml next to the controller
    assert verify_bundle(out) == []
    text = capsys.readouterr().out
    assert "wall_follow" in text and "raceforge deploy" in text


def test_race_flag_and_name(tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    args = ["bundle", str(CONTROLLER), "--car", str(EXAMPLE), "--out", str(out)]
    assert cli([*args, "--race", "--name", "quali-1"]) == 0
    m = load_manifest(out)
    assert m.runtime.mode == "race" and m.name == "quali-1"


def test_invalid_car_config_names_the_problem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for changes, expected in [
        ({"ev3": None}, "ev3"),
        ({"wheels": 4}, "wheels"),
        ({"robot": {"car_name": "car"}}, "robot.sensors"),
    ]:
        path = car_file(tmp_path, **changes)
        rc = cli(["bundle", str(CONTROLLER), "--car", str(path), "--out", str(tmp_path / "b")])
        assert rc == 1
        assert expected in capsys.readouterr().err, changes
    assert not (tmp_path / "b").exists()


def test_telemetry_token_comes_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = car_file(tmp_path, telemetry={"bind": "0.0.0.0:8765"})
    out = tmp_path / "bundle"
    args = ["bundle", str(CONTROLLER), "--car", str(path), "--out", str(out)]
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    assert cli(args) == 1  # non-loopback without a token
    assert "token" in capsys.readouterr().err
    monkeypatch.setenv(TOKEN_ENV, "a-long-random-token-1234")
    assert cli(args) == 0
    raw = json.loads((out / "bundle.json").read_text())
    assert raw["telemetry"]["token"] == "a-long-random-token-1234"
    assert "warning" not in capsys.readouterr().err
    # A token written into the car file works, but the repository is public: warn.
    path = car_file(tmp_path, telemetry={"bind": "0.0.0.0:8765", "token": "token-in-the-file-1234"})
    assert cli(args) == 0
    assert "warning" in capsys.readouterr().err


def test_out_dir_is_only_replaced_when_it_holds_a_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "notes.txt").write_text("mine")
    args = ["bundle", str(CONTROLLER), "--car", str(EXAMPLE), "--out", str(out)]
    assert cli(args) == 1
    assert "not a bundle" in capsys.readouterr().err
    assert (out / "notes.txt").read_text() == "mine"
    (out / "notes.txt").unlink()
    assert cli(args) == 0
    (out / "stale.py").write_text("old")  # a previous bundle is replaced as a whole
    assert cli(args) == 0
    assert not (out / "stale.py").exists() and verify_bundle(out) == []


def test_broken_controller_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("this is not python\n")
    out = tmp_path / "bundle"
    assert cli(["bundle", str(bad), "--car", str(EXAMPLE), "--out", str(out)]) == 1
    assert "bad.py" in capsys.readouterr().err
    assert not out.exists()
