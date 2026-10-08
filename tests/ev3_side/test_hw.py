"""Spec 0005 AC8: Ev3Hardware against a fake python-ev3dev2 (API usage, unit handling, caching)."""

import sys
import types
from collections.abc import Iterator
from typing import Any, ClassVar

import pytest


class FakeMotor:
    def __init__(self, port: str) -> None:
        self.port = port
        self.position = 0
        self.speed = 0
        self.max_speed = 1050
        self.log: list[tuple[str, dict[str, Any]]] = []

    def reset(self) -> None:
        self.log.append(("reset", {}))

    def run_to_abs_pos(self, **kw: Any) -> None:
        self.log.append(("run_to_abs_pos", kw))

    def run_forever(self, **kw: Any) -> None:
        self.log.append(("run_forever", kw))

    def stop(self, **kw: Any) -> None:
        self.log.append(("stop", kw))


class FakeSensor:
    values: ClassVar[dict[str, list[float]]] = {}

    def __init__(self, port: str) -> None:
        self.port = port
        self.mode = ""
        self.is_pressed = False

    def value(self, n: int = 0) -> float:
        return FakeSensor.values[self.port][n]


class Counter:
    reads = 0


class FakePower:
    @property
    def measured_volts(self) -> float:
        Counter.reads += 1
        return 7.85


class FakeButton:
    buttons_pressed: ClassVar[list[str]] = ["enter"]


class FakeLeds:
    def __init__(self) -> None:
        self.colors: dict[str, str] = {}

    def set_color(self, group: str, color: str) -> None:
        self.colors[group] = color


class FakeDisplay:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def clear(self) -> None:
        self.texts = []

    def text_pixels(self, text: str, **kw: Any) -> None:
        assert kw.get("clear_screen") is False
        self.texts.append(text)

    def update(self) -> None:
        pass


@pytest.fixture
def ev3dev2(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    mods: dict[str, Any] = {
        name: types.ModuleType(name)
        for name in [
            "ev3dev2",
            "ev3dev2.motor",
            "ev3dev2.sensor",
            "ev3dev2.sensor.lego",
            "ev3dev2.button",
            "ev3dev2.display",
            "ev3dev2.led",
            "ev3dev2.power",
        ]
    }
    m: Any = mods["ev3dev2.motor"]
    m.Motor = FakeMotor
    for x in "ABCD":
        setattr(m, "OUTPUT_" + x, "out" + x)
    s: Any = mods["ev3dev2.sensor"]
    for i in "1234":
        setattr(s, "INPUT_" + i, "in" + i)
    lego: Any = mods["ev3dev2.sensor.lego"]
    lego.UltrasonicSensor = lego.GyroSensor = lego.TouchSensor = FakeSensor
    s.lego = lego
    root: Any = mods["ev3dev2"]
    root.motor, root.sensor = m, s
    mods["ev3dev2.button"].Button = FakeButton
    mods["ev3dev2.display"].Display = FakeDisplay
    mods["ev3dev2.led"].Leds = FakeLeds
    mods["ev3dev2.power"].PowerSupply = FakePower
    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)
    FakeSensor.values = {"in1": [420.0], "in2": [2550.0], "in3": [0.0], "in4": [-90.0, 12.0]}
    Counter.reads = 0
    yield


def make(**cfg: Any) -> Any:
    from raceforge_ev3.hw import Ev3Hardware

    return Ev3Hardware(cfg)


def test_read_converts_units_and_flags(ev3dev2: None) -> None:
    hw = make(
        sensors={"1": "ultrasonic", "2": "ultrasonic", "3": "touch", "4": "gyro"},
        estop_touch_port="3",
    )
    hw.steer_motor.position, hw.steer_motor.speed = 30, -5
    r = hw.read()
    assert r["motors"][0] == (30, -5)  # steering on A
    assert r["motors"][2] == (0, 0)  # C not configured
    assert r["ultrasonic_mm"][:2] == [420, 0xFFFF]  # 255 cm = nothing in range
    assert r["ultrasonic_mm"][3] == 0xFFFF  # port 4 is the gyro
    assert (r["gyro_angle_deg"], r["gyro_rate_dps"]) == (-90, 12)
    assert r["battery_mv"] == 7850
    assert r["buttons"] == 1 << 4  # enter
    assert r["touch"] == 0 and r["estop"] is False
    hw.touch[2].is_pressed = True
    r = hw.read()
    assert r["touch"] == 1 << 2 and r["estop"] is True


def test_slow_values_are_cached(ev3dev2: None) -> None:
    hw = make(slow_every=10)
    for _ in range(25):
        hw.read()
    assert Counter.reads == 3  # cycles 0, 10, 20


def test_setpoints_written_only_on_change(ev3dev2: None) -> None:
    hw = make(drive_motors=["B", "C"], drive_invert=[False, True])
    b, c = hw.motors["B"], hw.motors["C"]
    hw.steer(1550)  # centi-degrees -> 16 deg (rounded)
    hw.steer(1560)
    moves = [kw for k, kw in hw.steer_motor.log if k == "run_to_abs_pos"]
    assert moves == [{"position_sp": 16, "speed_sp": 800, "stop_action": "hold"}]
    hw.drive(400)
    hw.drive(400)
    hw.drive(5000)  # clamped to the motor maximum
    assert [kw["speed_sp"] for k, kw in b.log if k == "run_forever"] == [400, 1050]
    assert [kw["speed_sp"] for k, kw in c.log if k == "run_forever"] == [-400, -1050]
    hw.brake()
    hw.brake()
    assert [kw for k, kw in b.log if k == "stop"] == [{"stop_action": "brake"}]


def test_show_sets_leds_and_lcd(ev3dev2: None) -> None:
    hw = make()
    hw.show("LINK LOST")
    assert hw.leds.colors == {"LEFT": "RED", "RIGHT": "RED"}
    assert hw.display.texts == ["RaceForge", "LINK LOST"]
    hw.show("RUN")
    assert hw.leds.colors["LEFT"] == "GREEN"


def test_bad_config_is_rejected(ev3dev2: None) -> None:
    with pytest.raises(ValueError, match="unknown sensor kind"):
        make(sensors={"1": "colour"})
    with pytest.raises(ValueError, match="estop_touch_port"):
        make(sensors={"1": "ultrasonic"}, estop_touch_port="1")
