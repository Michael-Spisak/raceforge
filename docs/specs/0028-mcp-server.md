# Spec 0028: MCP server v1 — parts, construction, simulation, training, tracks, run logs

- **Status:** approved (owner decisions 2026-10-09, Daniel Hodeib: tool groups parts + construction,
  sim + training, tracks + logs; transports stdio + local HTTP)
- **Owner:** Daniel Hodeib
- **Plan section:** docs/PLAN.md §8 (MCP server), milestone weeks 10–11
- **Related ADRs:** ADR-0031 (MCP Python SDK)
- **Depends on:** Specs 0002, 0013, 0014/0025, 0015/0016, 0020, 0022, 0027 (`raceforge.api` services)

## Purpose
Let AI assistants use RaceForge like a teammate: look up parts, build or change a car from the quick-start,
check it against the rules and budget, simulate and train controllers, and analyse test drives — through the
same `raceforge.api` services the desktop app uses, so every feature exists once.

## Scope
- In scope: `raceforge mcp` (stdio) and `raceforge mcp --http [--port 8765]` (streamable HTTP on 127.0.0.1),
  `--read-only` (only tools that change nothing), the tools below, a call log, config snippets in the README.
- Out of scope (later specs): remote MCP through the backend (tokens with scopes), live tools
  (`live_status`, `live_snapshot`), versions/diff/tag tools, scan/capture tools, controller PRs, images
  (`render_assembly`), resources and prompts, overnight AI experiments.

## Tools (contract — human-owned)
Read-only tools are marked (R); the others are left out with `--read-only`.

**Parts and construction.** Assemblies are edited as named **drafts** (`~/.cache/raceforge/mcp/drafts/<name>.json`,
assembly + quick-start params); `save_assembly` turns a draft into a workspace version.
- `search_parts(query, category="", limit=20)` (R) → key, name, category, mass, LEGO/printed, verified.
- `get_part(key)` (R) → part summary and its connectors.
- `quickstart_options()` (R) → default parameters and allowed values.
- `apply_quickstart(draft, params={})` → new draft from the parametric quick-start; returns its summary.
- `list_assemblies()` (R) → drafts and the workspace's assembly objects.
- `get_assembly(name)` (R) → summary of a draft or a workspace assembly (latest version): parts
  (path, key, name), derived data (mass, CoG, wheelbase, track width, turning radius, gear ratios), rule
  checks, budget, warnings, problems. A workspace assembly is copied into a draft of the same name on first edit.
- `add_part(draft, key, attach_to=None, candidate=0, position=[x,y,z])`, `move_part(draft, path, delta)`,
  `rotate_part(draft, path, axis, turns)`, `remove_part(draft, path)` → the updated summary.
- `validate_assembly(name)` (R) → rule checks, overlaps and budget only.
- `get_bom(name)` (R) → BOM CSV text.
- `save_assembly(draft, slug, message)` → workspace version (message prefixed "AI via MCP: ").

**Simulation and training.**
- `list_controllers()` (R).
- `run_simulation(controller, params=None, quick_track=None, seed=1000, length_m=25, laps=1, opponents=0,
  max_time_s=240)` → one race with the quick-start car: finished, time, laps, distance, fraction, contacts,
  error (synchronous; ≤ max_time_s of sim time).
- `start_training(kind, controller=None, run_on="local", …)` — kind `benchmark` | `tune` | `rl`, same options as
  the Train tab (spec 0013/0022); `run_on`: `local` (this MCP process), `team` (any worker) or a worker id
  (spec 0020) → job id.
- `job_status(job_id)` (R) → state, progress, score, result summary (local or team job).
- `list_jobs()` (R), `stop_job(job_id)`.

**Tracks and run logs.**
- `list_tracks()` (R) → quick tracks with validation state.
- `get_track(name)` (R) → points, width, loop, laps, edit summary and validation report.
- `validate_track(name)` (R).
- `generate_corridor(seed=0, length_m=25)` (R) → centreline length, width profile (min/mean/max), objects.
- `list_runs()` (R) → `run` objects of the workspace (spec 0027 live logs).
- `get_run_summary(run)` (R) → duration, frames, mean/max speed, distance (integrated speed), loop rate
  (mean/min), deadline misses, battery start/min, faults, time per state, notes.
- `compare_runs(a, b)` (R) → both summaries and the differences.

## Behaviour
- Every tool call is appended to `~/.cache/raceforge/mcp/calls.jsonl` (time, tool, arguments without file
  contents, ok/error) — the plan's "every call is logged".
- Errors (unknown part, bad path, invalid params) are tool errors with a readable message, never crashes.
- The MCP server never talks to a real car (no deploy, teleop or stop tools) and never deletes anything.
- Local training jobs live as long as the MCP process; team jobs live in the backend.

## Acceptance criteria (→ tests, critical paths only)
- [ ] AC1: In-memory client: `apply_quickstart` → `get_assembly` gives derived mass > 0 and rule checks;
  `add_part` + `remove_part` change the part count; `save_assembly` creates a workspace version.
- [ ] AC2: `run_simulation` with the centering template on a short corridor finishes.
- [ ] AC3: `--read-only` lists no write tools.
- [ ] AC4: `get_run_summary` of a recorded run log (fixture) gives the right frame count, max speed and states.
- [ ] AC5 (manual, owner): Claude Desktop/Code with the README snippet: "build a quick-start car, add a sensor,
  validate, simulate it" works end to end.
