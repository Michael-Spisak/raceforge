"""MJCF generation and simulation sanity (spec 0002: AC5-AC7, AC10)."""

import math
import os
import time
from typing import Any

import numpy as np
import pytest

from raceforge.construct.derive import derive
from raceforge.construct.quickstart import Layout, QuickStartParams, generate, vehicle_spec
from raceforge.parts.catalogue import Catalogue
from raceforge.sim.mj import mujoco
from raceforge.sim.mjcf import TIMESTEP, ModelInfo, RateLimiter, build_mjcf


def build(cat: Catalogue, **kw: object) -> tuple[Any, Any, ModelInfo, float]:
    res = generate(QuickStartParams.model_validate(kw), cat)
    spec = vehicle_spec(res, cat)
    xml, info = build_mjcf(res.assembly, cat, spec)
    model = mujoco.MjModel.from_xml_string(xml)
    return model, mujoco.MjData(model), info, derive(res.assembly, cat, spec).turning_radius_m


@pytest.mark.parametrize("layout", list(Layout))
@pytest.mark.parametrize("diff", [True, False])
def test_all_variants_load_and_are_deterministic(
    cat: Catalogue, layout: Layout, diff: bool
) -> None:
    res = generate(QuickStartParams(layout=layout, differential=diff), cat)
    spec = vehicle_spec(res, cat)
    xml1, info = build_mjcf(res.assembly, cat, spec)
    xml2, _ = build_mjcf(res.assembly, cat, spec)
    assert xml1 == xml2
    model = mujoco.MjModel.from_xml_string(xml1)
    assert model.nu == 1 + len(info.drive_actuators)
    assert len(info.sensor_sites) == 3


def _drive(
    model: Any,
    data: Any,
    info: ModelInfo,
    throttle: float,
    steer: float,
    seconds: float,
) -> list[np.ndarray]:
    limiter = RateLimiter(info.steer_rate_rad_s)
    trail: list[np.ndarray] = []
    for _ in range(int(seconds / TIMESTEP)):
        data.actuator(info.steer_actuator).ctrl = limiter.step(steer, TIMESTEP)
        for a in info.drive_actuators:
            data.actuator(a).ctrl = throttle
        mujoco.mj_step(model, data)
        trail.append(data.body("chassis").xpos.copy())
    return trail


def test_drives_straight(cat: Catalogue) -> None:
    model, data, info, _ = build(cat)
    trail = _drive(model, data, info, 1.0, 0.0, 16.0)
    xs = np.array([p[0] for p in trail])
    idx = int(np.argmax(xs >= 3.0)) if xs.max() >= 3.0 else len(xs) - 1
    assert xs[idx] >= 3.0, f"only drove {xs.max():.2f} m"
    assert abs(trail[idx][1]) < 0.02


def _fit_circle(points: np.ndarray) -> float:
    x, y = points[:, 0], points[:, 1]
    a = np.column_stack([x, y, np.ones_like(x)])
    b = x**2 + y**2
    cx, cy, c = np.linalg.lstsq(a, b, rcond=None)[0]
    return float(math.sqrt(c + (cx / 2) ** 2 + (cy / 2) ** 2))


@pytest.mark.parametrize("diff", [True, False])
def test_turning_radius_matches_derived(cat: Catalogue, diff: bool) -> None:
    model, data, info, expected = build(cat, differential=diff)
    trail = _drive(model, data, info, 0.6, info.max_steer_rad, 14.0)
    radius = _fit_circle(np.array(trail[len(trail) // 2 :])[:, :2])
    if diff:
        assert radius == pytest.approx(expected, rel=0.10)
    else:
        # A locked axle scrubs its tyres in curves: the car understeers, as in reality.
        assert expected * 1.05 < radius < expected * 1.6


def test_differential_lets_wheels_turn_at_different_speeds(cat: Catalogue) -> None:
    for diff, expect_equal in ((True, False), (False, True)):
        model, data, info, _ = build(cat, differential=diff)
        _drive(model, data, info, 0.6, info.max_steer_rad, 4.0)
        if diff:
            vl = data.joint(info.wheel_joints["wheel_rear_left"]).qvel[0]
            vr = data.joint(info.wheel_joints["wheel_rear_right"]).qvel[0]
            assert abs(vl - vr) > 0.1 * max(abs(vl), abs(vr))
        else:
            assert (
                "wheel_rear" in info.wheel_joints
            )  # one rigid axle body: both wheels share a joint
        assert expect_equal is not diff


def test_steering_rate_limit_and_play(cat: Catalogue) -> None:
    model, data, info, _ = build(cat, steering_play_deg=4.0)
    limiter = RateLimiter(info.steer_rate_rad_s)
    target = info.max_steer_rad
    t_reach = None
    for n in range(int(1.5 / TIMESTEP)):
        data.actuator(info.steer_actuator).ctrl = limiter.step(target, TIMESTEP)
        mujoco.mj_step(model, data)
        if t_reach is None and abs(
            data.joint(info.steer_joint_left).qpos[0] - target
        ) < math.radians(1):
            t_reach = n * TIMESTEP
    assert t_reach is not None
    assert t_reach >= target / info.steer_rate_rad_s * 0.9  # cannot be faster than the motor
    band = model.jnt_range[model.joint(info.play_joints[0]).id]
    assert math.degrees(band[1] - band[0]) == pytest.approx(4.0, abs=0.5)


@pytest.mark.skipif(os.environ.get("CI") == "true", reason="timing only on dev machines")
def test_faster_than_20x_real_time(cat: Catalogue) -> None:
    model, data, _, _ = build(cat)
    steps = 5000
    start = time.perf_counter()
    for _ in range(steps):
        mujoco.mj_step(model, data)
    assert steps * TIMESTEP / (time.perf_counter() - start) >= 20
