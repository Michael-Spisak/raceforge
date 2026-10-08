"""Spec 0010 AC2: teleop of the simulated car: override, dead-man, release, stop, demo log."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from raceforge.api.models import SimStart
from raceforge.api.service import Engine
from raceforge.api.sim_session import DEADMAN_S, SimSession
from raceforge.server.app import create_app
from raceforge.sim.record import read_frames
from raceforge.track.procedural import CorridorParams

ROOT = Path(__file__).resolve().parents[2]
CENTERING = str(ROOT / "controllers" / "templates" / "centering.py")


@pytest.fixture(scope="module")
def engine() -> Engine:
    return Engine()


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def _session(
    engine: Engine, controller: str, record: Path | None = None
) -> tuple[SimSession, Clock]:
    start = SimStart(
        controller=controller,
        corridor=CorridorParams(seed=1, length_m=25),
        record_path=str(record) if record else None,
    )
    s = SimSession(engine, start)
    clock = Clock()
    s.teleop.clock = clock
    return s, clock


def _steps(s: SimSession, n: int, clock: Clock, refresh: tuple[float, float] | None = None) -> None:
    for _ in range(n):
        if refresh is not None:
            s.teleop.drive(*refresh)
        s.advance(1)
        clock.t += s.sim.control_dt


def test_none_controller_stands_still_until_teleop(engine: Engine) -> None:
    s, clock = _session(engine, "none")
    _steps(s, 50, clock)
    assert s.sim.progress("ego").distance_m < 0.01 and s.race.last_state == "run"
    _steps(s, 150, clock, refresh=(0.0, 0.5))
    assert s.race.last_state == "teleop" and s.race.last_cmd.speed_m_s == 0.5
    assert s.sim.progress("ego").distance_m > 0.1


def test_deadman_stops_and_release_hands_back(engine: Engine) -> None:
    s, clock = _session(engine, CENTERING)
    s.teleop.drive(0.1, 0.0)  # hold the car still against the controller
    _steps(s, 5, clock)
    assert s.race.last_state == "teleop" and s.race.last_cmd.speed_m_s == 0.0
    clock.t += DEADMAN_S + 0.01  # operator stopped sending
    _steps(s, 1, clock)
    assert s.race.last_state == "deadman_stop" and s.race.last_cmd.speed_m_s == 0.0
    s.teleop.drive(0.0, 0.4)  # a new message re-engages
    _steps(s, 1, clock)
    assert s.race.last_state == "teleop"
    s.teleop.release()
    _steps(s, 3, clock)
    assert s.race.last_state == s.race.controller.state and s.race.last_cmd.speed_m_s > 0


def test_stop_car_latches(engine: Engine) -> None:
    s, clock = _session(engine, CENTERING)
    s.teleop.stop()
    _steps(s, 5, clock)
    s.teleop.release()  # release does not undo an operator stop
    _steps(s, 5, clock)
    assert s.race.last_state == "stop" and s.race.last_cmd.speed_m_s == 0.0


def test_non_finite_teleop_is_a_stop(engine: Engine) -> None:
    s, clock = _session(engine, "none")
    s.teleop.drive(float("nan"), float("inf"))
    _steps(s, 1, clock)
    assert s.race.last_cmd.speed_m_s == 0.0 and s.race.last_cmd.steering_rad == 0.0


def test_demonstration_is_recorded(engine: Engine, tmp_path: Path) -> None:
    log = tmp_path / "demo.mcap"
    s, clock = _session(engine, "none", record=log)
    _steps(s, 20, clock)
    _steps(s, 30, clock, refresh=(0.2, 0.6))
    s.result()
    frames = read_frames(log)
    teleop = [f for f in frames if f.state == "teleop"]
    assert len(teleop) == 30 and len(frames) == 50
    assert {(round(f.cmd.steering_rad, 3), f.cmd.speed_m_s) for f in teleop} == {(0.2, 0.6)}


def test_websocket_teleop_messages(engine: Engine) -> None:
    client = TestClient(create_app(engine, frontend_dist=None))
    with client.websocket_connect("/api/v1/sim") as ws:
        ws.send_json({"controller": "none", "corridor": {"seed": 1, "length_m": 25}, "speed": 1})
        assert ws.receive_json()["type"] == "scene"
        states: set[str] = set()
        for _ in range(12):
            ws.send_json({"type": "teleop", "steer": 0.0, "speed": 0.5})
            msg = ws.receive_json()
            if msg["type"] == "frame":
                states.add(msg["ego"]["state"])
        assert "teleop" in states
        ws.send_json({"type": "stop_car"})
        last = [ws.receive_json() for _ in range(3)][-1]
        assert last["ego"]["state"] == "stop"
        ws.send_json({"type": "stop"})
        while ws.receive_json()["type"] == "frame":
            pass
