"""Parts (LEGO, printed, devices) and their connectors (spec 0001)."""

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, PositiveFloat, StringConstraints, model_validator

from raceforge.core.base import CoreModel
from raceforge.core.connectors import ConnectorType, Gender
from raceforge.core.devices import Device
from raceforge.core.primitives import BlobRef, LocalId, Money, Pose, UnitVec3
from raceforge.core.registry import register_document


class PartSource(StrEnum):
    LEGO_LDRAW = "lego_ldraw"
    PRINTED = "printed"
    DEVICE = "device"
    OTHER = "other"


class MaterialKind(StrEnum):
    PLA = "PLA"
    PETG = "PETG"
    TPU = "TPU"
    ABS = "ABS"
    OTHER = "other"


class Material(CoreModel):
    kind: MaterialKind
    density_kg_m3: PositiveFloat
    infill: float = Field(ge=0, le=1)


class PriceInfo(CoreModel):
    price: Money
    source_url: str | None = None
    observed_on: date


class Connector(CoreModel):
    id: LocalId
    type: ConnectorType
    pose: Pose = Field(default_factory=Pose)
    axis: UnitVec3 = Field(default_factory=lambda: UnitVec3(x=0.0, y=0.0, z=1.0))
    gender: Gender
    length_m: PositiveFloat | None = None


@register_document("part", 1)
class Part(CoreModel):
    schema_: Literal["part"] = Field(default="part", alias="schema")
    schema_version: Literal[1] = 1
    source: PartSource
    ldraw_id: Annotated[str, StringConstraints(max_length=64)] | None = None
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    mass_kg: PositiveFloat | None = None
    mass_measured_kg: PositiveFloat | None = None
    material: Material | None = None
    price: PriceInfo | None = None
    mesh_ref: BlobRef | None = None
    connectors: list[Connector] = Field(default_factory=list[Connector])
    device: Device | None = None
    verified: bool = False

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids = [c.id for c in self.connectors]
        if len(ids) != len(set(ids)):
            raise ValueError("connector ids must be unique within a part")
        if self.source is PartSource.DEVICE and self.device is None:
            raise ValueError("parts with source 'device' need a device block")
        if self.source is PartSource.LEGO_LDRAW and not self.ldraw_id:
            raise ValueError("LEGO parts need an ldraw_id")
        return self

    def connector(self, connector_id: str) -> Connector | None:
        return next((c for c in self.connectors if c.id == connector_id), None)
