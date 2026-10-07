"""Spec 0005: the Rust logger embeds the TelemetryFrame JSON Schema; it must match core."""

import json
from pathlib import Path

from raceforge.core.io import json_schemas

SCHEMA = Path(__file__).parents[2] / "car_runtime/rf-log/schemas/telemetry.v1.schema.json"


def test_rust_logger_schema_matches_core() -> None:
    expected = json.dumps(json_schemas()["telemetry.v1"], indent=2, sort_keys=True) + "\n"
    assert SCHEMA.read_text(encoding="utf-8") == expected, (
        "regenerate with: python -c 'from raceforge.core.io import export_json_schemas; "
        "export_json_schemas(...)' and copy telemetry.v1.schema.json to car_runtime/rf-log/schemas/"
    )
