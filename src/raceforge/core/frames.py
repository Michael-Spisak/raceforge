"""Frame maths (pure Python): transforms with mirroring, instance resolution, LDraw conversion.

Conventions (spec 0001): right-handed, Z-up, SI units. LDraw uses -Y up and LDU (0.4 mm); the
mapping core = C @ ldraw with C = [[1,0,0],[0,0,1],[0,-1,0]] is a proper rotation
(no handedness flip). A mirrored part is reported as a proper pose plus ``mirrored=True``;
its geometry is the part mirrored
across its own local XZ plane (Y flipped), i.e. world_matrix = R_proper @ diag(1, -1, 1).
"""

import math
from dataclasses import dataclass

from raceforge.core.assembly import Assembly, Axis, PartInstance
from raceforge.core.primitives import Pose, Quat, Vec3

type Vec = tuple[float, float, float]
type Mat3 = tuple[Vec, Vec, Vec]

LDU_M = 0.0004
IDENTITY: Mat3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
_LDRAW_TO_CORE: Mat3 = ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0))
_FLIP_Y: Mat3 = ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0))


def matmul(a: Mat3, b: Mat3) -> Mat3:
    rows = tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3)
    )
    return (rows[0], rows[1], rows[2])  # type: ignore[return-value]


def transpose(m: Mat3) -> Mat3:
    return ((m[0][0], m[1][0], m[2][0]), (m[0][1], m[1][1], m[2][1]), (m[0][2], m[1][2], m[2][2]))


def apply(m: Mat3, v: Vec) -> Vec:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def det(m: Mat3) -> float:
    return (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )


def quat_to_matrix(q: Quat) -> Mat3:
    w, x, y, z = q.w, q.x, q.y, q.z
    return (
        (1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)),
        (2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)),
        (2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)),
    )


def matrix_to_quat(m: Mat3) -> Quat:
    """Proper rotation matrix -> unit quaternion with w >= 0 (Shepperd's method)."""
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        w, x, y, z = (
            0.25 * s,
            (m[2][1] - m[1][2]) / s,
            (m[0][2] - m[2][0]) / s,
            (m[1][0] - m[0][1]) / s,
        )
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2
        w, x, y, z = (
            (m[2][1] - m[1][2]) / s,
            0.25 * s,
            (m[0][1] + m[1][0]) / s,
            (m[0][2] + m[2][0]) / s,
        )
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2
        w, x, y, z = (
            (m[0][2] - m[2][0]) / s,
            (m[0][1] + m[1][0]) / s,
            0.25 * s,
            (m[1][2] + m[2][1]) / s,
        )
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2
        w, x, y, z = (
            (m[1][0] - m[0][1]) / s,
            (m[0][2] + m[2][0]) / s,
            (m[1][2] + m[2][1]) / s,
            0.25 * s,
        )
    if w < 0:
        w, x, y, z = -w, -x, -y, -z
    n = math.sqrt(w * w + x * x + y * y + z * z)
    return Quat(w=w / n, x=x / n, y=y / n, z=z / n)


def mirror_matrix(axis: Axis) -> Mat3:
    index = {Axis.X: 0, Axis.Y: 1, Axis.Z: 2}[axis]
    rows = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    rows[index][index] = -1.0
    return (
        (rows[0][0], rows[0][1], rows[0][2]),
        (rows[1][0], rows[1][1], rows[1][2]),
        (rows[2][0], rows[2][1], rows[2][2]),
    )


