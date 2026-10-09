#!/usr/bin/env sh
# Start RaceForge for manual testing: engine (API) + frontend with hot reload, optionally the team backend.
# Usage: scripts/dev.sh [--backend]    then open http://localhost:5173 ; Ctrl+C stops everything.
#   --backend   also run the local team backend (SQLite, http://127.0.0.1:8080, login admin / admin)
set -eu
cd "$(dirname "$0")/.."
# PYTHONPATH: Python >= 3.13 on macOS skips "hidden" .pth files that uv may create.
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"

with_backend=0
for arg in "$@"; do
  case "$arg" in
    --backend) with_backend=1 ;;
    -h | --help) sed -n '2,4p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

command -v uv >/dev/null 2>&1 || { echo "uv is missing: https://docs.astral.sh/uv/" >&2; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "npm is missing: install Node.js 22" >&2; exit 1; }
[ -d .venv ] || uv sync
[ -d frontend/node_modules ] || (cd frontend && npm ci)

stop() {
  trap - INT TERM EXIT
  kill 0 2>/dev/null || true # the whole process group: uv, npm and the servers they started
}
trap stop INT TERM EXIT

uv run raceforge ui --port 8765 &
if [ "$with_backend" = 1 ]; then
  uv run raceforge backend dev --admin admin:admin &
fi
(cd frontend && RACEFORGE_ENGINE_URL=http://127.0.0.1:8765 npm run dev -- --port 5173 --strictPort) &

echo
echo "RaceForge is starting:"
echo "  UI (hot reload)  http://localhost:5173"
echo "  engine API       http://127.0.0.1:8765"
[ "$with_backend" = 1 ] && echo "  team backend     http://127.0.0.1:8080  (Team tab: login admin / admin)"
echo "Press Ctrl+C to stop."
wait
