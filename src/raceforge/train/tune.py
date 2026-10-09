"""Classic tuning (spec 0013): Optuna TPE over a controller's `Tunable` parameters.

The objective is the benchmark score on training corridors (seeds from 0); the best parameters
are then scored on the held-out corridors and written as a params YAML for `raceforge bundle`.
"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import optuna
import yaml

from raceforge.control.controller import load_controller_class
from raceforge.train.benchmark import BenchConfig, BenchResult, benchmark


@dataclass(frozen=True)
class TuneConfig:
    trials: int = 30
    timeout_s: float | None = None
    train_tracks: int = 3
    bench: BenchConfig = field(default_factory=BenchConfig)  # held-out validation + race settings
    seed: int = 0
    out: Path | None = None  # params YAML


@dataclass(frozen=True)
class Trial:
    number: int
    params: dict[str, Any]
    score: float


@dataclass(frozen=True)
class TuneResult:
    best_params: dict[str, Any]
    train_score: float
    default_train_score: float
    validation: BenchResult
    default_validation: BenchResult  # the defaults on the same held-out corridors
    trials: list[Trial]
    out: Path | None


def _rounded(value: float, low: float, step: float | None) -> float:
    if step is None:
        return round(value, 6)
    return round(low + round((value - low) / step) * step, 6)


def tune(
    controller: Path,
    cfg: TuneConfig | None = None,
    progress: Callable[[Trial, Trial], None] | None = None,
) -> TuneResult:
    """``progress(trial, best)`` runs after every trial."""
    cfg = cfg or TuneConfig()
    cls = load_controller_class(controller)
    space = cls.Params.tunables()
    if not space:
        raise ValueError(f"{controller.name}: no Tunable parameters to tune")
    defaults = cls.Params().model_dump()
    train = replace(cfg.bench, seed0=0, tracks=cfg.train_tracks)

    def score(values: dict[str, Any]) -> float:
        return benchmark(controller, {**defaults, **values}, train).score

    default_score = score({})
    trials: list[Trial] = [Trial(-1, {}, default_score)]
    best = trials[0]

    def objective(t: optuna.Trial) -> float:
        nonlocal best
        values = {
            name: _rounded(t.suggest_float(name, low, high, step=step), low, step)
            for name, (low, high, step) in space.items()
        }
        trial = Trial(t.number, values, score(values))
        trials.append(trial)
        if trial.score < best.score:
            best = trial
        if progress:
            progress(trial, best)
        return trial.score

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        direction="minimize", sampler=optuna.samplers.TPESampler(seed=cfg.seed)
    )
    study.optimize(objective, n_trials=cfg.trials, timeout=cfg.timeout_s)

    best_params = {**defaults, **best.params}
    tuned = cls.Params.model_validate(best_params)  # range check before writing
    validation = benchmark(controller, tuned.model_dump(), cfg.bench)
    default_validation = benchmark(controller, defaults, cfg.bench)
    if cfg.out is not None:
        cfg.out.parent.mkdir(parents=True, exist_ok=True)
        header = (
            f"# {controller.name} tuned by raceforge train tune (spec 0013): train score "
            f"{best.score:.1f} (defaults {default_score:.1f}), held-out score "
            f"{validation.score:.1f} (defaults {default_validation.score:.1f})\n"
        )
        cfg.out.write_text(
            header + yaml.safe_dump(tuned.model_dump(mode="json"), sort_keys=False),
            encoding="utf-8",
        )
    return TuneResult(
        best_params=tuned.model_dump(),
        train_score=best.score,
        default_train_score=default_score,
        validation=validation,
        default_validation=default_validation,
        trials=trials,
        out=cfg.out,
    )
