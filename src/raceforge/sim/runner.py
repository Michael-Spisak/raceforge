"""One simulated race from CLI-style options (spec 0004: `raceforge sim`)."""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from raceforge.construct.quickstart import QuickStartParams, SensorSpec, generate, vehicle_spec
from raceforge.control.controller import load_controller
from raceforge.core.io import content_hash, dump, load_as
from raceforge.core.primitives import BlobRef, VersionRef
from raceforge.core.track import Track
from raceforge.parts.catalogue import Catalogue, deterministic_object_id
from raceforge.sim.engine import Simulation
from raceforge.sim.record import make_runlog
from raceforge.sim.simio import RaceResult, run_race
from raceforge.sim.world import CarEntry, build_world
from raceforge.track.procedural import CorridorParams, generate_corridor

# The simulator car carries every sensor so all templates work; the real car may have fewer.
SIM_SENSORS = [
    SensorSpec(kind="lidar_2d", preset="top"),
    SensorSpec(kind="ev3_ultrasonic", preset="front"),
    SensorSpec(kind="ev3_ultrasonic", preset="left"),
    SensorSpec(kind="ev3_ultrasonic", preset="right"),
    SensorSpec(kind="ev3_gyro", preset="center"),
]


@dataclass(frozen=True)
class SimOptions:
    controller: Path
    params: Path | None = None
    track: str = "seed:0"
    loop: bool = True
    length_m: float = 40.0
    laps: int = 3
    opponents: int = 0
    seed: int = 0
    record: Path | None = None
    quickstart: Path | None = None
    max_time_s: float = 900.0


def _track(opts: SimOptions) -> Track:
    if opts.track.startswith("seed:"):
        track = generate_corridor(
            CorridorParams(seed=int(opts.track[5:]), loop=opts.loop, length_m=opts.length_m)
        ).track
    else:
        track = load_as(Track, Path(opts.track).read_text(encoding="utf-8"))
    setup = track.race_setups[0]
    if setup.start_line == setup.finish_line and setup.laps != opts.laps:
        track = track.model_copy(
            update={"race_setups": [setup.model_copy(update={"laps": opts.laps})]}
        )
    return track


def run_once(opts: SimOptions, out: Callable[[str], None]) -> bool:
    """Run one race; print a summary; return True if the car finished without wall contact."""
    cat = Catalogue.load()
    raw = yaml.safe_load(opts.quickstart.read_text(encoding="utf-8")) if opts.quickstart else {}
    params = QuickStartParams.model_validate(
        {"drive_gears": "20-28", "sensors": SIM_SENSORS, **(raw or {})}
    )
    car = generate(params, cat)
    spec = vehicle_spec(car, cat)
    track = _track(opts)
    entries = [CarEntry("ego", car.assembly, spec, 0)]
    entries += [
        CarEntry(f"opp{i + 1}", car.assembly, spec, i + 1, builtin_driver=True)
        for i in range(opts.opponents)
    ]
    sim = Simulation(build_world(track, entries, cat, seed=opts.seed), seed=opts.seed)
    controller = load_controller(opts.controller, opts.params)
    mcap = None
    if opts.record:
        opts.record.mkdir(parents=True, exist_ok=True)
        mcap = opts.record / "run.mcap"
    result: RaceResult = run_race(sim, controller, max_time_s=opts.max_time_s, record=mcap)

    pr = result.progress
    laps = ", ".join(f"{t:.1f} s" for t in pr.lap_times_s) or "-"
    out(
        f"{opts.controller.name}: {'FINISHED' if pr.finished else 'not finished'} · "
        f"laps {pr.laps} ({laps}) · "
        f"distance {pr.distance_m:.1f} m · sim time {result.sim_time_s:.0f} s"
    )
    kinds = [e.kind for e in result.events if e.car == "ego"]
    out(
        f"contacts: wall {kinds.count('wall')} · object {kinds.count('object')} · "
        f"car {kinds.count('car')} · "
        f"stuck {kinds.count('stuck')} · slowest step {result.max_step_ms:.2f} ms"
    )
    for problem in result.problems[:5]:
        out(f"problem: {problem}")
    if mcap is not None and opts.record is not None:
        digest = hashlib.sha256(mcap.read_bytes()).hexdigest()
        blob = BlobRef(
            sha256=digest, size_bytes=mcap.stat().st_size, media_type="application/x-mcap"
        )
        source = opts.controller.read_bytes()
        controller_ref = VersionRef(
            object_id=deterministic_object_id(f"controller:{opts.controller.name}"),
            semver="0.0.0",
            content_hash=hashlib.sha256(source).hexdigest(),
        )
        assembly_ref = VersionRef(
            object_id=deterministic_object_id("quickstart-car"),
            semver="0.0.0",
            content_hash=content_hash(car.assembly),
        )
        track_ref = VersionRef(
            object_id=deterministic_object_id("track"),
            semver="0.0.0",
            content_hash=content_hash(track),
        )
        runlog = make_runlog(
            assembly_ref,
            controller_ref,
            blob,
            track=track_ref,
            race_setup_id=track.race_setups[0].id,
        )
        (opts.record / "runlog.json").write_text(dump(runlog) + "\n", encoding="utf-8")
        out(f"recorded {opts.record / 'run.mcap'} (open in Foxglove Studio)")
    return pr.finished and kinds.count("wall") == 0
