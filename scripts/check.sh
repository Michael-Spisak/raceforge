#!/usr/bin/env sh
# Run every quality gate (same as CI). Usage: scripts/check.sh
set -e
cd "$(dirname "$0")/.."
# PYTHONPATH: Python >= 3.13 on macOS skips "hidden" .pth files that uv may create.
export PYTHONPATH="$PWD/src:$PWD/ev3_side${PYTHONPATH:+:$PYTHONPATH}"
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run lint-imports
uv run pytest "$@"
