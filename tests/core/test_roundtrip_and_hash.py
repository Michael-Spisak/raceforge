"""AC1 (round trip), AC2 (content hash), AC4 (migration), AC5 (schema snapshots)."""

import json
import os
from pathlib import Path
from typing import Literal

import pytest
from hypothesis import given, settings
from pydantic import Field

from raceforge.core import registry
from raceforge.core.assembly import Assembly
from raceforge.core.base import CoreModel
from raceforge.core.io import (
    content_hash,
    dump,
    dump_yaml,
    export_json_schemas,
    json_schemas,
    load,
    load_as,
    load_yaml,
)
from raceforge.core.parts import Part
from raceforge.core.telemetry import TelemetryFrame
from raceforge.core.track import Track
from tests.core import factories, strategies

SNAPSHOTS = Path(__file__).parent.parent / "snapshots" / "schemas"


def _roundtrip(model: CoreModel) -> None:
    assert load(dump(model)) == model
    assert load(json.loads(dump(model))) == model
    assert load_yaml(dump_yaml(model)) == model


@pytest.mark.parametrize(
    "model",
    [
        factories.beam(),
        factories.ultrasonic(),
        factories.assembly()[0],
        factories.track(),
        factories.frame(3),
        factories.runlog(),
    ],
    ids=["part", "device-part", "assembly", "track", "telemetry", "runlog"],
)
def test_fixture_roundtrip(model: CoreModel) -> None:
    _roundtrip(model)


@settings(max_examples=40, deadline=None)
@given(strategies.parts())
def test_part_roundtrip_property(part: Part) -> None:
    _roundtrip(part)


@settings(max_examples=40, deadline=None)
@given(strategies.assemblies())
def test_assembly_roundtrip_property(asm: Assembly) -> None:
    _roundtrip(asm)


@settings(max_examples=40, deadline=None)
@given(strategies.tracks())
def test_track_roundtrip_property(track: Track) -> None:
    _roundtrip(track)


@settings(max_examples=40, deadline=None)
@given(strategies.frames)
def test_telemetry_roundtrip_property(frame: TelemetryFrame) -> None:
    _roundtrip(frame)


def test_content_hash_is_stable_and_order_independent() -> None:
    part = factories.beam()
    h = content_hash(part)
    assert h == content_hash(factories.beam())
    shuffled = dict(reversed(list(json.loads(dump(part)).items())))
    assert content_hash(load_as(Part, shuffled)) == h
    # Golden value guards cross-OS/Python stability of canonical JSON.
    assert dump(part).startswith('{"connectors":[')


def test_content_hash_changes_on_any_field_change() -> None:
    part = factories.beam()
    assert content_hash(part.model_copy(update={"name": "Beam 7 (old)"})) != content_hash(part)
    assert content_hash(part.model_copy(update={"mass_kg": 0.0027})) != content_hash(part)


@pytest.mark.parametrize("text", ["\x85", "a\u2028b", "tab\there", "Größe ✓", "line\nbreak"])
def test_yaml_preserves_special_strings(text: str) -> None:
    frame = factories.frame().model_copy(update={"channels": {"note": text}})
    assert load_yaml(dump_yaml(frame)) == frame


def test_load_as_rejects_wrong_type() -> None:
    with pytest.raises(TypeError):
        load_as(Track, dump(factories.beam()))


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register test-only documents without leaking them into other tests."""
    monkeypatch.setattr(registry, "_documents", dict(registry._documents))  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(registry, "_current", dict(registry._current))  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(registry, "_migrations", dict(registry._migrations))  # pyright: ignore[reportPrivateUsage]


@pytest.mark.usefixtures("isolated_registry")
def test_migration_framework_upgrades_old_documents() -> None:
    @registry.register_document("dummy", 2)
    class DummyV2(CoreModel):
        schema_: Literal["dummy"] = Field(default="dummy", alias="schema")
        schema_version: Literal[2] = 2
        full_name: str

    @registry.register_migration("dummy", 1)
    def _v1_to_v2(data: registry.JsonDict) -> registry.JsonDict:
        data["full_name"] = data.pop("name")
        data["schema_version"] = 2
        return data

    loaded = load({"schema": "dummy", "schema_version": 1, "name": "x"})
    assert isinstance(loaded, DummyV2)
    assert loaded.full_name == "x"
    with pytest.raises(ValueError, match="newer"):
        load({"schema": "dummy", "schema_version": 3, "full_name": "x"})
    with pytest.raises(ValueError, match="no migration"):
        load({"schema": "dummy", "schema_version": 0, "name": "x"})
    with pytest.raises(ValueError, match="already registered"):
        registry.register_migration("dummy", 1)(_v1_to_v2)


def test_unknown_schema_rejected() -> None:
    with pytest.raises(ValueError, match="unknown document schema"):
        load({"schema": "nope", "schema_version": 1})


def test_load_rejects_missing_schema() -> None:
    with pytest.raises(ValueError, match="schema"):
        load({"name": "x"})


def test_json_schema_snapshots(tmp_path: Path) -> None:
    """Fails on any contract change.

    Regenerate with UPDATE_SNAPSHOTS=1 only after an approved spec change.
    """
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        export_json_schemas(SNAPSHOTS)
    written = export_json_schemas(tmp_path)
    names = {p.name for p in written}
    assert {"part.v1.schema.json", "assembly.v1.schema.json", "track.v1.schema.json"} <= names
    for path in written:
        snapshot = SNAPSHOTS / path.name
        assert snapshot.exists(), f"missing snapshot {snapshot.name} (run with UPDATE_SNAPSHOTS=1)"
        assert path.read_text() == snapshot.read_text(), f"contract changed: {path.name}"
    assert set(json_schemas()) >= {"telemetry.v1", "runlog.v1"}
