# Spec: Imitation learning v1 — behaviour cloning from recorded drives

- **Status:** approved (owner request 2026-10-09: "Imitation learning als nächstes")
- **Owner:** Michael Spisak
- **Plan section:** docs/PLAN.md §4 (Imitation: demonstrations from sim, tuned controller or real drives; BC + DAgger)
- **Related ADRs:** ADR-0030 (torch/onnx in the optional extra `rl`)
- **Depends on:** Spec 0010 (teleop, demonstrations recorded with `state = teleop`), Spec 0022 (policy IO, ONNX
  controller)

## Purpose
The team drives the car well by hand. Behaviour cloning turns recorded teleop drives (simulator or real car) into a
policy that drives like them, deployable with the same `onnx_policy` controller as RL policies.

## Scope
- In scope: `raceforge.train.imitation` (recording summary, (observation, action) samples built with
  `policy_io`, MLP training with a time-split validation and best-epoch selection, ONNX export, params YAML,
  held-out benchmark), `raceforge train bc FILES…`, engine `GET /api/v1/train/recordings` and
  `POST /api/v1/train/bc` (job kind `bc`), Train tab mode "Learn from my driving", recordings of the Simulate tab
  saved in the engine's `runs/` folder (relative record paths).
- Out of scope (later): DAgger (needs a human in the loop during policy runs), BC on team workers (recordings would
  have to be uploaded), real-car logs listing in the app (use the CLI with the file), data augmentation.

## Behaviour
- Samples: frames whose `state` is `teleop` (or every frame with `all_states`); action label = driven command
  normalised like a policy action (inverse of `action_to_command`); previous label as "last action".
- Normalisation uses the simulator car's limits (as RL); a real car with other limits scales the same way.
- Fewer than 20 demonstration frames → clear error.

## Acceptance criteria (→ tests; skipped without the extra)
- [ ] AC1: Samples have shape N×41 / N×2 with actions in [-1, 1]; a controller run has no teleop frames.
- [ ] AC2: Training on recorded drives writes a params YAML that `onnx_policy.py` loads and that benchmarks
  without errors; teleop-only on non-teleop recordings fails with a clear message.
- [ ] AC3: The engine lists recordings (frames, teleop frames) and runs a `bc` job to `done` with a policy file.

## Result of a first check
Cloning the centering controller from 4 recordings (10.6k frames, 40 epochs, 16 s) finished all 3 held-out
corridors (score 71; the original scores about 61–65).
