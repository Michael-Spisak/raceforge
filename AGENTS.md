# AGENTS.md — rules for every AI coding agent (Claude Code, Copilot, Cursor, …)

RaceForge is built almost entirely by AI agents under human control ("controlled vibe coding").
The human owns the *what* (specs, contracts, reviews); agents own the *how*. Read this file fully before every task.

Full plan: [`docs/PLAN.md`](docs/PLAN.md). Decisions: [`docs/adr/`](docs/adr/). Module specs: [`docs/specs/`](docs/specs/).

## Golden rules
1. **No code without a spec.** Every task links an issue that links a spec in `docs/specs/`. If the spec is missing or unclear, stop and ask.
2. **Tests first.** Write or extend tests from the spec's acceptance criteria before implementing.
3. **Never weaken, skip or delete tests to make CI pass.** Fix the code or ask.
4. **Stay in scope.** Only touch files listed as in-scope in the issue. Out-of-scope improvements become new issues.
5. **No new dependency without an ADR** (`docs/adr/`). Check licence compatibility with GPL-3.0.
6. **Contracts are human-owned.** Do not change `src/raceforge/core/` schemas, the REST/OpenAPI schema, MCP tool schemas or the telemetry frame format without an approved spec change.
7. **No secrets, scans, models or logs in the repo.** The repo is public. Config comes from env vars / local config files listed in `.gitignore`.
8. **Small PRs.** Aim for < 500 changed lines, one module, one concern.

## Architecture rules (enforced by import-linter where possible)
- Front-ends (`app`/`frontend`, `cli`, `mcp`) only talk to `raceforge.api` — never directly to sim, train, backend internals.
- `raceforge.control` (controllers, shared by sim and real car) must not import `raceforge.sim`, `raceforge.train` or anything GUI/backend.
- Controllers access hardware only through the `RobotIO` interface (`SimIO` / `RealIO`).
- The construction assembly is the single source of truth for mass, CoG, joints, sensor poses, collision shape and budget — never duplicate these values elsewhere.
- Heavy work (training, batch sim, benchmarks, scan processing) runs on workers, never in the backend.

## Conventions
- Python ≥ 3.12, managed with `uv`. Formatting/linting: `ruff`. Types: `pyright` (strict in `core/`, `api/`, `control/`).
- TypeScript strict mode, ESLint, Vitest, Playwright (frontend).
- Units: SI internally (metres, kilograms, seconds, radians). Convert only at UI boundaries.
- Code, comments, docs, specs, ADRs: **English**. UI strings: i18n (DE + EN), never hard-coded.
- Pydantic v2 models for all data crossing module boundaries.

## Before you finish a task, run
```bash
scripts/check.sh   # ruff format/check, pyright, import-linter, pytest (same as CI)
```
All must pass. Then open a PR using the template; include risks and, for UI work, screenshots.

## Merge policy
- Auto-merge allowed (green gates + Claude `/code-review` + Copilot review) for: UI polish, docs, tests, reports, i18n strings.
- Human approval always required for: `src/raceforge/core/`, API/MCP schemas, `car_runtime/`, race mode, safety, backend auth.

## Lessons learned
Repeated agent mistakes are turned into new rules here at every milestone review.
