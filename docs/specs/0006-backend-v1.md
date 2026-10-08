# Spec 0006: Backend v1 — accounts, workspaces, versioned objects, blob store, sync

- **Status:** approved (2026-10-08)
- **Owner:** @Michael-Spisak
- **Plan section:** docs/PLAN.md §6 (versioning & sharing), §9 (backend), §9c (operations), §9d (targets)
- **Related ADRs:** ADR-0005 (backend on Proxmox), ADR-0009 (public repo, no secrets); new ADR-0019
  (backend stack: SQLAlchemy/Alembic, MinIO client, auth libraries)
- **Depends on:** Spec 0001 (core schemas, content hashes), Spec 0008 (engine API, desktop app)

## Purpose
The team's shared home: one server on your Proxmox machine where everybody logs in, and where cars, parts,
tracks, controllers and run logs live as **immutable versions**, with large files (scans, recordings,
models) in a blob store. The desktop app keeps working offline and syncs when the backend is reachable.
Later specs build collaboration (locks/presence), compute workers, live-telemetry relay, MCP and reports on it.

## Scope
- In scope (v1):
  1. **Deployment:** Docker Compose for one Debian VM: `api` (FastAPI, same Python package), `postgres`,
     `minio`, `caddy` (TLS, routes), plus `cloudflared` and Tailscale documentation/config templates.
     Secrets only from `.env` (git-ignored); `deploy/.env.example` documents every variable.
  2. **Accounts & auth:** admin bootstrap command; invite links (email via SMTP, or copy link);
     argon2id password hashes; sessions with short-lived access + refresh tokens (HTTP-only cookie for the
     browser, bearer for the desktop app); **TOTP 2FA mandatory for admins**, optional for members;
     roles `admin` / `member`; per-device **API tokens** with scopes (`read`, `sim_train`, `edit`, `admin`;
     MCP may never get `admin`), revocable, optional expiry; rate limiting on login.
  3. **Workspaces:** any number per team; every member sees every workspace (plan §6); copy/promote objects
     between workspaces with lineage.
  4. **Objects & versions:** kinds from `core.meta.ObjectKind`; an object has a slug, tags and a version
     history. Creating a version stores the canonical JSON (spec 0001), its SHA-256 content hash, a semantic
     version + optional name, author, message, parent versions; versions are immutable. Draft per user per
     object (autosave) separate from versions (plan: "autosave + explicit Create version").
     Branch = a version whose parent is not the latest; merge (submodel-wise) comes with the editor spec.
  5. **Blob store:** content-addressed (SHA-256) blobs in MinIO behind the API; **chunked, resumable uploads**
     (≤ 50 MB parts, works through Cloudflare's 100 MB limit); ranged downloads; checksum verification;
     "already have it?" check so nothing is uploaded twice. Retention: keep everything (plan §9c).
  6. **Trash:** deleting moves to trash for 30 days (admin only); restore; purge job.
  7. **Audit log:** every write (who, when, what, from which token/client); visible to admins.
  8. **Sync client in the desktop engine:** local SQLite + blob cache (`~/.cache/raceforge/workspace/`),
     background sync (pull versions/blobs on demand, push new versions and drafts), online/offline state in
     the UI status bar, conflicts reported (two users created versions from the same parent → both kept as
     branches, shown to the user; no silent overwrite).
  9. **UI (minimal):** login screen (with 2FA), workspace picker, "Save as version" for quick-start cars and
     controllers, version history list (no 3D diff yet), invite/token management for admins.
  10. **Operations:** health/status endpoint (DB, MinIO, disk usage with 70/85/95 % warnings), nightly
     `pg_dump` + MinIO mirror to a restic repository (offsite target configurable: teammate PC over
     Tailscale), restore script + documented restore test; `raceforge-admin update <version>` with Proxmox
     snapshot reminder, DB migrations (Alembic, expand/contract) and blue-green API containers.
- Out of scope (later specs): live presence and submodel locks, compute workers/job queue, live telemetry
  relay, Discord notifications, MCP server, reports, 3D diff/merge, comments, GlitchTip.

## API (contract — human-owned, OpenAPI snapshot-tested)
Prefix `/api/v1` on the backend (separate from the local engine API of spec 0008):
`auth/login`, `auth/refresh`, `auth/logout`, `auth/totp/setup|verify`, `invites` (admin), `users/me`,
`tokens` (CRUD own tokens), `workspaces` (list/create/rename), `workspaces/{ws}/objects` (list/create,
filter by kind/tag/slug), `objects/{id}` (get/update meta/delete→trash/restore), `objects/{id}/versions`
(list/create), `versions/{id}` (get content), `objects/{id}/draft` (get/put), `blobs/{sha}` (HEAD/GET with
Range), `uploads` (create → parts → complete), `audit` (admin), `status`.
Every object/version payload uses the spec 0001 models; content is validated with `core.io.load` on upload.

## Non-functional targets (plan §9d)
- Data loss ≤ 24 h (nightly backup + offsite), backend downtime "nearly never" (blue-green deploys).
- Upload of a 2 GB file through Cloudflare resumes after a disconnect without re-sending finished parts.
- Version list of a workspace with 1,000 objects < 300 ms; 50 concurrent users without errors (load test).
- The desktop app stays fully usable offline; sync of 100 small versions < 5 s on the LAN.

## Acceptance criteria (→ tests)
- [ ] AC1: Docker Compose stack starts from `.env.example`-style config on a clean VM (CI: Compose up on
      Ubuntu runner, health green).
- [ ] AC2: Auth: invite → register → login → refresh → logout; admin without TOTP cannot use admin endpoints;
      wrong TOTP rejected; token scopes enforced (an `read` token cannot create versions; MCP tokens cannot
      get `admin`); login rate limit works.
- [ ] AC3: Versions are immutable; content hash equals `core.io.content_hash`; invalid content (spec 0001
      validation) is rejected with the validation message; drafts are per user and never appear as versions.
- [ ] AC4: Blob upload in parts with an interruption resumes; duplicate upload is skipped; downloads support
      Range; corrupted part detected by checksum.
- [ ] AC5: Trash: delete → restore works; purge after 30 days (time-travel test); only admins can delete.
- [ ] AC6: Audit log records every write with user and token.
- [ ] AC7: Sync: two clients, offline edits, reconnect → both versions present as branches, conflict shown;
      blobs fetched lazily and cached; offline mode keeps the engine API working.
- [ ] AC8: Backup script produces a restic snapshot; restore into a scratch Compose stack gives identical
      versions and blobs (CI test with a local restic repo).
- [ ] AC9: Migrations run forward on an existing DB; blue-green switch keeps answering requests during the
      update (CI test with two API containers behind Caddy).
- [ ] AC10: UI: login (incl. 2FA), workspace picker, "Save as version", version history; Playwright e2e
      against the Compose stack.
- [ ] AC11 (manual, owner): deploy on the Proxmox VM, reach it via Tailscale and via the Cloudflare domain,
      invite one teammate.

## Open questions
- Which teammate PC hosts the offsite backup (needed for AC11, not for the code).
- SMTP account for invite emails (until then invite links are copied manually).
