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
- [ ] AC4 (part B): The engine queues a tune job (controller sent inline, drawn tracks embedded), a worker runs it,
  the engine lists it as done and saves the tuned parameters to a YAML file; the worker list shows it.

## Part B — engine and Train tab
- Engine: `GET /api/v1/workspace/workers`, `GET|POST /api/v1/workspace/jobs` (`TeamJobRequest {bench|tune}`),
  `POST /api/v1/workspace/jobs/{id}/cancel`, `POST /api/v1/workspace/jobs/{id}/save-params {path}`.
- Train tab (when logged in): "Run on: this computer / team workers"; team workers (online/busy) and team jobs
  (status, progress, worker, score, cancel, save tuned parameters next to the controller).

## Part C — availability policy, leases, targeting (owner decisions 2026-10-09, Daniel Hodeib)
Brings v1 up to the plan's "Workers v1" milestone (§9: idle-only policy, launch dialog local/specific/auto) while
keeping the security model above (worker tokens, inline inputs). Owner choices: queue stays in PostgreSQL (no
Redis), workers only take jobs of their own RaceForge version (uv envs per version: later spec), the full
availability policy, and a worker switch in the desktop app in addition to the CLI.

**Queue (backend, additive contract; migration 0003)**
- `JobCreate` gets `priority: normal | high | critical` (`critical` admin only, else 403), `target_worker_id`
  (a worker of the same workspace, else 422; only that worker may claim it) and `raceforge_version` (the engine
  sends its own; a worker whose reported `raceforge` version differs never claims the job).
- Claim order: `critical` > `high` > `normal`, then oldest first.
- **Leases:** a running job whose worker was not seen for 2 minutes goes back to `queued` with `attempt + 1` and
  keeps its partial result; the 3rd loss ends it as `error` ("worker lost 3 times"). For 2 minutes after such a
  loss the lost worker does not get the job back, so another worker is preferred. Checked lazily on claim and on
  job/worker reads (the backend has no background threads).
- **Partial results:** `JobProgress.partial` (finished runs or trials so far) is stored with the job;
  `JobFinish {status: "paused", result: partial}` puts the job back to `queued` without counting an attempt.
  `WorkerJob.resume` hands the partial result to the next worker.
- `JobInfo` adds `priority`, `target_worker_id`, `raceforge_version`, `attempt`.

**Worker (agent + CLI)**
- `WorkerPolicy {mode: always | idle | schedule | paused, idle_minutes (default 10), schedule: [{days, start,
  end}], processes}`; default `idle`. Stored in `worker.json`; `raceforge worker run --mode M --idle-minutes N`
  overrides it (`--idle-only` = `--mode idle`). The heartbeat reports `available` and `reason`.
- Idle = no keyboard/mouse input for `idle_minutes` **and** on AC power (no battery = AC); stdlib only: macOS
  `ioreg`/`pmset`, Windows `GetLastInputInfo`/`GetSystemPowerStatus` (ctypes), Linux logind `IdleSinceHint` and
  `/sys/class/power_supply`. Unknown idle time → unavailable, reason `idle_unknown`.
- The worker heartbeats every 30 s **also while a job runs**. When it becomes unavailable during a job, the job
  stops after the current race/trial and is reported `paused`. A job the backend took away (404) is dropped and
  the worker keeps serving.
- Resume: a benchmark skips the corridors already raced; a tune adds the finished trials to a new study and runs
  the remaining number. A paused-and-resumed benchmark gives the same runs as an uninterrupted one.

**Engine and app**
- `TeamJobRequest` gets `target_worker_id` and `priority`. `GET|PUT /api/v1/workspace/worker/local` shows and
  sets this computer's worker (`enabled`, `policy`): enabling registers it in the current workspace if needed
  and runs the worker loop in the engine; disabling stops it after the current race/trial (the job is paused).
- Train tab: "Run on" lists this computer, any team worker, and each worker by name; priority select; the worker
  list shows availability and the reason. Team tab: "Use this computer as a worker" with mode, idle minutes and
  one schedule window.

**Acceptance criteria (part C)**
- [ ] AC5: priority order, target and version filters; `critical` by a member → 403; unknown target → 422.
- [ ] AC6: lease loss requeues with attempt 2 and the partial result as `resume`; the lost worker is skipped
  for 2 minutes; the 3rd loss → `error`. `paused` requeues without counting an attempt.
- [ ] AC7: policy decisions for every mode with fake probes; a schedule window across midnight; unknown idle time.
- [ ] AC8: a benchmark paused after the first race and resumed by another worker ends with the same runs as an
  uninterrupted benchmark; a tune resumed after 2 trials ends with `trials` trials.
- [ ] AC9: the engine's local worker switch registers, runs a queued job and stops.
- [ ] AC10 (manual, owner): Mac + Windows, worker switched on in the app in `idle` mode: the job waits while you
  type, starts after the idle time, pauses when you use the computer.
