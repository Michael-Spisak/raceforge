# ADR-0019: Backend stack (spec 0006)

- **Status:** accepted
- **Date:** 2026-10-08

## Context
Spec 0006 needs persistence, migrations, password hashing, TOTP and an S3-compatible blob store on the
Proxmox VM (ADR-0005). All dependencies must be GPL-3.0-compatible.

## Decision
- **SQLAlchemy 2** (MIT) ORM, synchronous engine with **psycopg 3** (LGPL-3.0, dynamically linked, so
  GPL-compatible); FastAPI runs sync endpoints in its thread pool. Unit tests run on SQLite; CI runs the
  same tests against PostgreSQL 17.
- **Alembic** (MIT) migrations, expand/contract style so two API versions can run side by side (blue-green).
- **argon2-cffi** (MIT) for argon2id password hashes, **pyotp** (MIT) for TOTP.
- **Opaque tokens** (random, stored as SHA-256 hashes) for access/refresh/API tokens instead of JWT:
  instantly revocable, no signing key to manage, no extra dependency. Refresh tokens rotate; reuse of a
  rotated refresh token revokes the whole session.
- **httpx** (BSD-3) moves from dev to runtime dependencies: the desktop engine's sync client uses it.
- **Blob store: SeaweedFS** (Apache-2.0, `chrislusf/seaweedfs`, S3 gateway) instead of MinIO.
  MinIO stopped publishing community Docker images in 2025 (`minio/minio` and `quay.io/minio/minio` can no
  longer be pulled), and its server is AGPL. The API talks plain S3 through the **minio Python client**
  (Apache-2.0, pinned < 8), so any S3-compatible server (Garage, RustFS, AWS S3) can replace SeaweedFS by
  changing `RF_S3_*` settings. The plan's "MinIO" means "self-hosted S3 store" from here on.
- Deployment: Docker Compose (Caddy for TLS/routing, two API containers for blue-green, cloudflared;
  Tailscale on the host); nightly backups with restic.

## Consequences
One Python package serves both the local engine (spec 0008) and the backend (`raceforge.backend`), with
separate FastAPI apps. PostgreSQL is required in production; SQLite is for tests and `raceforge backend dev`.
