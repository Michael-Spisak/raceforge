"""Spec 0003 AC1-AC4, AC6-AC9."""

import math
import os
import time
from pathlib import Path

import numpy as np
import pytest

from raceforge.parts.catalogue import Catalogue
from raceforge.sim.engine import CarCommand, Simulation, centreline_follower
from raceforge.sim.record import Recorder, frame_from, read_frames
from raceforge.sim.world import CarEntry
from raceforge.track.procedural import CorridorParams, generate_corridor
from tests.sim.helpers import box_room, car, sim_in


def _run(
    sim: Simulation, seconds: float, cmd: CarCommand | None = None, car_name: str = "ego"
) -> None:
    for _ in range(round(seconds / sim.control_dt)):
        sim.command(car_name, cmd or CarCommand())
        sim.step()


def test_same_seed_same_trajectory(cat: Catalogue) -> None:
    track = generate_corridor(CorridorParams(seed=2, length_m=30)).track

    def trajectory() -> list[tuple[float, ...]]:
        sim = sim_in(track, cat, [car(cat)], seed=7)
        out = []
        for _ in range(250):
            sim.command("ego", centreline_follower(sim, "ego", 0.3))
            sim.step()
            r = sim.readings("ego")
            front = r.ultrasonic_m["front"]
            out.append(
                (
                    sim.truth("ego").x,
                    sim.truth("ego").y,
                    front.value if front and front.value else -1.0,
                )
            )
        return out

    assert trajectory() == trajectory()


def test_ultrasonic_distance_range_and_latency(cat: Catalogue) -> None:
    sim = sim_in(box_room(width=4.0, length=2.0), cat, [car(cat)])
    _run(sim, 0.5)
    r = sim.readings("ego")
    front = r.ultrasonic_m["front"]
    assert front is not None and front.value is not None
    sensor_x = float(sim.data.site(sim.world.cars["ego"].sensors[1].site).xpos[0])
    assert front.value == pytest.approx(2.0 - sensor_x, abs=0.04)
    assert r.t - front.t_sample >= 0.03 - 1e-6  # latency
    # side sensors: 2 m to each wall is within range
    assert r.ultrasonic_m["left"] is not None
    far = sim_in(box_room(width=8.0, length=9.0), cat, [car(cat)])
    _run(far, 0.5)
    left = far.readings("ego").ultrasonic_m["left"]
    assert left is not None and left.value is None  # > 2.55 m -> no echo


def test_ultrasonic_dropout_at_grazing_angle(cat: Catalogue) -> None:
    # Narrow, long room; car yawed 10° towards the left wall: every cone ray that reaches a wall
    # within range hits the side wall at > 60° incidence (the far end wall is out of range).
    sim = sim_in(box_room(width=1.0, length=9.0), cat, [car(cat)])
    sim.data.qpos[3:7] = [math.cos(math.radians(5)), 0, 0, math.sin(math.radians(5))]
    _run(sim, 0.5)
    front = sim.readings("ego").ultrasonic_m["front"]
    assert front is not None and front.value is None


def test_lidar_matches_room_geometry_and_glass_dropouts(cat: Catalogue) -> None:
    sim = sim_in(box_room(width=4.0, length=6.0), cat, [car(cat)])
    lidar = sim.cars["ego"].lidar
    assert lidar is not None
    lidar.p = lidar.p.model_copy(update={"noise_std_m": 0.0})
    _run(sim, 0.4)
    scan = sim.readings("ego").lidar
    assert scan is not None
    pos = sim.data.site(sim.world.cars["ego"].sensors[0].site).xpos
    errors = []
    for a, d in zip(scan.angles_rad, scan.ranges_m, strict=True):
        if d is None:
            continue
        dx, dy = math.cos(a), math.sin(a)
        ts = [
            t
            for t in (
                (6 - pos[0]) / dx if dx > 1e-9 else math.inf,
                -pos[0] / dx if dx < -1e-9 else math.inf,
                (4 - pos[1]) / dy if dy > 1e-9 else math.inf,
                -pos[1] / dy if dy < -1e-9 else math.inf,
            )
        ]
        errors.append(abs(d - min(ts)))
    assert len(errors) > 400
    assert float(np.median(errors)) < 0.01

    glass = sim_in(box_room(width=4.0, length=6.0, glass=True), cat, [car(cat)])
    _run(glass, 0.4)
    scan = glass.readings("ego").lidar
    assert scan is not None
    ahead = [
        d
        for a, d in zip(scan.angles_rad, scan.ranges_m, strict=True)
        if abs(math.remainder(a, 2 * math.pi)) < 0.5
    ]
    drop_rate = sum(d is None for d in ahead) / len(ahead)
    assert 0.4 < drop_rate < 0.9


