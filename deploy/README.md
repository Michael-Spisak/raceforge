# Backend deployment (spec 0006)

One Debian VM on the Proxmox server runs the whole backend with Docker Compose:

| Service | What it does |
|---|---|
| `api-blue`, `api-green` | Two copies of the RaceForge API (blue-green updates without downtime) |
| `postgres` | Users, workspaces, objects, versions, audit log |
| `s3` | Blob store (SeaweedFS, S3-compatible; ADR-0019) for scans, logs and models |
| `caddy` | TLS and the single entry point; spreads requests over blue and green |
| `migrate` | Runs database migrations, then exits |
| `cloudflared` | Optional (`--profile cloudflare`): public HTTPS path for teammates |

Secrets live only in `deploy/.env` (git-ignored). `deploy/.env.example` documents every variable.

## First setup
1. VM: Debian 13, 4+ vCPU, 8+ GB RAM, disk for the blobs. Install Docker Engine + Compose plugin, git, curl.
2. `git clone https://github.com/Michael-Spisak/raceforge.git && cd raceforge`
3. `cp deploy/.env.example deploy/.env` and fill it in (`openssl rand -hex 32` for each secret).
   Set `RF_TAG_BLUE`/`RF_TAG_GREEN` to a released version (e.g. `v0.1.0`), or build locally with
   `docker compose -f deploy/compose.yaml --env-file deploy/.env build`.
4. `deploy/raceforge-admin up`, then `deploy/raceforge-admin status` shows the health check.
5. Create your admin account: `RF_ADMIN_PASSWORD=… deploy/raceforge-admin bootstrap-admin <name>`.
6. Log in from the desktop app (tab **Team**) and click **Set up 2FA** (add the key to an authenticator
   app, enter the code). Admin actions stay blocked until 2FA is on.
7. Invite teammates: **Team → Create invite link** (admins only) and send them the link.

## Network access
- **Tailscale (your devices):** install Tailscale on the VM (`tailscale up`). Add the VM's tailnet name
  (e.g. `raceforge.your-tailnet.ts.net`) to `RF_SITE_ADDRESS`; Caddy gets its certificate from Tailscale
  when `tailscale cert` is allowed for the machine (or keep HTTP inside the tailnet).
- **Cloudflare Tunnel (teammates):** create a tunnel in the Cloudflare dashboard, route
  `raceforge.<your-domain>` to `http://caddy:80`, put the token into `RF_CLOUDFLARE_TUNNEL_TOKEN` and
  start it with `docker compose -f deploy/compose.yaml --env-file deploy/.env --profile cloudflare up -d`.
  Uploads are sent in ≤ 50 MB parts, so Cloudflare's 100 MB request limit is never hit.
- No ports need to be opened on the router.

## Daily operation (cron on the VM)
```cron
30 2 * * * cd /opt/raceforge && deploy/raceforge-admin backup >>/var/log/raceforge-backup.log 2>&1
0 3 * * *  cd /opt/raceforge && deploy/raceforge-admin purge-trash >>/var/log/raceforge-purge.log 2>&1
```
- **Backup:** `pg_dump` + a copy of all new blobs → encrypted **restic** snapshot. Keeps 7 daily,
  4 weekly and 6 monthly snapshots. Offsite: point `RESTIC_REPOSITORY` at a teammate's PC over Tailscale
  (`rest:http://teammate-pc:8000/raceforge` with `restic/rest-server`, or `sftp:user@teammate-pc:/backups`).
  Keep `RESTIC_PASSWORD` somewhere safe outside the VM: without it the backups cannot be read.
- **Status:** `deploy/raceforge-admin status`. The API reports disk usage with levels
  `warn` (70 %), `high` (85 %) and `critical` (95 %).

## Updates
`deploy/raceforge-admin update v0.2.0`
1. Asks you to take a **Proxmox snapshot** first.
2. Pulls the image and runs the migrations (expand/contract: the old version keeps working).
3. Replaces `api-green`, waits until it is healthy, then replaces `api-blue`. Caddy keeps serving
   from the other container during each step, so requests never fail (tested in CI).

Rollback: `deploy/raceforge-admin update <old version>` (same procedure), or restore the Proxmox snapshot.

## Restore (and the monthly restore test)
`deploy/raceforge-admin restore [snapshot-id]` stops the API, restores the database dump and puts all
blobs back, then starts the API again.

Restore test on a scratch VM (monthly, plan §9): clone the repo, copy `deploy/.env` (with the same
`RESTIC_REPOSITORY`/`RESTIC_PASSWORD`), run `deploy/raceforge-admin up` and `deploy/raceforge-admin restore`,
then log in and open a few versions. CI runs the same steps on every pull request
(`deploy/tests/stack-e2e.sh`: backup → wipe all volumes → restore → identical versions and blobs).

## Local development
- `PYTHONPATH=src uv run raceforge backend dev --admin admin:some-password` runs a backend with SQLite
  and a blob folder on http://127.0.0.1:8080.
- `deploy/tests/stack-e2e.sh` builds the image and tests the full Compose stack (needs Docker).
