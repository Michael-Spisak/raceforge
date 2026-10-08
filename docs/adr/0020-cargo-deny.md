# ADR-0020: cargo-deny for the car runtime's dependencies

- **Status:** accepted (2026-10-08)
- **Date:** 2026-10-08

## Context
Spec 0005 AC9 requires `cargo deny` in CI. ADR-0015 deferred it until the runtime binary existed.
`rf-runtime` now ships to the car with 49 third-party crates in `car_runtime/Cargo.lock`.
- The repository is GPL-3.0 (ADR-0009). Every dependency licence must be compatible, but today
  only reviewers check this, and only for direct dependencies.
- Nothing tells us when a crate on the car gets a security advisory or is yanked.

A prototype with cargo-deny 0.20.2 on the current lockfile turned up two configuration needs:
- **Our own crates:** they are `GPL-3.0-or-later` and were flagged by the licence allow-list.
- **Path dependencies:** they count as wildcards for crates that could be published.

With both handled (see Decision), all four checks pass in under 2 s.

## Decision
- **Tool:** **cargo-deny** 0.20.2 (MIT OR Apache-2.0), a development tool only.
  - Installed with `cargo install --locked cargo-deny@0.20.2`, and cached in CI.
  - A third-party GitHub Action is not used: it would add a container and its own update cycle.
- **Config:** `car_runtime/deny.toml`, run in the `rust` CI job as `cargo deny check` for the runtime
  workspace and for the fuzz crate (ADR-0019).
  - **licenses:** an explicit allow-list of licences compatible with GPL-3.0:
    - `MIT`, `Apache-2.0`, `Apache-2.0 WITH LLVM-exception`
    - `BSD-2-Clause`, `BSD-3-Clause`, `ISC`, `Zlib`
    - `Unicode-3.0` (for `unicode-ident`)
    - `NCSA` (for libFuzzer, fuzz crate only)

    `OR` expressions pass when one option is allowed. Our own crates are marked
    `publish = false` and skipped (`[licenses.private] ignore = true`); their licence comes from
    `[workspace.package]`. A licence outside the list needs an ADR.
  - **advisories:** the RustSec database. Vulnerabilities, unmaintained crates and yanked versions
    fail the check.
  - **bans:** wildcard versions are denied (path dependencies between our private crates are
    allowed). Duplicate versions only warn: the tree already has two `getrandom` and two `r-efi`
    versions that we cannot unify.
  - **sources:** crates.io only, with no unknown registries and no git dependencies.
- **Schedule:** the check runs on every pull request. A weekly scheduled CI run also catches new
  advisories when no pull request is open.
- **Exceptions:** an advisory without a fix that does not affect us gets an `ignore` entry in
  `deny.toml` with the advisory ID, a reason and a review date. Otherwise the fix is a dependency
  bump in the same week.

## Consequences
- AC9's dependency gate is enforced: new licences, git dependencies and known-vulnerable or
  yanked crates fail CI instead of relying on review.
- **Network:** the advisory check downloads the RustSec database from GitHub, so CI can turn red
  without a code change when an advisory is published. That is intended for software that drives
  the car, and the weekly run makes such failures show up outside feature work.
- **Small manifest change:** each `car_runtime` crate gets `publish = false`. This matches reality,
  since nothing is published to crates.io.
- **Python dependencies are not covered.** They are still reviewed through ADRs (AGENTS.md rule 5)
  and pinned in `uv.lock`. A licence check for them would be a separate decision.
- The aarch64 cross-build, the other half of AC9, is a separate decision.
