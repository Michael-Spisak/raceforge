"""Spec 0005: controller host process protocol (Python side of the Rust <-> Python link)."""

import io
import json
from pathlib import Path
from typing import Any

import pytest

from raceforge.car.host import observation_from_json, serve
from raceforge.control.types import Mode

TEMPLATES = Path(__file__).parents[2] / "controllers" / "templates"

INFO = {
    "car_name": "car",
    "sensors": ["front", "left", "right", "gyro"],
    "max_steer_rad": 0.5,
    "max_speed_m_s": 2.0,
    "wheelbase_m": 0.2,
    "track_m": 0.15,
    "control_rate_hz": 50.0,
}


def obs(seq: int, **extra: Any) -> dict[str, Any]:
    o = {
        "t_s": seq * 0.02,
        "dt_s": 0.02,
        "ultrasonic_m": {"front": 2.0, "left": 0.5, "right": 0.7},
        "heading_rad": 0.0,
        "yaw_rate_rad_s": 0.0,
        "speed_m_s": 0.3,
        "mode": "test",
    }
    o.update(extra)
    return {"type": "obs", "seq": seq, "obs": o}


def run(controller: Path, msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    inp = io.StringIO("".join(json.dumps(m) + "\n" for m in msgs))
    out = io.StringIO()
    assert serve(inp, out, controller) == 0
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_template_controller_runs_through_host() -> None:
    msgs = [{"type": "hello", "info": INFO}, *(obs(i) for i in range(5)), {"type": "shutdown"}]
    replies = run(TEMPLATES / "wall_follow.py", msgs)
    assert replies[0] == {"type": "ready"}
    cmds = replies[1:]
    assert [r["seq"] for r in cmds] == list(range(5))
    for r in cmds:
        assert r["type"] == "cmd"
        assert set(r["cmd"]) == {"steering_rad", "speed_m_s"}
        assert "state" in r["channels"]  # ControllerHost emits the state machine state
    # Left wall closer than right -> steer right (negative) at some point.
    assert any(r["cmd"]["steering_rad"] < 0 for r in cmds)


def test_exception_is_reported_with_seq(tmp_path: Path) -> None:
    ctrl = tmp_path / "boom.py"
    ctrl.write_text(
        "from raceforge.control import Command, Controller, Observation\n"
        "class Boom(Controller):\n"
        "    def step(self, obs: Observation) -> Command:\n"
        "        raise RuntimeError('kaputt')\n"
    )
    replies = run(ctrl, [{"type": "hello", "info": INFO}, obs(3)])
    assert replies[1]["type"] == "error"
    assert replies[1]["seq"] == 3
    assert "kaputt" in replies[1]["detail"]


def test_non_finite_command_becomes_error(tmp_path: Path) -> None:
    ctrl = tmp_path / "nan.py"
    ctrl.write_text(
        "from raceforge.control import Command, Controller, Observation\n"
        "class NaN(Controller):\n"
        "    def step(self, obs: Observation) -> Command:\n"
        "        return Command(float('nan'), 1.0)\n"
    )
    replies = run(ctrl, [{"type": "hello", "info": INFO}, obs(0)])
    assert replies[1]["type"] == "error"


def test_obs_before_hello_is_an_error() -> None:
    replies = run(TEMPLATES / "wall_follow.py", [obs(0)])
    assert replies == [{"type": "error", "seq": 0, "detail": "obs before hello"}]


def test_observation_mapping_full() -> None:
    o = observation_from_json(
        obs(
            1,
            lidar={"angles_rad": [0.0, 1.0], "ranges_m": [1.5, None], "t_s": 0.01},
            ultrasonic_m={"front": None},
            bumper={"any": True},
            pose_estimate={"x_m": 1, "y_m": 2, "heading_rad": 0.1, "confidence": 0.5},
            battery_v=7.9,
            mode="race",
        )["obs"]
    )
    assert o.lidar is not None and o.lidar.ranges_m == (1.5, None)
    assert o.ultrasonic_m == {"front": None}
    assert o.bumper == {"any": True}
    assert o.pose_estimate is not None and o.pose_estimate.y_m == 2.0
    assert o.mode is Mode.RACE
    assert o.steering_rad is None


def test_missing_required_field_raises() -> None:
    with pytest.raises(KeyError):
        observation_from_json({"dt_s": 0.02})
