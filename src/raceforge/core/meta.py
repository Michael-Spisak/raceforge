"""Envelope metadata for versioned objects (spec 0001)."""

from enum import StrEnum

from pydantic import AwareDatetime, Field

from raceforge.core.base import CoreModel
from raceforge.core.primitives import ObjectId, Slug, VersionRef


class ObjectKind(StrEnum):
    PART = "part"
    ASSEMBLY = "assembly"
    TRACK = "track"
    CONTROLLER = "controller"
    DATASET = "dataset"
    RUN = "run"
    BUNDLE = "bundle"
    CAPTURE = "capture"
    MODEL = "model"


class ObjectMeta(CoreModel):
    id: ObjectId
    slug: Slug
    kind: ObjectKind
    workspace_id: ObjectId
    created_by: str = Field(min_length=1)
    created_at: AwareDatetime
    tags: list[str] = Field(default_factory=list[str])


class VersionMeta(CoreModel):
    ref: VersionRef
    name: str | None = None
    parent_versions: list[VersionRef] = Field(default_factory=list[VersionRef])
    message: str = ""
    author: str = Field(min_length=1)
    created_at: AwareDatetime
