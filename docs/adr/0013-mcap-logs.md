# ADR-0013: MCAP for run logs

- **Status:** accepted
- **Date:** 2026-10-07

## Context
Spec 0003 records every simulated (and later real) run. Logs must be streamable, append-only, indexable
by time, and open in common robotics tools.

## Decision
Use **MCAP** (`mcap` Python package, MIT) with JSON-encoded messages: channel `/telemetry`
(`raceforge.TelemetryFrame`, JSON Schema from spec 0001) and `/truth` (simulation ground truth).

## Consequences
Logs open directly in Foxglove Studio. The Rust car runtime (spec 0005) will write the same format.
