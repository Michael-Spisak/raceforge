# ADR-0027: aarch64 build of the car runtime (static musl)

- **Status:** accepted (2026-10-08)
- **Date:** 2026-10-08

## Context
Spec 0005 AC9 requires a cross-compiled aarch64 build in CI. The board is generic aarch64 Linux,
either Raspberry Pi OS or Armbian; the exact board is decided in January. Nothing in CI builds or
tests `rf-runtime` for aarch64 today, and `setup-board.sh --binary` expects a binary to come from
somewhere. Every runtime dependency is pure Rust: there are no `-sys` crates and no `cc` builds.

A prototype on the current workspace (Rust 1.98.0, x86_64 Ubuntu 24.04 host) found:

- **glibc target (`aarch64-unknown-linux-gnu`), built with Ubuntu 24.04's cross gcc:**
  - It builds, but requires **glibc 2.39**. Rust's standard library references `pidfd_spawnp` and
    `pidfd_getpid`, and the linker records them as a hard version requirement.
  - Run against Ubuntu 22.04's arm64 glibc 2.35 under qemu, it fails with
    ``version `GLIBC_2.39' not found``. Raspberry Pi OS and Armbian Bookworm ship glibc 2.36, so the
    binary would not start on the boards we target.
- **musl target (`aarch64-unknown-linux-musl`), linked with `rust-lld`:**
  - It builds in 16 s with only `rustup target add`: no system packages and no new tools.
  - The result is a 1.8 MB statically linked binary that runs on any aarch64 Linux, including under
    qemu against glibc 2.35.
- **Tests under qemu (musl target):** 112 of 117 pass. All 5 failures are timing tests:
  - 4 miss the 15 ms deadline on the first control step while the emulator translates code;
  - 1 misses the ~8 ms send-latency bound in `udp_link`.

  Emulation checks behaviour, not timing.
- **Allocator cost:** musl's allocator is slower. The per-tick serialization work (clone the tick
  record, build and serialize the telemetry frame, the observation and a LiDAR scan) took, on x86:

  | Build | Median per tick | p99 per tick |
  |---|---|---|
  | glibc | 55 µs | 140–170 µs |
  | musl | 200–250 µs | ~420 µs |
  | musl + `dlmalloc` (pure Rust) | 130 µs | 220 µs |

  All of these are small against the 15 ms per-step deadline. Only part of this work runs on the
  control thread; the rest runs on the logger and telemetry threads.

## Decision
- **Target:** ship `rf-runtime` as **`aarch64-unknown-linux-musl`**, statically linked: one binary
  for every aarch64 board, independent of the OS's glibc version.
- **Toolchain:**
  - `rust-toolchain.toml` adds `targets = ["aarch64-unknown-linux-musl"]`, so rustup installs it
    with the pinned compiler.
  - `car_runtime/.cargo/config.toml` sets `linker = "rust-lld"` for that target.
  - No cross gcc, Docker image, `cross` or `cargo-zigbuild`, and no new crate.
  - Locally it is one command on any dev machine:
    `cargo build --release --target aarch64-unknown-linux-musl -p rf-runtime`. Only Linux was tried
    in the prototype; macOS and Windows are expected to work because `rust-lld` and musl's startup
    files ship with rustup, and the first CI run or a team member confirms it.
- **CI:**
  - The `rust` job (x86_64) cross-builds the release binary for the musl target and uploads it as
    the workflow artifact `rf-runtime-aarch64` with short retention, for manual installs until
    `raceforge deploy` exists.
  - A new **`rust-arm64`** job runs `clippy` and
    `cargo test --locked --target aarch64-unknown-linux-musl`, including the `RF_PYTHON` end-to-end
    tests, natively on GitHub's free arm64 runner for public repositories (`ubuntu-24.04-arm`). It
    tests the binary that ships, with real timing on a real ARM core rather than under emulation.
- **Allocator:** keep musl's default for now. A different global allocator is a runtime dependency
  and gets its own decision. If the HIL measurements on the board (AC10) show allocator time in the
  jitter or step time, the first candidate is `dlmalloc` (MIT OR Apache-2.0, pure Rust, so the
  static linker-only build stays). It halved the musl overhead in the prototype.

## Consequences
- AC9 is complete with this (cargo-deny: ADR-0026).
- The board needs no Rust, no compiler and no particular glibc. `setup-board.sh --binary` installs
  the CI artifact or a local build.
- The arm64 job tests the shipped target but not the Pi itself. Real jitter numbers still come from
  HIL (AC10, ADR-0016).
- The CI timing tests run on one more machine type. If they flake there, the fix is in the test or
  the code, per AGENTS.md: never weaken the test.
- The `rustup` download for `car_runtime/` grows by the musl `rust-std` component, about 30 MB.
- **Rejected alternatives:**
  - glibc cross-build on the CI host: needs glibc 2.39 on the board, see Context.
  - glibc against an older sysroot, via a Debian Bookworm container or `cargo-zigbuild` with a
    glibc version suffix: gets glibc's faster allocator, but needs Docker or Zig in CI and on dev
    machines for a cost the deadline does not show yet. Revisit if HIL shows the allocator matters
    and `dlmalloc` is not enough.
  - Tests under qemu only: timing tests fail under emulation, see Context.
