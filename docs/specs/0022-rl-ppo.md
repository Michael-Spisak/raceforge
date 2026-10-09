# Spec: Reinforcement learning v1 — PPO policy, ONNX on the car

- **Status:** approved (owner request 2026-10-09: "arbeite weiter die Features ab")
- **Owner:** Michael Spisak
- **Plan section:** docs/PLAN.md §4 (Train: RL, ONNX export), milestone weeks 8–9
- **Related ADRs:** ADR-0030 (stable-baselines3, torch, onnx, onnxruntime as optional extra `rl`)
- **Depends on:** Spec 0013 (RaceForgeEnv, benchmark), Spec 0020 (workers)

## Purpose
Learn a driving policy in the simulator and run exactly that policy on the real car, compared on the same
benchmark as the classic controllers.

## Scope
- In scope: `raceforge.control.policy_io` (observation vector + action mapping shared by the training env and the
  car controller), `RaceForgeEnv` using it, `raceforge.train.rl.train_ppo` (PPO, vectorised envs, progress,
  cancel), ONNX export of the deterministic actor, `controllers/templates/onnx_policy.py` (policy as base64 ONNX in
  its params YAML → the deploy bundle format stays unchanged), `raceforge train rl`, engine job `POST
  /api/v1/train/rl`, worker job kind `rl`, Train tab mode "Train a policy (RL, PPO)".
- Out of scope (later): SAC in parallel, residual RL, camera/localisation inputs, LSTM, domain randomisation
  presets, imitation learning, GPU (MJX) tier, early stopping on the benchmark.

## Behaviour
- Observation (41) and action (2) as documented in `policy_io`; steering rate limit 3 rad/s (LEGO gears).
- Training corridors: procedural seeds from 0 (or a drawn track with varied sim seeds); evaluation: the held-out
  benchmark (seeds from 1000) through the `onnx_policy` controller, i.e. the same path as on the car.
- Default output in the engine: `<engine data>/policies/ppo-<time>.yaml`.

## Acceptance criteria (→ tests; skipped without the `rl` extra)
- [ ] AC1: The exported ONNX actor gives the same actions as `PPO.predict(deterministic=True)` (≤ 1e-5).
- [ ] AC2: `train_ppo` writes a params YAML that `onnx_policy.py` loads and that drives in a benchmark without errors.
- [ ] AC3: The engine RL job runs to `done`, reports steps and writes the policy into the policies folder.
