"""Spec 0013: RaceForgeEnv (AC1), benchmark (AC2), classic tuning (AC3)."""

from pathlib import Path

import numpy as np
from gymnasium.utils.env_checker import check_env

from raceforge.api.service import TEMPLATES_DIR
from raceforge.control.controller import load_controller_class
from raceforge.train.benchmark import BenchConfig, benchmark
from raceforge.train.env import EnvConfig, RaceForgeEnv
from raceforge.train.tune import TuneConfig, tune

CENTERING = TEMPLATES_DIR / "centering.py"
SHORT = BenchConfig(tracks=2, length_m=20.0, max_time_s=120.0, workers=2)


def test_env_contract_progress_and_determinism() -> None:
    env = RaceForgeEnv(EnvConfig(seeds=(0,), length_m=20.0))
    check_env(env, skip_render_check=True)
    first, _ = env.reset(seed=0)
    total = 0.0
    for _ in range(100):
        obs, reward, terminated, truncated, _info = env.step(np.array([0.0, 0.8], np.float32))
        assert env.observation_space.contains(obs)
        total += reward
        assert not (terminated or truncated)
    assert total > 0  # driving forward earns progress
    again, _ = RaceForgeEnv(EnvConfig(seeds=(0,), length_m=20.0)).reset(seed=0)
    np.testing.assert_array_equal(first, again)


def test_benchmark_ranks_a_driving_controller_above_standing_still(tmp_path: Path) -> None:
    stand = tmp_path / "stand.py"
    stand.write_text(
        "from raceforge.control.controller import Controller\n"
        "from raceforge.control.params import ControllerParams\n"
        "from raceforge.control.types import Command\n\n\n"
        "class Stand(Controller[ControllerParams]):\n"
        "    def step(self, obs):\n"
        "        return Command()\n"
    )
    good = benchmark(CENTERING, None, SHORT)
    still = benchmark(stand, None, SHORT)
    assert good.finished_rate > 0 and still.finished_rate == 0
    assert good.score < still.score


def test_tune_writes_loadable_params_not_worse_on_training(tmp_path: Path) -> None:
    out = tmp_path / "centering.tuned.yaml"
    res = tune(CENTERING, TuneConfig(trials=2, train_tracks=1, bench=SHORT, out=out))
    assert res.train_score <= res.default_train_score
    params = load_controller_class(CENTERING).Params.from_yaml(out)
    assert params.model_dump() == res.best_params
