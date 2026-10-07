"""Spec 0004: AC3-AC6 (host policy, params, state machine, filters)."""

import math
from enum import Enum, auto
from pathlib import Path

import pytest
from pydantic import ValidationError

from raceforge.control import (
    PID,
    Command,
    Controller,
    ControllerHost,
    ControllerParams,
    Ema,
    LidarScan,
    Median,
    Observation,
    RateLimiter,
    RobotInfo,
    StateMachine,
    Tunable,
    centering_error,
    sector_min,
)


class FakeIO:
    def __init__(self) -> None:
        self.written: list[Command] = []
        self.channels: dict[str, float | int | bool | str] = {}
        self.t = 0.0

    @property
    def info(self) -> RobotInfo:
        return RobotInfo("fake", ("front",), 0.5, 0.4, 0.12, 0.09, 50.0)

    def read(self) -> Observation:
        self.t += 0.02
        return Observation(t_s=self.t, dt_s=0.02, ultrasonic_m={"front": 1.0})

    def write(self, cmd: Command) -> None:
        self.written.append(cmd)

    def emit(self, channel: str, value: float | int | bool | str) -> None:
        self.channels[channel] = value

    def note(self, text: str, tags: list[str] | None = None) -> None:
        pass


class Flaky(Controller[ControllerParams]):
    def step(self, obs: Observation) -> Command:
        if obs.t_s > 0.05:
            raise RuntimeError("boom")
        return Command(0.1, 0.3)


class Slow(Controller[ControllerParams]):
    def step(self, obs: Observation) -> Command:
        import time

        time.sleep(0.003)
        return Command()


def test_exception_is_reported_and_run_continues() -> None:
    io = FakeIO()
    host = ControllerHost(Flaky(), io, deadline_s=1.0)
    host.start()
    for _ in range(5):
        host.step()
    assert len(io.written) == 5
    assert io.written[-1] == Command()  # stop command after the exception
    assert host.problems[0].kind == "exception" and host.problems[0].step == 2
    assert "boom" in host.problems[0].detail
    strict = ControllerHost(Flaky(), FakeIO(), 1.0, on_exception="raise")
    strict.start()
    with pytest.raises(RuntimeError):
        for _ in range(5):
            strict.step()


def test_deadline_is_reported() -> None:
    host = ControllerHost(Slow(), FakeIO(), deadline_s=0.001)
    host.start()
    host.step()
    assert host.problems and host.problems[0].kind == "deadline"
    assert host.max_step_s >= 0.003


class P(ControllerParams):
    speed: float = Tunable(0.3, 0.1, 1.0, step=0.05)
    name: str = "x"


def test_params_yaml_and_tunables(tmp_path: Path) -> None:
    f = tmp_path / "p.yaml"
    f.write_text("speed: 0.5\n")
    assert P.from_yaml(f).speed == 0.5
    f.write_text("speed: 5\n")
    with pytest.raises(ValidationError):
        P.from_yaml(f)
    f.write_text("sped: 0.5\n")
    with pytest.raises(ValidationError):
        P.from_yaml(f)
    assert P.tunables() == {"speed": (0.1, 1.0, 0.05)}


class St(Enum):
    A = auto()
    B = auto()
    C = auto()


def test_state_machine() -> None:
    log: list[str] = []
    flag = {"go": False}
    sm = StateMachine(St.A)
    sm.transition(St.A, St.B, lambda: flag["go"])
    sm.transition(None, St.C, lambda: sm.state is St.B and sm.time_in_state_s > 0.05)
    sm.on_exit(St.A, lambda: log.append("exit A"))
    sm.on_enter(St.B, lambda: log.append("enter B"))
    assert sm.update(0.02) is St.A
    flag["go"] = True
    assert sm.update(0.02) is St.B and log == ["exit A", "enter B"]
    assert sm.update(0.02) is St.B
    assert sm.update(0.04) is St.C


def test_pid_reference_values() -> None:
    pid = PID(kp=2.0, ki=1.0, kd=0.5)
    assert pid.update(1.0, 0.0, 0.1) == pytest.approx(2.0 + 0.1)  # P + I, no D on first call
    out = pid.update(1.0, 0.2, 0.1)  # error 0.8, integral 0.18, derivative -2
    assert out == pytest.approx(2 * 0.8 + 1.0 * 0.18 + 0.5 * -2.0)
    sat = PID(kp=10.0, ki=10.0, out_max=1.0)
    for _ in range(50):
        sat.update(1.0, 0.0, 0.1)
    assert sat.integral < 1.0  # anti-windup


def test_filters_and_helpers() -> None:
    ema = Ema(0.5)
    assert ema.update(1.0) == 1.0 and ema.update(3.0) == 2.0
    med = Median(3)
    assert [med.update(v) for v in (1.0, 9.0, 2.0)] == [1.0, 5.0, 2.0]
    rl = RateLimiter(1.0)
    assert rl.update(5.0, 0.5) == 0.5
    assert centering_error(1.0, 0.5) == 0.25 and centering_error(None, 1.0) == 0.0
    scan = LidarScan(tuple(math.radians(a) for a in (0, 90, 180, 270)), (2.0, 1.0, None, 0.5), 0.0)
    assert sector_min(scan, -10, 10) == 2.0
    assert sector_min(scan, 80, 100) == 1.0
    assert sector_min(scan, -100, -80) == 0.5
    assert sector_min(scan, 170, -170) is None
    with pytest.raises(ValueError):
        Ema(0.0)
