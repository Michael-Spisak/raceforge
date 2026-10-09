"""Shared setup for training and benchmarks (spec 0013): a quick-start car on a corridor."""

from dataclasses import dataclass
from functools import cache

from raceforge.construct.quickstart import QuickStartParams, generate, vehicle_spec
from raceforge.parts.catalogue import Catalogue
from raceforge.sim.engine import Simulation
from raceforge.sim.runner import SIM_SENSORS
from raceforge.sim.world import CarEntry, build_world
from raceforge.track.procedural import CorridorParams, GenerationError, generate_corridor
from raceforge.track.quick import QuickTrack, build_quick_track


@dataclass(frozen=True)
class TrackConfig:
    """Which corridor and race: procedural seed, length, laps and opponents (built-in drivers)."""

    seed: int = 0
    length_m: float = 25.0
    laps: int = 1
    opponents: int = 0
    loop: bool = True
    quick: QuickTrack | None = None  # a drawn track (spec 0014) instead of the procedural corridor


@cache
def _catalogue() -> Catalogue:
    return Catalogue.load()


def make_sim(cfg: TrackConfig) -> Simulation:
    """A fresh, deterministic simulation for ``cfg`` (the ego car is ``"ego"``)."""
    cat = _catalogue()
    car = generate(QuickStartParams(drive_gears="20-28", sensors=SIM_SENSORS), cat)
    spec = vehicle_spec(car, cat)
    if cfg.quick is not None:
        # Same track every run; the seed varies sensor noise and object placement.
        track = build_quick_track(cfg.quick).track
    else:
        track = generate_corridor(
            CorridorParams(seed=cfg.seed, loop=cfg.loop, length_m=cfg.length_m)
        ).track
    setup = track.race_setups[0]
    if setup.start_line == setup.finish_line and setup.laps != cfg.laps:
        track = track.model_copy(
            update={"race_setups": [setup.model_copy(update={"laps": cfg.laps})]}
        )
    entries = [CarEntry("ego", car.assembly, spec, 0)]
    entries += [
        CarEntry(f"opp{i + 1}", car.assembly, spec, i + 1, builtin_driver=True)
        for i in range(cfg.opponents)
    ]
    return Simulation(build_world(track, entries, cat, seed=cfg.seed), seed=cfg.seed)


@cache
def corridor_seeds(seed0: int, count: int, length_m: float, loop: bool = True) -> tuple[int, ...]:
    """The first ``count`` seeds from ``seed0`` that generate a valid corridor (some do not, e.g.
    short loops), so a benchmark never scores a track that does not exist."""
    out: list[int] = []
    seed = seed0
    while len(out) < count:
        try:
            generate_corridor(CorridorParams(seed=seed, loop=loop, length_m=length_m))
            out.append(seed)
        except GenerationError:
            pass
        seed += 1
        if seed - seed0 > 50 * count + 50:
            raise GenerationError(f"too few valid corridors of {length_m} m from seed {seed0}")
    return tuple(out)


def race_distance_m(sim: Simulation) -> float:
    """Centreline distance of the whole race (all laps on a loop, start → finish otherwise)."""
    return sim.length * sim.laps_target if sim.loop else sim.finish_s
