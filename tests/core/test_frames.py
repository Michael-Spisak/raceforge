"""AC6: instance resolution (linked + mirrored) and LDraw conversion."""

import math

import pytest
from hypothesis import given, settings

from raceforge.core.frames import (
    Transform,
    core_to_ldraw_transform,
    det,
    iter_part_placements,
    ldraw_point_to_core,
    ldraw_to_core_transform,
    matrix_to_quat,
    quat_to_matrix,
    world_pose,
)
from raceforge.core.primitives import Quat
from tests.core import factories, strategies


def close(a: tuple[float, ...], b: tuple[float, ...], tol: float = 1e-9) -> bool:
    return all(math.isclose(x, y, abs_tol=tol) for x, y in zip(a, b, strict=True))


def test_linked_and_mirrored_instances() -> None:
    asm, _ = factories.assembly()
    left = world_pose(asm, ["left", "pin"])
    right = world_pose(asm, ["right", "pin"])
    assert close(left.pose.position.as_tuple(), (0.0, 0.07, 0.0))
    assert not left.mirrored
    # Right side is the left side mirrored across the XZ plane: pin at y = -0.05 - 0.02.
    assert close(right.pose.position.as_tuple(), (0.0, -0.07, 0.0))
    assert right.mirrored
    placements = iter_part_placements(asm)
    assert len(placements) == 4
    assert sum(p.mirrored for _, _, p in placements) == 2


def test_resolution_errors() -> None:
    asm, _ = factories.assembly()
    with pytest.raises(ValueError, match="unresolved"):
        world_pose(asm, ["left", "nope"])


@settings(max_examples=100, deadline=None)
@given(strategies.quats())
def test_quaternion_matrix_roundtrip(q: Quat) -> None:
    m = quat_to_matrix(q)
    assert math.isclose(det(m), 1.0, abs_tol=1e-9)
    b = matrix_to_quat(m)
    back = (b.w, b.x, b.y, b.z)
    orig = (q.w, q.x, q.y, q.z)
    # q and -q describe the same rotation.
    assert close(back, orig, 1e-6) or close(back, tuple(-c for c in orig), 1e-6)


def test_ldraw_up_is_core_z() -> None:
    # LDraw "up" is -Y; 1 brick height = 24 LDU = 9.6 mm.
    assert close(ldraw_point_to_core((0.0, -24.0, 0.0)), (0.0, 0.0, 0.0096))
    assert close(ldraw_point_to_core((20.0, 0.0, 0.0)), (0.008, 0.0, 0.0))


@settings(max_examples=50, deadline=None)
@given(strategies.quats(), strategies.vec3)
def test_ldraw_transform_roundtrip(q: Quat, v: object) -> None:
    from raceforge.core.primitives import Vec3

    assert isinstance(v, Vec3)
    ld_rot = quat_to_matrix(q)
    ld_t = v.as_tuple()
    core = ldraw_to_core_transform(ld_rot, ld_t)
    assert isinstance(core, Transform)
    assert math.isclose(det(core.rotation), 1.0, abs_tol=1e-9)
    rot_back, t_back = core_to_ldraw_transform(core)
    assert all(close(a, b, 1e-9) for a, b in zip(rot_back, ld_rot, strict=True))
    assert close(t_back, ld_t, 1e-6 * max(1.0, max(abs(c) for c in ld_t)))
