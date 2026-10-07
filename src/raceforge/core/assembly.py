"""Car assembly: a tree of submodels with part/submodel instances, connections and joints."""

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, PositiveFloat, model_validator

from raceforge.core.base import CoreModel
from raceforge.core.connectors import ConnectorType, are_compatible
from raceforge.core.parts import Part
from raceforge.core.primitives import LocalId, Pose, Slug, Vec3, VersionRef
from raceforge.core.registry import register_document


class Axis(StrEnum):
    X = "x"
    Y = "y"
    Z = "z"


class SubmodelRole(StrEnum):
    STEERING = "steering"
    DRIVE = "drive"
    CHASSIS = "chassis"
    SENSOR_MAST = "sensor_mast"
    ELECTRONICS = "electronics"
    OTHER = "other"


class JointKind(StrEnum):
    STEERING_PIVOT = "steering_pivot"
    WHEEL_AXLE = "wheel_axle"
    DRIVE_MOTOR = "drive_motor"
    STEERING_MOTOR = "steering_motor"
    GEAR_MESH = "gear_mesh"


class PartInstance(CoreModel):
    kind: Literal["part"] = "part"
    id: LocalId
    part: VersionRef
    pose: Pose = Field(default_factory=Pose)
    color: int | None = Field(default=None, ge=0)


class SubmodelInstance(CoreModel):
    """Linked instance of another submodel.

    ``mirrored`` reflects it across the plane normal to the given axis.
    """

    kind: Literal["submodel"] = "submodel"
    id: LocalId
    submodel: LocalId
    pose: Pose = Field(default_factory=Pose)
    mirrored: Axis | None = None


Item = Annotated[PartInstance | SubmodelInstance, Field(discriminator="kind")]


class Submodel(CoreModel):
    id: LocalId
    name: str = Field(min_length=1, max_length=200)
    role: SubmodelRole | None = None
    items: list[Item] = Field(default_factory=list[Item])

    def item(self, instance_id: str) -> PartInstance | SubmodelInstance | None:
        return next((i for i in self.items if i.id == instance_id), None)


class ConnectorPath(CoreModel):
    """Instance chain from the root submodel to a part instance, plus the connector id."""

    instances: list[LocalId] = Field(min_length=1)
    connector: LocalId


class Connection(CoreModel):
    a: ConnectorPath
    b: ConnectorPath


class JointRole(CoreModel):
    connection: int = Field(ge=0)
    role: JointKind
    gear_ratio: PositiveFloat | None = None


class MeasuredOverrides(CoreModel):
    mass_kg: PositiveFloat | None = None
    cog: Vec3 | None = None


class AssemblyError(ValueError):
    """Raised when an assembly is inconsistent with its parts."""


@register_document("assembly", 1)
class Assembly(CoreModel):
    schema_: Literal["assembly"] = Field(default="assembly", alias="schema")
    schema_version: Literal[1] = 1
    root: LocalId
    submodels: dict[LocalId, Submodel]
    connections: list[Connection] = Field(default_factory=list[Connection])
    joints: list[JointRole] = Field(default_factory=list[JointRole])
    rules: Slug | None = None
    measured: MeasuredOverrides | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.root not in self.submodels:
            raise ValueError(f"root submodel {self.root!r} not defined")
        for key, sub in self.submodels.items():
            if key != sub.id:
                raise ValueError(f"submodel key {key!r} does not match id {sub.id!r}")
            ids = [i.id for i in sub.items]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate instance ids in submodel {sub.id!r}")
            for item in sub.items:
                if isinstance(item, SubmodelInstance) and item.submodel not in self.submodels:
                    raise ValueError(
                        f"{sub.id}/{item.id} references unknown submodel {item.submodel!r}"
                    )
        self._check_acyclic()
        for n, conn in enumerate(self.connections):
            for path in (conn.a, conn.b):
                self.resolve(path, context=f"connection {n}")
        for joint in self.joints:
            if joint.connection >= len(self.connections):
                raise ValueError(f"joint references missing connection {joint.connection}")
        return self

    def _check_acyclic(self) -> None:
        state: dict[str, int] = {}  # 1 = visiting, 2 = done

        def visit(sid: str, trail: tuple[str, ...]) -> None:
            if state.get(sid) == 2:
                return
            if state.get(sid) == 1:
                raise ValueError("submodel cycle: " + " -> ".join((*trail, sid)))
            state[sid] = 1
            for item in self.submodels[sid].items:
                if isinstance(item, SubmodelInstance):
                    visit(item.submodel, (*trail, sid))
            state[sid] = 2

        for sid in self.submodels:
            visit(sid, ())

    def resolve(self, path: ConnectorPath, context: str = "path") -> PartInstance:
        """Walk an instance chain from the root; return the final part instance."""
        current = self.submodels[self.root]
        for depth, instance_id in enumerate(path.instances):
            item = current.item(instance_id)
            if item is None:
                raise ValueError(
                    f"{context}: unresolved instance {instance_id!r} in {current.id!r}"
                )
            last = depth == len(path.instances) - 1
            if isinstance(item, PartInstance):
                if not last:
                    raise ValueError(f"{context}: {instance_id!r} is a part, cannot descend")
                return item
            if last:
                raise ValueError(f"{context}: path must end at a part instance")
            current = self.submodels[item.submodel]
        raise AssertionError("unreachable")  # pragma: no cover

    def validate_against_parts(self, parts: Mapping[str, Part]) -> None:
        """Check connector ids and type compatibility. ``parts`` maps content_hash -> Part."""
        problems: list[str] = []
        for n, conn in enumerate(self.connections):
            types: list[ConnectorType] = []
            for path in (conn.a, conn.b):
                inst = self.resolve(path, context=f"connection {n}")
                part = parts.get(inst.part.content_hash)
                if part is None:
                    problems.append(
                        f"connection {n}: part {inst.part.content_hash[:12]} not provided"
                    )
                    continue
                connector = part.connector(path.connector)
                if connector is None:
                    problems.append(
                        f"connection {n}: part {part.name!r} has no connector {path.connector!r}"
                    )
                    continue
                types.append(connector.type)
            if len(types) == 2 and not are_compatible(types[0], types[1]):
                problems.append(
                    f"connection {n}: incompatible connectors {types[0]} <-> {types[1]}"
                )
        if problems:
            raise AssemblyError("; ".join(problems))
