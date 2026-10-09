"""Benchmark and tune controllers in the sim (spec 0013): the API the CLI and the Train tab use."""

from raceforge.train.benchmark import BenchConfig, BenchResult, RunResult, benchmark
from raceforge.train.env import EnvConfig
from raceforge.train.rl import TEMPLATE as ONNX_TEMPLATE
from raceforge.train.rl import RLCancelledError, RLConfig, RLProgress, RLResult, train_ppo
from raceforge.train.tune import Trial, TuneConfig, TuneResult, tune

__all__ = [
    "ONNX_TEMPLATE",
    "BenchConfig",
    "BenchResult",
    "EnvConfig",
    "RLCancelledError",
    "RLConfig",
    "RLProgress",
    "RLResult",
    "RunResult",
    "Trial",
    "TuneConfig",
    "TuneResult",
    "benchmark",
    "train_ppo",
    "tune",
]
