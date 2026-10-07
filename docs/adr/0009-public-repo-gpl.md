# ADR-0009: Repository visibility and licence

- **Status:** accepted
- **Date:** 2026-10-07

## Context
Unlimited CI minutes (incl. macOS/Windows) are needed; data must never be published.

## Decision
Tool repo is **public** on the owner's account under **GPL-3.0-or-later**; secret scanning + push protection on; no self-hosted runners; no data in the repo. Controller code lives in a separate **private** repo created by the car team.

## Consequences
Licence check in CI blocks GPL-incompatible dependencies.
