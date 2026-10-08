# ADR-0018: Frontend toolchain and local engine API

- **Status:** accepted
- **Date:** 2026-10-08

## Context
Spec 0008 builds the first UI (ADR-0004: Electron + React + three.js). The UI must talk to Python code only
through `raceforge.api` (AGENTS.md) and work both in Electron and in a plain browser.

## Decision
- **Engine:** FastAPI + Uvicorn (MIT/BSD) serve `raceforge.api` on 127.0.0.1 (REST + WebSocket); OpenAPI is
  snapshot-tested and the TypeScript client types are generated with `openapi-typescript` (MIT).
- **Frontend:** Vite + TypeScript (strict) + React 19 + three.js + @react-three/fiber + @react-three/drei
  (all MIT), zustand for state (MIT), i18next/react-i18next (MIT), uPlot for time-series plots (MIT),
  `@foxglove/mcap`-free replay (frames come from the engine).
- **Quality:** ESLint + typescript-eslint, Vitest, Playwright (Apache-2.0).
- **Desktop:** Electron (MIT) main process spawns `raceforge ui --engine-only --port <free>` and loads the UI.
- Package manager: npm (ships with Node, no extra tool).
- TypeScript is pinned to 5.9: typescript-eslint supports < 6.1 and openapi-typescript needs 5.x.

## Consequences
All dependencies are GPL-3.0-compatible. Node 22+ is needed for development; end users get it bundled in
Electron later (installer spec).
