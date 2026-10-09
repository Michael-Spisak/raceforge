"""Benchmark and tune controllers in the sim (spec 0013): the API the CLI and the Train tab use."""

from raceforge.train.benchmark import BenchConfig, BenchResult, RunResult, benchmark
from raceforge.train.env import EnvConfig
from raceforge.train.imitation import (
    DEMO_STATES,
    BCConfig,
    BCProgress,
    BCResult,
    Recording,
    sim_robot_info,
    summarise,
    train_bc,
)
from raceforge.train.rl import TEMPLATE as ONNX_TEMPLATE
from raceforge.train.rl import RLCancelledError, RLConfig, RLProgress, RLResult, train_ppo
from raceforge.train.tune import Trial, TuneConfig, TuneResult, tune

__all__ = [
    "DEMO_STATES",
    "ONNX_TEMPLATE",
    "BCConfig",
    "BCProgress",
    "BCResult",
    "BenchConfig",
    "BenchResult",
    "EnvConfig",
    "RLCancelledError",
    "RLConfig",
    "RLProgress",
    "RLResult",
    "Recording",
    "RunResult",
    "Trial",
    "TuneConfig",
    "TuneResult",
    "benchmark",
    "sim_robot_info",
    "summarise",
    "train_bc",
    "train_ppo",
    "tune",
]
