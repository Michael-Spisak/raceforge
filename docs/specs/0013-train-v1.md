# Spec: Training v1 — RaceForgeEnv, benchmark, classic tuning

- **Status:** approved (owner request 2026-10-09: "continue working off the list of features")
- **Owner:** Michael Spisak
- **Plan section:** docs/PLAN.md §4 (Train), milestone weeks 4–5 ("Gym env, Optuna tuning")
- **Related ADRs:** ADR-0028 (gymnasium, optuna)
- **Depends on:** Spec 0003 (sim), Spec 0004 (controller SDK, `Tunable`)

## Purpose
Give the car team numbers instead of guesses: a **benchmark** that scores any controller on the same held-out
corridors, **Optuna tuning** of the classic controllers' `Tunable` parameters (output: a params YAML that works on
the car), and the **Gymnasium environment** that the RL/imitation specs build on.

## Scope
- In scope: `raceforge.train.env.RaceForgeEnv`, `raceforge.train.benchmark`, `raceforge.train.tune`,
  CLI `raceforge train benchmark | tune`, engine endpoints + a Train tab (separate PR, same spec).
- Out of scope (later specs): PPO/SAC/imitation/offline RL, ONNX export, workers, camera/localisation
  observations, domain-randomisation presets, ghost opponents, leaderboard.

## Interfaces
- `RaceForgeEnv(EnvConfig)` (gymnasium.Env):
  - **Observation** `Box(float32, 41)`: 36 LiDAR sectors of 10° (min distance, clipped to `lidar_max_m`,
    divided by it; no return = 1), speed / top speed, steering / max steer, yaw rate / π, last action (2).
  - **Action** `Box([-1,-1],[1,1])`: steering (× max steer, + = left) and speed (≥ 0: × top speed; < 0:
    × top speed × `reverse_factor`). A **steering-rate limit** (`max_steer_rate_rad_s`) is applied before the sim.
  - **Reward** per step: `w_progress · Δcentreline progress (m)` − `w_wall · new wall/object contacts` −
    `w_car · new car contacts` − `w_smooth · |Δaction|²` + `w_lap` per finished lap + `w_finish` on finishing;
    a stuck event gives `−w_stuck` and ends the episode (truncated).
  - `terminated` when the race is finished; `truncated` at `max_time_s` or when stuck.
  - Track per episode: procedural corridor from `seeds[episode % len(seeds)]` (reset `seed` picks it too).
- `benchmark(controller, params, BenchConfig) -> BenchResult`: runs the controller on `tracks` corridors
  (seeds from `seed0`, default **1000+** = held out from tuning) with `laps` laps, in parallel processes.
  Per run: finished, time, laps, distance, contacts. **Score** (lower is better): mean of the time to finish;
  a DNF counts as `max_time_s + (1 − fraction of the race distance done) · max_time_s`.
- `tune(controller, TuneConfig, progress) -> TuneResult`: Optuna TPE over every `Tunable` field (step respected),
  objective = benchmark score on the **training** seeds (default 0…); best params validated on the held-out
  benchmark; writes `<out>.yaml` (params) and returns the trial history.
- CLI: `raceforge train benchmark CONTROLLER [--params P] [--tracks N] [--laps N] [--length M] [--opponents N]`,
  `raceforge train tune CONTROLLER [--trials N] [--timeout S] [--tracks N] [--out FILE]`.

## Train tab (engine jobs)
- `POST /api/v1/train/benchmark` (`TrainBenchRequest`) and `POST /api/v1/train/tune` (`TrainTuneRequest`) start a
  job and return `TrainJob`; **one job at a time** (409 otherwise); a controller that does not load → 422.
- `GET /api/v1/train/jobs`, `GET /api/v1/train/jobs/{id}`, `POST /api/v1/train/jobs/{id}/cancel` (takes effect
  after the current race/trial). The UI polls every second while a job runs; the last 20 jobs are kept in memory.
- Tune results show the held-out score of the tuned params next to the defaults and warn on overfitting.

## Behaviour
- Deterministic for given seeds (sim is deterministic per seed); Optuna sampler seeded.
- Parallel runs use a process pool (`workers`, default CPU count − 1); a controller that raises counts as DNF.
- The tuned YAML only contains `Tunable` fields and passes `Params.from_yaml` (so `raceforge bundle --params` works).

## Acceptance criteria (→ tests, critical paths only)
- [ ] AC1: `RaceForgeEnv` passes `gymnasium.utils.env_checker.check_env`; driving straight ahead earns
  positive progress reward; same seed → same observations.
- [ ] AC2: `benchmark` of the centering template on 2 short corridors finishes them and scores lower (better)
  than a stand-still controller (DNF).
- [ ] AC3: `tune` with a few trials writes a params YAML that loads with the controller's `Params` and whose
  training score is ≤ the default params' score.
