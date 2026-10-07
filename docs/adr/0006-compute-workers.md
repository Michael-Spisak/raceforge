# ADR-0006: Where training runs

- **Status:** accepted
- **Date:** 2026-10-07

## Context
Training and processing need GPUs; the team has RTX 4070/3070 machines, gaming laptops and MacBooks.

## Decision
Opt-in, idle-only **workers** built into the desktop app pull jobs from the backend, fetch only missing blobs, run in uv-pinned isolated processes, stream logs, checkpoint and resume elsewhere. Users pick this machine, a specific worker or auto.

## Consequences
Jobs wait when no worker is free (no backend compute).
