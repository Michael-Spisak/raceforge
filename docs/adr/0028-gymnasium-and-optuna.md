# ADR-0028: Gymnasium environment and Optuna tuning

- **Status:** accepted
- **Date:** 2026-10-09

## Context
Spec 0013 (training v1) needs a standard RL environment interface for `RaceForgeEnv` (PPO/SAC and imitation
learning later use it through Stable-Baselines3 / `imitation`) and a black-box optimiser for the classic
controllers' `Tunable` parameters (docs/PLAN.md §4 "Classic tuning: Optuna").

## Decision
- **gymnasium** (MIT): the maintained successor of OpenAI Gym, required by Stable-Baselines3 ≥ 2.
  Pulls in cloudpickle (BSD-3) and farama-notifications (MIT).
- **optuna** (MIT): TPE sampler, pruning, in-memory or SQLite studies. Pulls in alembic (MIT), Mako (MIT),
  colorlog (MIT), tqdm (MPL-2.0 AND MIT). SQLAlchemy is already a dependency.

All licences are compatible with GPL-3.0. Alternatives: own random search (no TPE, no pruning), scikit-optimize
(unmaintained), Nevergrad (larger, no pruning).

## Consequences
Small pure-Python additions to the engine environment. PyTorch/SB3 are not added yet (spec for RL follows).
