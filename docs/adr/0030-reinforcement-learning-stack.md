# ADR-0030: Reinforcement learning stack (optional extra `rl`)

- **Status:** accepted
- **Date:** 2026-10-09

## Context
Spec 0022 trains driving policies with PPO in `RaceForgeEnv` and runs them on the car. docs/PLAN.md §Tech stack
names Stable-Baselines3 + PyTorch for training and ONNX → onnxruntime for the car.

## Decision
Optional extra **`rl`** (`uv sync --extra rl`, `pip install raceforge[rl]`):
- **stable-baselines3** (MIT): PPO (SAC later), vectorised environments, callbacks.
- **torch** (BSD-3-Clause / Apache-2.0 parts): pulled in by SB3; CPU wheels are enough for the small MLPs.
- **onnx** (Apache-2.0) for the export and **onnxruntime** (MIT) to run the policy in benchmarks and on the car.

All compatible with GPL-3.0. Optional because torch is large; the training code imports it lazily and tests that
need it skip without the extra. The car only needs onnxruntime (aarch64 wheels exist).

## Consequences
Workers that run RL jobs need the extra installed. The policy travels inside the controller's params YAML (base64
ONNX), so the deploy bundle format (spec 0005) stays unchanged.
