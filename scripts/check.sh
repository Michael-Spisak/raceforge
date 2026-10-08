#!/usr/bin/env sh
# Run every quality gate (same as CI). Usage: scripts/check.sh
set -e
cd "$(dirname "$0")/.."
# PYTHONPATH: Python >= 3.13 on macOS skips "hidden" .pth files that uv may create.
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run lint-imports
uv run pytest "$@"
# Frontend (spec 0008): lint, types, unit tests, build. E2E/Electron run in CI (see ci.yml).
if command -v npm >/dev/null 2>&1 && [ -d frontend/node_modules ]; then
  (cd frontend && npm run --silent check)
else
  echo "skipping frontend checks (run 'npm ci' in frontend/)"
fi
