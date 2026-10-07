# ADR-0001: One tool or three separate tools?

- **Status:** accepted
- **Date:** 2026-10-07

## Context
Construction output (geometry, mass, joints, sensor poses) is the simulation input; training is the simulator run headless many times. Separate tools would need a shared format anyway and would drift apart.

## Decision
Build **one repository and one application** (RaceForge) with modules construct / track / sim / train / deploy / live / history, a CLI and an MCP server, all thin layers over one service API (`raceforge.api`). The iOS app TrackScout lives in the same repo.

## Consequences
Every feature is implemented once. Module boundaries are enforced by import-linter and contract tests.
