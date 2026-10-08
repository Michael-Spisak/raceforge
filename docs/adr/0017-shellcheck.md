# ADR-0017: shellcheck for shell scripts

- **Status:** accepted (2026-10-08)
- **Date:** 2026-10-08

## Context
Spec 0005 added `car_runtime/deploy/setup-board.sh`, which runs as root on the car's board and edits
kernel arguments, `/etc/fstab` and systemd units. Mistakes in such scripts (unquoted variables,
`rm -rf $dir/*` with an empty `$dir`, unchecked `cd`) are easy to make and hard to review. Python
and Rust code is linted in CI; shell scripts were not. AGENTS.md requires an ADR for every new
dependency, including dev tools.

## Decision
- Dev dependency **shellcheck-py** (the MIT-licensed wheel that ships the shellcheck binary;
  shellcheck itself is GPL-3.0). It is a development tool only and is never distributed with
  RaceForge, so both licences are compatible.
- Installed with the other dev tools by `uv sync`, version locked in `uv.lock`; wheels exist for
  every CI platform (Linux x86_64, macOS arm64/x86_64, Windows x86_64).
- `scripts/check.sh` runs `shellcheck -S style` on every tracked `*.sh` file, so it runs locally
  and in CI on all three operating systems. Findings fail the check; a deliberate exception needs
  an inline `# shellcheck disable=SCxxxx` with a reason.
- `.gitattributes` keeps `*.sh` files LF on every checkout (CRLF would break the scripts on the
  board and make shellcheck report every line on Windows).

## Consequences
- Board and helper scripts get the same gate as the code; new scripts are covered automatically.
- About 16 MB more in the dev environment (the shellcheck binary); nothing changes for the car or
  the app.
- shellcheck updates arrive as normal dependency bumps in `uv.lock`.
