# ADR-0015: Dependencies for the car runtime wire protocols (`rf-proto`)

- **Status:** proposed
- **Date:** 2026-10-07

## Context
Spec 0005 needs binary EV3 frames, an LD06 LiDAR parser and JSON IPC with the Python controller host
(ADR-0014). AC1 asks for property tests of all encoders/decoders.

## Decision
- Runtime: **serde** + **serde_json** (MIT OR Apache-2.0) for the controller IPC messages,
  **thiserror** (MIT OR Apache-2.0) for error types. Binary frames are hand-written (no codec crate).
- Dev only: **proptest** (MIT OR Apache-2.0).
- `Cargo.lock` is committed (the runtime is a binary deployed to the car).
- Clippy denies `unwrap` in library code; `clippy.toml` allows `unwrap`/`expect`/`panic` in tests only.

## Consequences
All licences are GPL-3.0 compatible. `cargo deny` (AC9) will enforce the licence allow-list once the
`rf-core` binary lands; until then CI runs `cargo fmt --check`, `clippy -D warnings` and `cargo test`.
