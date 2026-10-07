# ADR-0007: Car runtime architecture

- **Status:** accepted
- **Date:** 2026-10-07

## Context
The car control loop must be reliable at 50–100 Hz with camera and LiDAR load; controllers are written in Python by the team.

## Decision
**Rust core** (drivers, timing loop, watchdog, motor output, safety, race mode, logging, telemetry) + **Python controllers** via PyO3/IPC with per-tick deadlines. EV3 runs ev3dev from microSD and talks over USB serial. Watchdog stops the car on controller crash/hang.

## Consequences
Owner is not a Rust reviewer yet → property tests, fuzzing of parsers, HIL suite, clippy/cargo-deny in CI, always human-approved.
