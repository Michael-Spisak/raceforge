"""Spec 0033: calibration maths (AC2, AC3) and applying results to a car config (AC4)."""

import math
from pathlib import Path

import pytest

from raceforge.api import fleet
from raceforge.api.calibration import circle, straight
from raceforge.api.models import CalibrationSample


def _drive(
    speed: float, yaw_rate: float, steer: float = 0.0, n: int = 100, dt_ms: float = 50
) -> list[CalibrationSample]:
    return [
        CalibrationSample(
            t_ms=i * dt_ms,
            frame={
                "meas": {"speed_m_s": speed, "yaw_rate_rad_s": yaw_rate},
                "cmd": {"steering_rad": steer},
            },
        )
        for i in range(n)
    ]


def test_straight_scales_counts_and_trims_a_left_drift() -> None:
    # encoder says 0.55 m/s for 99 * 0.05 s = 2.72 m; the tape says 2.475 m (10 % over-count)
    samples = _drive(0.55, 0.02)
    true = 0.55 * 99 * 0.05 / 1.1
    r = straight(samples, true, drive_counts_per_m=2000.0, steer_trim_rad=0.0, wheelbase_m=0.2)
    assert r.changes["ev3.drive_counts_per_m"] == pytest.approx(2000.0 * 1.1, rel=1e-3)
    assert r.changes["ev3.steer_trim_rad"] < 0  # drifted left: steer a little right


def test_circle_doubles_the_ratio_when_half_the_angle_is_reached() -> None:
    wheelbase, commanded = 0.2, 0.4
    achieved = commanded / 2
    radius = wheelbase / math.tan(achieved)
    speed = 0.5
    left = _drive(speed, speed / radius, commanded)
    right = _drive(speed, -speed / radius, -commanded)
    r = circle(left, right, wheelbase, steer_motor_deg_per_rad=100.0)
    assert r.changes["ev3.steer_motor_deg_per_rad"] == pytest.approx(200.0, rel=1e-3)
    assert abs(float(r.details["asymmetry_deg"])) < 0.1


def test_apply_keeps_comments_backs_up_and_refuses_invalid(tmp_path: Path) -> None:
    fleet.create_car("car-2", tmp_path)
    p = tmp_path / "car-2.yaml"
    fleet.apply_changes(
        "car-2", {"ev3.drive_counts_per_m": 2100.5, "ev3.steer_trim_rad": -0.01}, tmp_path
    )
    text = p.read_text(encoding="utf-8")
    assert "drive_counts_per_m: 2100.5  # PLACEHOLDER" in text
    assert "steer_trim_rad: -0.01" in text and "car_name: car-2" in text
    assert (tmp_path / "car-2.yaml.bak").is_file()
    with pytest.raises(ValueError):
        fleet.apply_changes("car-2", {"ev3.steer_trim_rad": 5.0}, tmp_path)
    assert "steer_trim_rad: -0.01" in p.read_text(encoding="utf-8")  # unchanged after a refusal
    assert [c.name for c in fleet.list_cars(tmp_path)] == ["car-2"]
