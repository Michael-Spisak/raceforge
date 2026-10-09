"""Spec 0029: map (AC1), racing line (AC2), particle filter (AC3), template lap (AC4)."""

import itertools
import math
from pathlib import Path

import numpy as np
import yaml

from raceforge.control.localisation import (
    DistanceMap,
    ParticleFilter,
    decode_field,
    encode_field,
)
from raceforge.track.localisation import build_map
from raceforge.track.procedural import Corridor
from raceforge.track.quick import QuickTrack, build_quick_track
from raceforge.track.racing_line import LineConfig, curvature, racing_line

RECT = QuickTrack(points=[(0, 0), (10, 0), (10, 5), (0, 5)], width_m=1.6, laps=1)


def _corridor() -> Corridor:
    return build_quick_track(RECT)


def test_map_distances_drivable_and_round_trip() -> None:
    cor = _corridor()
    world = build_map(cor)
    wall = cor.track.walls[0].points[3]
    assert world.distance(np.array([wall.x]), np.array([wall.y]))[0] < 0.05
    c = cor.centreline[len(cor.centreline) // 8]
    d = world.distance(np.array([c[0]]), np.array([c[1]]))[0]
    assert abs(d - 0.8) < 0.08  # half the corridor width
    assert world.drivable(np.array([c[0]]), np.array([c[1]]))[0]
    assert not world.drivable(np.array([5.0]), np.array([2.5]))  # the infield of the rectangle
    back = decode_field(encode_field(world.cells), world.width, world.height)
    assert np.array_equal(back, world.cells)


def test_racing_line_inside_and_smoother_than_the_centreline() -> None:
    cor = _corridor()
    cfg = LineConfig(margin_m=0.2)
    line = racing_line(cor, cfg)
    widths = np.asarray(cor.widths)[: len(line.offset)]
    assert (np.abs(line.offset) <= widths / 2 - cfg.margin_m + 1e-6).all()
    centre = np.asarray(cor.centreline)[: len(line.xy)]
    assert np.abs(line.curvature).sum() < np.abs(curvature(centre, line.loop)).sum()
    lat = line.speed**2 * np.abs(line.curvature)
    assert (lat <= cfg.lateral_accel_m_s2 + 1e-6).all()
    corner = int(np.argmax(np.abs(line.curvature)))
    assert line.speed[corner] < line.speed.max()


def _cast(world: DistanceMap, x: float, y: float, a: float, max_r: float = 8.0) -> float | None:
    """Sphere tracing on the distance field: the range a LiDAR at (x, y) measures at angle a."""
    r = 0.0
    while r < max_r:
        px, py = np.array([x + r * math.cos(a)]), np.array([y + r * math.sin(a)])
        d = float(world.distance(px, py)[0])
        if d < 0.02:
            return r
        r += max(d * 0.9, 0.01)
    return None


def _scan(
    world: DistanceMap, x: float, y: float, yaw: float
) -> tuple[tuple[float, ...], tuple[float | None, ...]]:
    angles = tuple(math.radians(a) for a in range(0, 360, 6))
    return angles, tuple(_cast(world, x, y, yaw + a) for a in angles)


def test_particle_filter_tracks_and_recovers_from_a_kidnap() -> None:
    cor = _corridor()
    world = build_map(cor)
    path = np.asarray(cor.centreline)[:40]
    pf = ParticleFilter(world, particles=300, beams=30, seed=1)
    x0, y0 = path[0]
    yaw0 = math.atan2(path[1][1] - y0, path[1][0] - x0)
    pf.init_around(float(x0), float(y0), yaw0)
    dt = 0.1
    for (xa, ya), (xb, yb) in itertools.pairwise(path):
        yaw = math.atan2(yb - ya, xb - xa)
        speed = math.hypot(xb - xa, yb - ya) / dt
        pf.predict(speed, 0.0, dt)  # no gyro here: the scan update corrects the heading
        pf.update(*_scan(world, float(xb), float(yb), yaw))
    est = pf.estimate()
    xb, yb = path[-1]
    assert math.hypot(est.x - xb, est.y - yb) < 0.10
    # kidnap: the car is somewhere else; global re-initialisation finds it
    kx, ky = np.asarray(cor.centreline)[len(cor.centreline) // 2]
    kn = np.asarray(cor.centreline)[len(cor.centreline) // 2 + 1]
    kyaw = math.atan2(kn[1] - ky, kn[0] - kx)
    pf.init_global()
    for _ in range(50):
        pf.predict(0.0, 0.0, dt)
        pf.update(*_scan(world, float(kx), float(ky), kyaw))
    est = pf.estimate()
    assert math.hypot(est.x - kx, est.y - ky) < 0.3


def test_localised_template_finishes_a_lap(tmp_path: Path) -> None:
    from raceforge.api.localisation import localisation_params
    from raceforge.api.service import TEMPLATES_DIR
    from raceforge.api.tracks import QuickTracks
    from raceforge.train.benchmark import BenchConfig, benchmark

    quick = QuickTracks(tmp_path / "tracks")
    quick.save("rect", RECT)
    params = tmp_path / "rect.localised.yaml"
    params.write_text(yaml.safe_dump(localisation_params("rect", quick)), encoding="utf-8")
    res = benchmark(
        TEMPLATES_DIR / "localised.py",
        params,
        BenchConfig(tracks=1, max_time_s=180, workers=1, quick=RECT),
    )
    assert res.runs[0].finished, res.runs[0]
