# ADR-0011: Dependencies for `raceforge.core`

- **Status:** accepted
- **Date:** 2026-10-07

## Context
Spec 0001 requires YAML for human-edited configs and property-based tests for all top-level models.
`core` must stay lightweight because it is imported by the car runtime's Python side, workers and the backend.

## Decision
- Runtime: **pydantic ≥ 2.8** (MIT) and **PyYAML ≥ 6** (MIT). No numpy in `core` — the little quaternion/transform maths needed is implemented in pure Python.
- Dev only: **hypothesis** (MPL-2.0, used only as a test tool, not distributed).
- UUIDv7 is generated in-house (Python 3.12 has no `uuid.uuid7`).

## Consequences
`core` stays import-cheap and GPL-compatible. Heavy numeric work lives in `construct`/`sim`, which may use numpy.
