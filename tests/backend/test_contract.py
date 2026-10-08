"""Spec 0006: the backend REST contract is snapshot-tested like the engine API (spec 0008 AC1)."""

import json
import os
from pathlib import Path

from tests.backend.conftest import Env

SNAPSHOT = Path(__file__).parents[1] / "snapshots" / "backend-openapi.json"


def test_backend_openapi_snapshot(env: Env) -> None:
    """Regenerate with UPDATE_SNAPSHOTS=1 only after an approved spec change."""
    current = json.dumps(env.client.get("/openapi.json").json(), indent=2, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        SNAPSHOT.write_text(current, encoding="utf-8")
    assert SNAPSHOT.read_text(encoding="utf-8") == current, "backend API contract changed"
