"""Benchmark: score a controller on the same held-out corridors (spec 0013).

Score (lower is better): mean time to finish the race; a DNF counts as
``max_time_s + (1 - fraction of the race done) * max_time_s``, so getting further ranks higher.
"""

import os
from collections.abc import Callable, Mapping
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from raceforge.control.controller import load_controller, load_controller_class
from raceforge.sim.simio import run_race
from raceforge.track.procedural import CorridorParams
from raceforge.train.common import TrackConfig, corridor_seeds, make_sim, race_distance_m

HELD_OUT_SEED0 = 1000  # tuning uses seeds from 0; the benchmark corridors start here


@dataclass(frozen=True)
class BenchConfig:
    tracks: int = 5
    seed0: int = HELD_OUT_SEED0
    length_m: float = 25.0
    laps: int = 1
    opponents: int = 0
    max_time_s: float = 240.0
    workers: int = 0  # 0: CPU count - 1


@dataclass(frozen=True)
class RunResult:
    seed: int
    finished: bool
    time_s: float
    laps: int
    distance_m: float
    fraction: float  # of the race distance
    wall_contacts: int
    car_contacts: int
    error: str = ""

    def score(self, max_time_s: float) -> float:
        if self.finished:
            return self.time_s
        return max_time_s + (1.0 - self.fraction) * max_time_s


@dataclass(frozen=True)
class BenchResult:
    runs: list[RunResult]
    max_time_s: float
    score: float = field(init=False)

    def __post_init__(self) -> None:
        scores = [r.score(self.max_time_s) for r in self.runs]
        object.__setattr__(self, "score", sum(scores) / len(scores) if scores else float("inf"))

    @property
    def finished_rate(self) -> float:
        return sum(r.finished for r in self.runs) / len(self.runs) if self.runs else 0.0


def run_one(
    controller: str, params: Mapping[str, Any] | str | None, track: TrackConfig, max_time_s: float
) -> RunResult:
    """One race (runs in a worker process). ``params``: values, a YAML path, or None (defaults)."""
    try:
        path = Path(controller)
        if isinstance(params, Mapping):
            cls = load_controller_class(path)
            ctrl = cls(cls.Params.model_validate(dict(params)))
        else:
            ctrl = load_controller(path, Path(params) if params else None)
        sim = make_sim(track)
        result = run_race(sim, ctrl, max_time_s=max_time_s)
    except Exception as e:  # a controller that cannot load or crashes is a DNF, not a crash
        return RunResult(
            track.seed, False, max_time_s, 0, 0.0, 0.0, 0, 0, f"{type(e).__name__}: {e}"
        )
    pr = result.progress
    total = race_distance_m(sim)
    ego = [e for e in result.events if e.car == "ego"]
    return RunResult(
        seed=track.seed,
        finished=pr.finished,
        time_s=result.sim_time_s,
        laps=pr.laps,
        distance_m=pr.distance_m,
        fraction=max(0.0, min(1.0, pr.distance_m / total)) if total > 0 else 0.0,
        wall_contacts=sum(1 for e in ego if e.kind in ("wall", "object")),
        car_contacts=sum(1 for e in ego if e.kind == "car"),
        error="; ".join(result.problems[:1]),
    )


def _workers(n: int, jobs: int) -> int:
    return max(1, min(jobs, n or max(1, (os.cpu_count() or 2) - 1)))


def benchmark(
    controller: Path | str,
    params: Mapping[str, Any] | Path | str | None = None,
    cfg: BenchConfig | None = None,
    progress: Callable[[RunResult], None] | None = None,
) -> BenchResult:
    cfg = cfg or BenchConfig()
    CorridorParams(length_m=cfg.length_m)  # bad settings fail here, not as silent DNFs
    tracks = [
        TrackConfig(seed=seed, length_m=cfg.length_m, laps=cfg.laps, opponents=cfg.opponents)
        for seed in corridor_seeds(cfg.seed0, cfg.tracks, cfg.length_m)
    ]
    p: Mapping[str, Any] | str | None = str(params) if isinstance(params, Path) else params
    workers = _workers(cfg.workers, len(tracks))
    runs: list[RunResult] = []
    if workers == 1:
        for t in tracks:
            runs.append(run_one(str(controller), p, t, cfg.max_time_s))
            if progress:
                progress(runs[-1])
    else:
        with ProcessPoolExecutor(workers) as pool:
            futures = [pool.submit(run_one, str(controller), p, t, cfg.max_time_s) for t in tracks]
            for fut in futures:
                runs.append(fut.result())
                if progress:
                    progress(runs[-1])
    return BenchResult(runs=runs, max_time_s=cfg.max_time_s)
