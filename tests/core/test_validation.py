"""AC3: invalid data is rejected with clear messages."""

import pytest
from pydantic import ValidationError

from raceforge.core.assembly import (
    Assembly,
    AssemblyError,
    Connection,
    ConnectorPath,
    Submodel,
    SubmodelInstance,
)
from raceforge.core.ids import new_object_id
from raceforge.core.io import dump, load
from raceforge.core.meta import ObjectKind, ObjectMeta
from raceforge.core.parts import Part, PartSource
from raceforge.core.primitives import Quat, UnitVec3
from tests.core import factories


def test_unknown_fields_rejected() -> None:
    data = factories.beam().model_dump(mode="json", by_alias=True)
    data["colour"] = "red"
    with pytest.raises(ValidationError, match="colour"):
        load(data)


def test_negative_mass_rejected() -> None:
    with pytest.raises(ValidationError, match="mass_kg"):
        Part(source=PartSource.PRINTED, name="x", mass_kg=-1)


def test_quaternion_normalisation() -> None:
    q = Quat(w=1.0000004, x=0, y=0, z=0)
    assert q.w == 1.0
    with pytest.raises(ValidationError, match="unit length"):
        Quat(w=2, x=0, y=0, z=0)
    with pytest.raises(ValidationError, match="zero"):
        Quat(w=0, x=0, y=0, z=0)
    with pytest.raises(ValidationError, match="zero"):
        UnitVec3(x=0, y=0, z=0)


def test_bad_slug_rejected() -> None:
    from datetime import UTC, datetime

    with pytest.raises(ValidationError, match="slug"):
        ObjectMeta(
            id=new_object_id(),
            slug="Car A!",
            kind=ObjectKind.ASSEMBLY,
            workspace_id=new_object_id(),
            created_by="me",
            created_at=datetime.now(UTC),
        )


def test_lego_part_needs_ldraw_id_and_device_part_needs_device() -> None:
    with pytest.raises(ValidationError, match="ldraw_id"):
        Part(source=PartSource.LEGO_LDRAW, name="x")
    with pytest.raises(ValidationError, match="device block"):
        Part(source=PartSource.DEVICE, name="x")


def test_unresolved_connector_path_rejected() -> None:
    asm, _ = factories.assembly()
    data = asm.model_dump(mode="json", by_alias=True)
    data["connections"][0]["a"]["instances"] = ["left", "nope"]
    with pytest.raises(ValidationError, match="unresolved instance 'nope'"):
        load(data)
    data["connections"][0]["a"]["instances"] = ["left"]
    with pytest.raises(ValidationError, match="must end at a part"):
        load(data)


def test_cyclic_submodels_rejected() -> None:
    a = Submodel(id="a", name="A", items=[SubmodelInstance(id="to-b", submodel="b")])
    b = Submodel(id="b", name="B", items=[SubmodelInstance(id="to-a", submodel="a")])
    with pytest.raises(ValidationError, match="cycle"):
        Assembly(root="a", submodels={"a": a, "b": b})


def test_incompatible_connectors_rejected() -> None:
    asm, parts = factories.assembly()
    asm.validate_against_parts(parts)  # valid: pin into pin hole
    bad = asm.model_copy(
        update={
            "connections": [
                Connection(
                    a=ConnectorPath(instances=["left", "beam"], connector="h1"),
                    b=ConnectorPath(instances=["left", "beam"], connector="h2"),
                )
            ]
        }
    )
    with pytest.raises(AssemblyError, match="incompatible connectors"):
        bad.validate_against_parts(parts)
    missing = asm.model_copy(
        update={
            "connections": [
                Connection(
                    a=ConnectorPath(instances=["left", "beam"], connector="zz"),
                    b=ConnectorPath(instances=["left", "pin"], connector="a"),
                )
            ]
        }
    )
    with pytest.raises(AssemblyError, match="no connector 'zz'"):
        missing.validate_against_parts(parts)


def test_track_object_must_use_defined_class() -> None:
    data = factories.track().model_dump(mode="json", by_alias=True)
    data["objects"][0]["class_id"] = "ghost"
    with pytest.raises(ValidationError, match="undefined class"):
        load(data)


def test_runlog_race_setup_requires_track() -> None:
    data = factories.runlog().model_dump(mode="json", by_alias=True)
    data["track"] = None
    with pytest.raises(ValidationError, match="requires a track"):
        load(data)


def test_nan_rejected() -> None:
    text = dump(factories.frame()).replace('"speed_m_s":0.8', '"speed_m_s":NaN')
    with pytest.raises(ValidationError):
        load(text)
