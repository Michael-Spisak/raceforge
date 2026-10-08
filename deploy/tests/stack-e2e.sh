#!/usr/bin/env bash
# Spec 0006 AC1 (stack starts, health green), AC9 (blue-green update keeps answering) and
# AC8 (backup → wipe → restore gives identical versions and blobs). Needs Docker + uv.
#   deploy/tests/stack-e2e.sh            (from the repo root)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="$(mktemp -d)"
PORT="${RF_E2E_PORT:-18080}"
URL="http://127.0.0.1:$PORT"
export COMPOSE_PROJECT_NAME="rf-e2e"
export RF_ENV_FILE="$WORK/.env"
export RF_ADMIN_PASSWORD="e2e-admin-password"
ADMIN="$ROOT/deploy/raceforge-admin"
PY=(uv run --project "$ROOT" python "$ROOT/deploy/tests/stack_check.py")

cat >"$RF_ENV_FILE" <<ENV
RF_IMAGE=raceforge-backend-e2e
RF_TAG_BLUE=a
RF_TAG_GREEN=a
RF_PUBLIC_URL=$URL
RF_SITE_ADDRESS=:80
RF_HTTP_PORT=$PORT
RF_HTTPS_PORT=$((PORT + 363))
RF_SECURE_COOKIES=false
RF_POSTGRES_PASSWORD=$(openssl rand -hex 16)
RF_S3_ACCESS_KEY=e2e
RF_S3_SECRET_KEY=$(openssl rand -hex 16)
RESTIC_REPOSITORY=$WORK/restic
RESTIC_PASSWORD=e2e-restic
ENV

cleanup() {
  docker compose -f "$ROOT/deploy/compose.yaml" --env-file "$RF_ENV_FILE" down -v --remove-orphans \
    >/dev/null 2>&1 || true
  rm -rf "$WORK" 2>/dev/null || true
}
trap cleanup EXIT

echo "== build image"
docker build -q -f "$ROOT/deploy/Dockerfile" -t raceforge-backend-e2e:a "$ROOT" >/dev/null
docker tag raceforge-backend-e2e:a raceforge-backend-e2e:b

echo "== AC1: stack up, health green"
"$ADMIN" up
curl -fsS "$URL/api/v1/status"
echo
"$ADMIN" bootstrap-admin admin

echo "== seed data"
"${PY[@]}" seed "$URL" "$WORK/fingerprint.json"

echo "== AC9: blue-green update while requests keep coming"
"${PY[@]}" hammer "$URL" 45 >"$WORK/hammer.log" 2>&1 &
HAMMER=$!
sleep 3
"$ADMIN" update b --yes --no-pull
sleep 3
wait "$HAMMER" || { cat "$WORK/hammer.log"; echo "requests failed during update" >&2; exit 1; }
cat "$WORK/hammer.log"
grep -q "^RF_TAG_BLUE=b" "$RF_ENV_FILE" && grep -q "^RF_TAG_GREEN=b" "$RF_ENV_FILE"

echo "== AC8: backup, wipe everything, restore"
"$ADMIN" backup
docker compose -f "$ROOT/deploy/compose.yaml" --env-file "$RF_ENV_FILE" down -v
"$ADMIN" up
"$ADMIN" restore
"${PY[@]}" verify "$URL" "$WORK/fingerprint.json"
if [ "${RF_E2E_PLAYWRIGHT:-0}" = 1 ]; then
  echo "== AC10: Playwright team flow against the stack (frontend must be built)"
  # the e2e admin logs in with 2FA; give it the test secret from frontend/playwright.config.ts
  docker compose -f "$ROOT/deploy/compose.yaml" --env-file "$RF_ENV_FILE" exec -T postgres \
    psql -q -U raceforge -d raceforge -c \
    "UPDATE users SET totp_secret = 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP', totp_enabled = true WHERE username = 'admin'"
  (cd "$ROOT/frontend" && RF_E2E_BACKEND_URL="$URL" npx playwright test e2e/team.spec.ts)
fi
echo "== stack e2e passed"