def test_lidar_motion_distortion(cat: Catalogue) -> None:
    sim = sim_in(box_room(width=4.0, length=6.0), cat, [car(cat)])
    lidar = sim.cars["ego"].lidar
    assert lidar is not None
    assert lidar.period == pytest.approx(0.1)
    _run(sim, 0.2)
    scan = sim.readings("ego").lidar
    assert scan is not None and scan.t_end - scan.t_start == pytest.approx(0.1, abs=0.021)


def test_gyro_bias_and_rate(cat: Catalogue) -> None:
    sim = sim_in(box_room(), cat, [car(cat)])
    _run(sim, 2.0)
    still = sim.readings("ego").yaw_rate_rad_s
    assert still is not None and abs(still.value) <= math.radians(2)
    assert still.value == pytest.approx(
        math.radians(round(math.degrees(still.value)))
    )  # integer °/s
    _run(sim, 6.0, CarCommand(steering_rad=0.5, speed_m_s=0.3))
    r = sim.readings("ego").yaw_rate_rad_s
    assert r is not None
    assert r.value == pytest.approx(sim.truth("ego").yaw_rate_rad_s, abs=math.radians(3))


def test_progress_and_laps_on_loop(cat: Catalogue) -> None:
    track = generate_corridor(
        CorridorParams(
            seed=4,
            length_m=40,
            width_min_m=1.8,
            jog_rate_per_10m=0,
            niche_rate_per_10m=0,
            door_rate_per_10m=0,
            pillar_rate_per_10m=0,
            object_rate_per_10m=0,
        )
    ).track
    sim = sim_in(track, cat, [car(cat)])
    last = sim.progress("ego").distance_m
    for _ in range(round(400 / sim.control_dt)):
        sim.command("ego", centreline_follower(sim, "ego", 0.35))
        sim.step()
        d = sim.progress("ego").distance_m
        assert d >= last - 0.05  # monotonic (small noise allowed)
        last = d
        if sim.progress("ego").finished:
            break
    pr = sim.progress("ego")
    assert pr.finished and pr.laps == 3 and len(pr.lap_times_s) == 3
    assert all(t > 0 for t in pr.lap_times_s)


def test_collisions_and_stuck(cat: Catalogue) -> None:
    sim = sim_in(box_room(width=4.0, length=2.0), cat, [car(cat)])
    _run(sim, 6.0, CarCommand(speed_m_s=0.4))
    kinds = [e.kind for e in sim.events]
    assert "wall" in kinds and "stuck" in kinds
    a, b = car(cat), car(cat)
    room = box_room(width=4.0, length=6.0)
    two = sim_in(
        room, cat, [CarEntry("a", a.assembly, a.spec, 0), CarEntry("b", b.assembly, b.spec, 1)]
    )
    # b starts 1 m to the left of a; turn it to face a (-y) and drive straight into it.
    adr = two.model.jnt_qposadr[two.model.joint("b/root").id]
    two.data.qpos[adr + 3 : adr + 7] = [math.cos(-math.pi / 4), 0, 0, math.sin(-math.pi / 4)]
    for _ in range(round(6 / two.control_dt)):
        two.command("a", CarCommand())
        two.command("b", CarCommand(speed_m_s=0.3))
        two.step()
    assert any(e.kind == "car" and {e.car, e.other} == {"a", "b"} for e in two.events)


def test_record_mcap_roundtrip(cat: Catalogue, tmp_path: Path) -> None:
    sim = sim_in(box_room(), cat, [car(cat)])
    rec = Recorder(tmp_path / "run.mcap")
    steps = round(30 / sim.control_dt)
    for k in range(steps):
        cmd = CarCommand(speed_m_s=0.0)
        sim.command("ego", cmd)
        sim.step()
        rec.add(frame_from(k, sim.readings("ego"), cmd, "idle", 50.0, {"k": k}), sim.truth("ego"))
    blob = rec.close()
    frames = read_frames(tmp_path / "run.mcap")
    assert len(frames) == steps and frames[-1].channels["k"] == steps - 1
    assert blob.size_bytes == (tmp_path / "run.mcap").stat().st_size


@pytest.mark.skipif(os.environ.get("CI") == "true", reason="timing only on dev machines")
def test_real_time_factor(cat: Catalogue) -> None:
    track = generate_corridor(CorridorParams(seed=1, length_m=60)).track
    sim = sim_in(track, cat, [car(cat)])
    start = time.perf_counter()
    _run(sim, 10.0, CarCommand(speed_m_s=0.0))
    assert 10.0 / (time.perf_counter() - start) >= 10
