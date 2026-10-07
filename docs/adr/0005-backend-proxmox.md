# ADR-0005: Backend

- **Status:** accepted
- **Date:** 2026-10-07

## Context
Team of 6 needs shared data, versioning, collaboration, job queue and telemetry relay; a home Proxmox server (20 vCPU, 100 GB RAM, 1–2 TB SSD for the VM) is available 24/7.

## Decision
One Debian VM with Docker Compose: **FastAPI** (REST + WebSocket), **PostgreSQL** (metadata, versions — source of truth), **MinIO** (content-addressed blobs), **Redis** (queue, pub/sub), Yjs sync, Caddy, GlitchTip. Access via **Tailscale** (owner's devices) and **Cloudflare Tunnel** (team, browser, MCP). Own accounts (2FA mandatory for admin). Blue-green API deploys; nightly + offsite restic backups. The backend never runs heavy compute.

## Consequences
Local-first desktop cache bridges outages.
