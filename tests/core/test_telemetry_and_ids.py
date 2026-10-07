"""AC7: telemetry channel limits; UUIDv7 ids."""

import uuid

import pytest
from pydantic import ValidationError

from raceforge.core.ids import new_object_id
from raceforge.core.telemetry import MAX_CHANNELS, TelemetryFrame
from tests.core import factories


def test_channel_key_pattern_enforced() -> None:
    data = factories.frame().model_dump(mode="json", by_alias=True)
    data["channels"] = {"Bad Key": 1}
    with pytest.raises(ValidationError):
        TelemetryFrame.model_validate(data)


def test_channel_count_limit() -> None:
    data = factories.frame().model_dump(mode="json", by_alias=True)
    data["channels"] = {f"c{n}": n for n in range(MAX_CHANNELS + 1)}
    with pytest.raises(ValidationError, match="64"):
        TelemetryFrame.model_validate(data)
    data["channels"] = {f"c{n}": n for n in range(MAX_CHANNELS)}
    TelemetryFrame.model_validate(data)


def test_channel_value_types_preserved() -> None:
    f = factories.frame()
    assert f.channels["debug.flag"] is True
    assert isinstance(f.channels["pid.error"], float)


def test_uuid7_format_and_ordering() -> None:
    a = new_object_id(timestamp_ms=1_000)
    b = new_object_id(timestamp_ms=2_000)
    assert uuid.UUID(a).version == 7
    assert a < b
    with pytest.raises(ValueError):
        new_object_id(timestamp_ms=-1)


def test_timestamp_wall_time() -> None:
    f = factories.frame(seq=2)
    assert f.t.wall_ns == f.t.mono_ns + (f.t.wall_offset_ns or 0)
