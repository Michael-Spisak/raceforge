"""Evaluate a controller on several generated corridors (dev helper, not part of the package)."""

import sys
import time
from pathlib import Path

from raceforge.construct.quickstart import QuickStartParams, SensorSpec, generate, vehicle_spec
from raceforge.control.controller import load_controller
from raceforge.parts.catalogue import Catalogue
from raceforge.sim.engine import Simulation
from raceforge.sim.simio import run_race
from raceforge.sim.world import CarEntry, build_world
from raceforge.track.procedural import CorridorParams, generate_corridor

SENSORS = [
    SensorSpec(kind="lidar_2d", preset="top"),
    SensorSpec(kind="ev3_ultrasonic", preset="front"),
    SensorSpec(kind="ev3_ultrasonic", preset="left"),
    SensorSpec(kind="ev3_ultrasonic", preset="right"),
    SensorSpec(kind="ev3_gyro", preset="center"),
]


def main() -> None:
    path = Path(sys.argv[1])
    seeds = range(int(sys.argv[2]) if len(sys.argv) > 2 else 5)
    loop = (sys.argv[3] if len(sys.argv) > 3 else "loop") == "loop"
    length = float(sys.argv[4]) if len(sys.argv) > 4 else 40.0
    laps = int(sys.argv[5]) if len(sys.argv) > 5 else 1
    cat = Catalogue.load()
    res = generate(QuickStartParams(drive_gears="20-28", sensors=SENSORS), cat)
    spec = vehicle_spec(res, cat)
    ok = 0
    for seed in seeds:
        cor = generate_corridor(CorridorParams(seed=seed, loop=loop, length_m=length))
        track = cor.track
        if laps != track.race_setups[0].laps:
            setup = track.race_setups[0].model_copy(update={"laps": laps})
            track = track.model_copy(update={"race_setups": [setup]})
        sim = Simulation(
            build_world(track, [CarEntry("ego", res.assembly, spec, 0)], cat, seed=seed), seed=seed
        )
        t0 = time.perf_counter()
        r = run_race(sim, load_controller(path), max_time_s=laps * length / 0.15 + 30)
        walls = r.wall_contacts
        good = r.progress.finished and walls == 0
        ok += good
        kinds = [e.kind for e in r.events]
        print(
            f"seed {seed:3d}: finished={r.progress.finished} laps={r.progress.laps} "
            f"dist={r.progress.distance_m:6.1f} walls={walls:3d} objs={kinds.count('object')} "
            f"stuck={kinds.count('stuck')} t={r.sim_time_s:5.0f}s "
            f"wall={time.perf_counter() - t0:4.1f}s {'OK' if good else 'FAIL'} {r.problems[:1]}"
        )
    print(f"{ok}/{len(seeds)} clean")


if __name__ == "__main__":
    main()
