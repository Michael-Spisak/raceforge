# Spec: Workers v1 — run training and benchmarks on team computers

- **Status:** approved by the owner 2026-10-09 (backend security gate: "Ja, so umsetzen" — dedicated `worker`
  token that can only fetch jobs and report results)
- **Owner:** Michael Spisak
- **Plan section:** docs/PLAN.md §9 (backend, compute workers), milestone weeks 4–5 ("Workers v1")
- **Depends on:** Spec 0006 (backend, tokens), Spec 0013 (benchmark, tuning)

## Purpose
Heavy jobs (benchmarks, Optuna tuning, later RL) should run on whichever team computer is free, not only on the
laptop of the person who starts them.

## Security (owner gate)
- New scope `worker`. Worker tokens (`rfw_…`, client `worker`) are created **only** by registering a worker
  (`POST /workers`, scope `sim_train`) and carry **only** the `worker` scope: every other endpoint answers 403.
  No API token can request the `worker` scope or client (422); login/access tokens never have it.
- A worker token is bound to one worker row and one workspace; it can claim jobs of that workspace only and touch
  only the job it is running. Removing a worker revokes its token (401 afterwards) and re-queues its running job.
- Inputs (controller source ≤ 512 KB, params YAML) and results travel inline in the job; workers get no access to
  objects or blobs. The token is stored on the worker in `worker.json` next to the workspace cache, mode 0600.
- Registering, removing workers and creating/cancelling jobs are audited.

## Interfaces (additive; backend contract snapshot)
- Members: `POST /workers {workspace_id, name}` → `WorkerRegistration {worker, token}`;
  `GET /workspaces/{id}/workers`, `DELETE /workers/{id}`; `POST|GET /workspaces/{id}/jobs`, `GET /jobs/{id}`,
  `POST /jobs/{id}/cancel`.
- Worker token: `POST /worker/heartbeat {info}`, `POST /worker/claim` → `WorkerJob` or 204,
  `POST /worker/jobs/{id}/progress {progress, log}` → `{cancel}`, `POST /worker/jobs/{id}/finish {status, result,
  error}`.
- DB: tables `workers`, `jobs` (migration 0002). Job status: queued → running → done | error | cancelled.
- CLI: `raceforge worker register [--name] | run [--idle-only] [--once] | status | remove`.

## Behaviour
- One job per worker at a time; the oldest queued job is claimed first (PostgreSQL: `FOR UPDATE SKIP LOCKED`).
- Cancel: a queued job is cancelled at once; a running job stops after the current race/trial.
- Workers are "online" when seen within 2 minutes (heartbeat every 30 s). `--idle-only` waits while the 1-minute
  load is above half the cores.
- The worker runs the same `raceforge.train` code as the Train tab (spec 0013) in a temporary folder.

## Acceptance criteria (→ tests)
- [ ] AC1: A worker token gets 403 on workspaces/jobs/tokens; a member token gets 403 on `/worker/claim`; an API
  token with scope/client `worker` is refused (422).
- [ ] AC2: Queue → claim (source delivered) → second claim 409 → progress/log → member cancel → ack `cancel` →
  finish cancelled; a second job finishes with its result; the worker is listed online; removing it revokes the
  token (401).
- [ ] AC3: `run_worker(once)` runs a real benchmark job from the queue and reports `done` with a result.
- Part B (separate PR): Train tab "run on a team worker", worker and job lists.
