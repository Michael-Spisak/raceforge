# ADR-0019: Fuzzing the car runtime parsers (cargo-fuzz)

- **Status:** proposed
- **Date:** 2026-10-08

## Context
Spec 0005 AC1 asks for `cargo fuzz` targets for the EV3 and LD06 parsers that run in CI (short) without
crashes. These parsers read bytes the runtime does not control: UDP datagrams from the EV3 and a UART
byte stream from the LiDAR. A panic in either stops the control loop on the car. Property tests
(proptest, ADR-0015) cover round-trips of valid frames but do not search for crashing inputs.
AGENTS.md requires an ADR for every new dependency, including dev tools.

A prototype against the current `rf-proto` (`nightly-2026-09-24`, 20 s per target, no corpus) found:
- **LD06 stream parser:** about 27,000 inputs/s; its 8-bit CRC is no obstacle (179 edges, 1,207 features).
- **EV3 frames:** about 400,000 inputs/s, but coverage stalled at 34 edges, because random inputs
  almost never pass the CRC-16 check, so the field decoding behind it was never reached.
  Recomputing the CRC inside the target raised coverage to 56 edges and features from 35 to 104.

No crashes were found.

## Decision
- **Tools:** **cargo-fuzz** 0.13.2 (MIT OR Apache-2.0) with **libfuzzer-sys** 0.4
  (`(MIT OR Apache-2.0) AND NCSA`; the NCSA part is LLVM's libFuzzer). Both are development tools only.
  The fuzz binaries are never built into the runtime or shipped to the car, and all three licences
  are GPL-3.0 compatible.
- **Crate:** `car_runtime/fuzz/` is its own crate (`publish = false`, with its own `[workspace]` and
  `Cargo.lock`), outside the runtime workspace.
  - Runtime builds, `cargo test` and the stable toolchain pin are unaffected.
  - libFuzzer's `#[no_mangle]` entry point is not subject to the workspace's `unsafe_code = forbid`.
    The crates under test keep it.
- **Toolchain:** cargo-fuzz needs a nightly compiler (sanitizer coverage). The fuzz job uses a
  **dated nightly** (`nightly-2026-09-24` at first), named explicitly as `cargo +nightly-YYYY-MM-DD fuzz`
  in CI and in the README. It is bumped deliberately, like the stable pin in `rust-toolchain.toml`.
  The address sanitizer stays on (cargo-fuzz default) so that `unsafe` code in dependencies is
  checked too.
- **Targets (v1, AC1):**
  - `ev3_frames`: `SensorFrame::decode` and `CommandFrame::decode` on the raw input, and again with
    the CRC-16 trailer recomputed so the field decoding is reached. Every decoded frame must
    re-encode to the same frame.
  - `ld06_stream`: the streaming `ld06::Parser` fed the input in chunks, as the UART delivers it.
- **CI:** a Linux `fuzz` job.
  - On every pull request: each target runs 60 s (`-max_total_time=60`).
  - Weekly scheduled run: 30 min per target.
  - The corpus is kept between runs with the Actions cache and never committed (it is generated data).
  - On a crash, the job fails and uploads `fuzz/artifacts/` as a workflow artifact.
  - The fix lands with a regular unit test that reproduces the input, so the regression is guarded
    by `cargo test` on every OS.
- **Install:** `cargo install --locked cargo-fuzz@0.13.2`, cached in CI. Locally the same command
  plus `rustup toolchain install nightly-2026-09-24 --profile minimal` (Linux or macOS; libFuzzer
  is not supported on Windows).

## Consequences
- AC1 is complete once the job is green.
- New binary parsers are added as targets in the same pull request that adds the parser.
- **Candidates for the next targets:** the WebSocket frame reader in `rf-telemetry` (the only parser
  that is reachable over the network, in test mode) and controller IPC line decoding.
- **Cost:** CI gets one more Linux job, with 2 × 60 s of fuzzing per pull request plus build time.
  The first run, without a cache, also compiles cargo-fuzz.
- A dated nightly can stop building after a bump. The pin keeps that failure out of unrelated pull
  requests, and moving to a newer nightly is a deliberate one-line change.
- Rejected alternatives:
  - proptest only: already in use, but not coverage-guided.
  - afl.rs: needs the AFL++ toolchain on every runner.
  - bolero: one harness for several engines, but more dependencies than this needs.