@dataclass(frozen=True)
class Transform:
    """Affine transform x -> R x + t; R may be improper (mirroring)."""

    rotation: Mat3 = IDENTITY
    translation: Vec = (0.0, 0.0, 0.0)

    @staticmethod
    def from_pose(pose: Pose) -> "Transform":
        return Transform(quat_to_matrix(pose.orientation), pose.position.as_tuple())

    def compose(self, inner: "Transform") -> "Transform":
        """self ∘ inner (apply inner first)."""
        t = apply(self.rotation, inner.translation)
        return Transform(
            matmul(self.rotation, inner.rotation),
            (t[0] + self.translation[0], t[1] + self.translation[1], t[2] + self.translation[2]),
        )

    def apply_point(self, p: Vec) -> Vec:
        r = apply(self.rotation, p)
        return (r[0] + self.translation[0], r[1] + self.translation[1], r[2] + self.translation[2])

    @property
    def mirrored(self) -> bool:
        return det(self.rotation) < 0


@dataclass(frozen=True)
class Placement:
    """World pose of a part instance; ``mirrored``: part geometry is mirrored (module doc)."""

    pose: Pose
    mirrored: bool


def to_placement(transform: Transform) -> Placement:
    rotation = transform.rotation
    mirrored = transform.mirrored
    if mirrored:
        rotation = matmul(rotation, _FLIP_Y)
    x, y, z = transform.translation
    return Placement(
        Pose(position=Vec3(x=x, y=y, z=z), orientation=matrix_to_quat(rotation)), mirrored
    )


def instance_transform(assembly: Assembly, instances: list[str]) -> Transform:
    """Compose the transforms along an instance chain starting in the root submodel."""
    total = Transform()
    current = assembly.submodels[assembly.root]
    for depth, instance_id in enumerate(instances):
        item = current.item(instance_id)
        if item is None:
            raise ValueError(f"unresolved instance {instance_id!r} in {current.id!r}")
        local = Transform.from_pose(item.pose)
        if isinstance(item, PartInstance):
            if depth != len(instances) - 1:
                raise ValueError(f"{instance_id!r} is a part, cannot descend")
            return total.compose(local)
        if item.mirrored is not None:
            local = local.compose(Transform(mirror_matrix(item.mirrored)))
        total = total.compose(local)
        current = assembly.submodels[item.submodel]
    return total


def world_pose(assembly: Assembly, instances: list[str]) -> Placement:
    """World placement of the part instance at the end of ``instances``."""
    return to_placement(instance_transform(assembly, instances))


def iter_part_placements(assembly: Assembly) -> list[tuple[list[str], PartInstance, Placement]]:
    """All part instances in the assembly with their world placement (linked instances expanded)."""
    out: list[tuple[list[str], PartInstance, Placement]] = []

    def walk(sub_id: str, chain: list[str], parent: Transform) -> None:
        for item in assembly.submodels[sub_id].items:
            local = Transform.from_pose(item.pose)
            if isinstance(item, PartInstance):
                out.append(([*chain, item.id], item, to_placement(parent.compose(local))))
            else:
                if item.mirrored is not None:
                    local = local.compose(Transform(mirror_matrix(item.mirrored)))
                walk(item.submodel, [*chain, item.id], parent.compose(local))

    walk(assembly.root, [], Transform())
    return out


def ldraw_point_to_core(p: Vec) -> Vec:
    x, y, z = apply(_LDRAW_TO_CORE, p)
    return (x * LDU_M, y * LDU_M, z * LDU_M)


def core_point_to_ldraw(p: Vec) -> Vec:
    x, y, z = apply(transpose(_LDRAW_TO_CORE), p)
    return (x / LDU_M, y / LDU_M, z / LDU_M)


def ldraw_to_core_transform(rotation: Mat3, translation_ldu: Vec) -> Transform:
    """Convert an LDraw placement (3x3 matrix + LDU offset) into a core transform."""
    c = _LDRAW_TO_CORE
    return Transform(
        matmul(matmul(c, rotation), transpose(c)), ldraw_point_to_core(translation_ldu)
    )


def core_to_ldraw_transform(transform: Transform) -> tuple[Mat3, Vec]:
    c = _LDRAW_TO_CORE
    rotation = matmul(matmul(transpose(c), transform.rotation), c)
    return rotation, core_point_to_ldraw(transform.translation)
