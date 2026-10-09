"""Assembly editing operations for the Construct editor (spec 0015).

Every operation takes an :class:`Assembly` and returns a new one (the UI keeps the undo stack).
Parts are addressed by their instance chain from the root submodel (``path``). Editing a part
inside a linked submodel changes every instance of that submodel (that is what "linked" means).
World-space moves and quarter turns are converted into the part's parent frame, so they work
through nested and mirrored submodels.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt

from raceforge.core.assembly import Assembly, Connection, ConnectorPath, PartInstance, Submodel
from raceforge.core.connectors import ConnectorType, Gender, are_compatible
from raceforge.core.frames import (
    instance_transform,
    iter_part_placements,
    matrix_to_quat,
    quat_to_matrix,
)
from raceforge.core.primitives import Pose, Quat, Vec3
from raceforge.parts.catalogue import Catalogue

type Arr = npt.NDArray[np.float64]
AxisName = Literal["x", "y", "z"]
SNAP_TOLERANCE_M = 0.006  # axis lines closer than this (after a move) snap together
DEFAULT_CONNECTOR_M = 0.008  # one stud along the axis when the part does not say
PARALLEL = 0.98  # |cos| between connector axes


class EditError(ValueError):
    pass


@dataclass(frozen=True)
class WorldConnector:
    id: str
    type: ConnectorType
    gender: Gender
    position: Arr
    axis: Arr
    length_m: float


@dataclass(frozen=True)
class EditorPart:
    path: tuple[str, ...]
    key: str
    name: str
    category: str
    color: int | None
    position: Arr
    rotation: Arr  # proper rotation (mirroring is folded into the geometry, see core.frames)
    mirrored: bool
    bbox_lo: Arr
    bbox_hi: Arr
    connectors: list[WorldConnector]
    linked: bool  # inside a submodel that is instanced more than once


def _parent_and_item(assembly: Assembly, path: list[str]) -> tuple[Submodel, PartInstance]:
    if not path:
        raise EditError("empty path")
    sub = assembly.submodels[assembly.root]
    for instance_id in path[:-1]:
        item = sub.item(instance_id)
        if item is None or item.kind != "submodel":
            raise EditError(f"no submodel instance {instance_id!r}")
        sub = assembly.submodels[item.submodel]
    item = sub.item(path[-1])
    if item is None or not isinstance(item, PartInstance):
        raise EditError(f"no part {path[-1]!r} in {sub.id!r}")
    return sub, item


def _with_item(
    assembly: Assembly, sub: Submodel, item: PartInstance | None, old_id: str
) -> Assembly:
    items = (
        [i for i in sub.items if i.id != old_id]
        if item is None
        else [item if i.id == old_id else i for i in sub.items]
    )
    submodels = {**assembly.submodels, sub.id: sub.model_copy(update={"items": items})}
    return assembly.model_copy(update={"submodels": submodels})


def _instance_counts(assembly: Assembly) -> dict[str, int]:
    counts: dict[str, int] = {assembly.root: 1}

    def walk(sub_id: str) -> None:
        for item in assembly.submodels[sub_id].items:
            if item.kind == "submodel":
                counts[item.submodel] = counts.get(item.submodel, 0) + 1
                walk(item.submodel)

    walk(assembly.root)
    return counts


def view(assembly: Assembly, cat: Catalogue) -> list[EditorPart]:
    """Every placed part with its world pose and world-space connectors."""
    counts = _instance_counts(assembly)
    out: list[EditorPart] = []
    for chain, inst, placement in iter_part_placements(assembly):
        key = cat.key_for_hash(inst.part.content_hash)
        entry, part = cat.entry(key), cat.part(key)
        transform = instance_transform(assembly, chain)
        r_full = np.array(transform.rotation)
        t = np.array(transform.translation)
        conns = [
            WorldConnector(
                id=c.id,
                type=c.type,
                gender=c.gender,
                position=r_full @ np.array(c.pose.position.as_tuple()) + t,
                axis=_unit(r_full @ np.array(c.axis.as_tuple())),
                length_m=c.length_m or DEFAULT_CONNECTOR_M,
            )
            for c in part.connectors
        ]
        parent_sub = assembly.root
        sub = assembly.submodels[assembly.root]
        for instance_id in chain[:-1]:
            item = sub.item(instance_id)
            assert item is not None and item.kind == "submodel"
            parent_sub = item.submodel
            sub = assembly.submodels[parent_sub]
        lo, hi = cat.bbox(key)
        out.append(
            EditorPart(
                path=tuple(chain),
                key=key,
                name=part.name,
                category=entry.category.value,
                color=inst.color,
                position=np.array(placement.pose.position.as_tuple()),
                rotation=np.array(quat_to_matrix(placement.pose.orientation)),
                mirrored=placement.mirrored,
                bbox_lo=np.array(lo),
                bbox_hi=np.array(hi),
                connectors=conns,
                linked=counts.get(parent_sub, 1) > 1,
            )
        )
    return out


def _unit(v: Arr) -> Arr:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def _parent_rotation(assembly: Assembly, path: list[str]) -> Arr:
    return (
        np.array(instance_transform(assembly, path[:-1]).rotation) if len(path) > 1 else np.eye(3)
    )


def move(assembly: Assembly, path: list[str], delta_world: tuple[float, float, float]) -> Assembly:
    """Translate a part by ``delta_world`` metres (world axes)."""
    sub, item = _parent_and_item(assembly, path)
    r_parent = _parent_rotation(assembly, path)
    d_local = r_parent.T @ np.array(delta_world)  # orthogonal (also when mirrored)
    p = item.pose.position
    pos = Vec3(x=p.x + float(d_local[0]), y=p.y + float(d_local[1]), z=p.z + float(d_local[2]))
    new = item.model_copy(update={"pose": Pose(position=pos, orientation=item.pose.orientation)})
    return _with_item(assembly, sub, new, item.id)


def _quat(m: Arr) -> Quat:
    r = [[float(v) for v in row] for row in m]
    return matrix_to_quat(
        ((r[0][0], r[0][1], r[0][2]), (r[1][0], r[1][1], r[1][2]), (r[2][0], r[2][1], r[2][2]))
    )


def _axis_rotation(axis: AxisName, quarter_turns: int) -> Arr:
    a = math.pi / 2 * quarter_turns
    c, s = round(math.cos(a)), round(math.sin(a))
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float64)
    if axis == "y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float64)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float64)


def rotate(assembly: Assembly, path: list[str], axis: AxisName, quarter_turns: int = 1) -> Assembly:
    """Turn a part about the world ``axis`` through its own origin (90° steps)."""
    sub, item = _parent_and_item(assembly, path)
    r_parent = _parent_rotation(assembly, path)
    rot_local = r_parent.T @ _axis_rotation(axis, quarter_turns) @ r_parent  # proper rotation
    r_local = np.array(quat_to_matrix(item.pose.orientation))
    new_r = rot_local @ r_local
    new = item.model_copy(
        update={"pose": Pose(position=item.pose.position, orientation=_quat(new_r))}
    )
    return _with_item(assembly, sub, new, item.id)


def _drop_connections(assembly: Assembly, path: list[str]) -> Assembly:
    """Remove connections (and their joint roles) that touch the instance at ``path``."""

    def touches(c: Connection) -> bool:
        return any(p.instances[: len(path)] == path for p in (c.a, c.b))

    keep = [i for i, c in enumerate(assembly.connections) if not touches(c)]
    index = {old: new for new, old in enumerate(keep)}
    joints = [
        j.model_copy(update={"connection": index[j.connection]})
        for j in assembly.joints
        if j.connection in index
    ]
    return assembly.model_copy(
        update={"connections": [assembly.connections[i] for i in keep], "joints": joints}
    )


def delete(assembly: Assembly, path: list[str]) -> Assembly:
    sub, item = _parent_and_item(assembly, path)
    return _drop_connections(_with_item(assembly, sub, None, item.id), path)


# ------------------------------------------------------------------ groups (spec 0015 part C)
def _origin(assembly: Assembly, path: list[str]) -> Arr:
    return np.array(instance_transform(assembly, path).translation)


def pivot_of(assembly: Assembly, paths: list[list[str]]) -> Arr:
    """Turning point of a selection: centre of the part origins, on the LDU grid."""
    centre = np.mean([_origin(assembly, p) for p in paths], axis=0)
    return np.round(centre / 0.0004) * 0.0004


def move_many(
    assembly: Assembly, paths: list[list[str]], delta_world: tuple[float, float, float]
) -> Assembly:
    for path in paths:
        assembly = move(assembly, path, delta_world)
    return assembly


def rotate_many(
    assembly: Assembly, paths: list[list[str]], axis: AxisName, quarter_turns: int = 1
) -> Assembly:
    """Turn a selection as one rigid group about its pivot (world axis, 90° steps)."""
    if len(paths) == 1:
        return rotate(assembly, paths[0], axis, quarter_turns)
    pivot = pivot_of(assembly, paths)
    rot = _axis_rotation(axis, quarter_turns)
    for path in paths:
        before = _origin(assembly, path)
        assembly = rotate(assembly, path, axis, quarter_turns)
        after = pivot + rot @ (before - pivot)
        d = after - before
        assembly = move(assembly, path, (float(d[0]), float(d[1]), float(d[2])))
    return assembly


def delete_many(assembly: Assembly, paths: list[list[str]]) -> Assembly:
    for path in sorted(paths, key=len, reverse=True):
        assembly = delete(assembly, path)
    return assembly


def _copy_into_root(
    assembly: Assembly,
    cat: Catalogue,
    paths: list[list[str]],
    place: Callable[[Arr, Arr], tuple[Arr, Arr]],
) -> tuple[Assembly, list[list[str]]]:
    """Copy parts into the root submodel at ``place(origin, rotation)`` (world, proper rotation)
    and copy the connections between them; returns the new paths in selection order."""
    placements = {tuple(c): pl for c, _, pl in iter_part_placements(assembly)}
    new_paths: list[list[str]] = []
    renamed: dict[tuple[str, ...], list[str]] = {}
    for path in paths:
        _, item = _parent_and_item(assembly, path)
        pl = placements[tuple(path)]
        origin = np.array(pl.pose.position.as_tuple())
        rotation = np.array(quat_to_matrix(pl.pose.orientation))
        pos, rot = place(origin, rotation)
        key = cat.key_for_hash(item.part.content_hash)
        assembly, new = add(
            assembly, cat, key, (float(pos[0]), float(pos[1]), float(pos[2])), item.color
        )
        sub, inst = _parent_and_item(assembly, new)
        inst = inst.model_copy(
            update={"pose": Pose(position=inst.pose.position, orientation=_quat(rot))}
        )
        assembly = _with_item(assembly, sub, inst, inst.id)
        renamed[tuple(path)] = new
        new_paths.append(new)
    copies: list[Connection] = []
    for c in assembly.connections:
        a, b = renamed.get(tuple(c.a.instances)), renamed.get(tuple(c.b.instances))
        if a is not None and b is not None:
            copies.append(
                Connection(
                    a=ConnectorPath(instances=a, connector=c.a.connector),
                    b=ConnectorPath(instances=b, connector=c.b.connector),
                )
            )
    if copies:
        assembly = assembly.model_copy(update={"connections": [*assembly.connections, *copies]})
    return assembly, new_paths


def duplicate(
    assembly: Assembly,
    cat: Catalogue,
    paths: list[list[str]],
    offset: tuple[float, float, float],
) -> tuple[Assembly, list[list[str]]]:
    """Copies of the selection (with their connections), shifted by ``offset`` metres."""
    d = np.array(offset)
    return _copy_into_root(assembly, cat, paths, lambda o, r: (o + d, r))


def mirror_copy(
    assembly: Assembly, cat: Catalogue, paths: list[list[str]], axis: AxisName = "y"
) -> tuple[Assembly, list[list[str]]]:
    """Copies of the selection mirrored across the plane normal to ``axis`` through the origin
    (left ↔ right for ``y``). The rotation is conjugated (M·R·M), which is exact for parts that are
    symmetric to their own mirror plane; handed parts may need turning afterwards."""
    m = np.diag(
        [-1.0 if axis == "x" else 1.0, -1.0 if axis == "y" else 1.0, -1.0 if axis == "z" else 1.0]
    )
    return _copy_into_root(assembly, cat, paths, lambda o, r: (m @ o, m @ r @ m))


def add(
    assembly: Assembly,
    cat: Catalogue,
    key: str,
    position: tuple[float, float, float],
    color: int | None = None,
) -> tuple[Assembly, list[str]]:
    """Add catalogue part ``key`` to the root submodel at a world position; returns its path."""
    cat.entry(key)  # KeyError for unknown parts
    root = assembly.submodels[assembly.root]
    used = {i.id for s in assembly.submodels.values() for i in s.items}
    base = "".join(ch if ch.isalnum() else "-" for ch in key.lower()).strip("-") or "part"
    n = 1
    while f"{base}-{n}" in used:
        n += 1
    inst = PartInstance(
        id=f"{base}-{n}",
        part=cat.ref(key),
        pose=Pose(position=Vec3(x=position[0], y=position[1], z=position[2])),
        color=color,
    )
    submodels = {
        **assembly.submodels,
        root.id: root.model_copy(update={"items": [*root.items, inst]}),
    }
    return assembly.model_copy(update={"submodels": submodels}), [inst.id]


@dataclass(frozen=True)
class SnapResult:
    assembly: Assembly
    snapped: bool
    connector: str | None = None
    target: tuple[str, ...] | None = None
    target_connector: str | None = None
    distance_m: float = 0.0


def snap(
    assembly: Assembly, cat: Catalogue, path: list[str], tolerance_m: float = SNAP_TOLERANCE_M
) -> SnapResult:
    """Quick snap: if a connector of the part is within ``tolerance_m`` of a compatible connector
    of another part with a parallel axis, move the part so they coincide and record the
    connection."""
    parts = view(assembly, cat)
    me = next((p for p in parts if list(p.path) == path), None)
    if me is None:
        raise EditError(f"no part at {path}")
    # Axial connectors (pins, axles, holes, studs) engage when their axis lines coincide and they
    # overlap along the axis; the snap removes only the sideways offset.
    best: tuple[float, float, WorldConnector, EditorPart, WorldConnector, Arr] | None = None
    for other in parts:
        if other.path == me.path:
            continue
        for a in me.connectors:
            for b in other.connectors:
                if not are_compatible(a.type, b.type) or abs(float(a.axis @ b.axis)) < PARALLEL:
                    continue
                gap = b.position - a.position
                along = float(gap @ a.axis)
                perp = gap - along * a.axis
                side = float(np.linalg.norm(perp))
                if side > tolerance_m or abs(along) > (a.length_m + b.length_m) / 2 + tolerance_m:
                    continue
                if best is None or (side, abs(along)) < (best[0], best[1]):
                    best = (side, abs(along), a, other, b, perp)
    if best is None:
        return SnapResult(assembly, False)
    d, _, a, other, b, delta = best
    moved = move(assembly, path, (float(delta[0]), float(delta[1]), float(delta[2])))
    conn = Connection(
        a=ConnectorPath(instances=path, connector=a.id),
        b=ConnectorPath(instances=list(other.path), connector=b.id),
    )
    exists = any(
        {(tuple(c.a.instances), c.a.connector), (tuple(c.b.instances), c.b.connector)}
        == {
            (tuple(conn.a.instances), conn.a.connector),
            (tuple(conn.b.instances), conn.b.connector),
        }
        for c in moved.connections
    )
    if not exists:
        moved = moved.model_copy(update={"connections": [*moved.connections, conn]})
    return SnapResult(moved, True, a.id, other.path, b.id, d)


# ------------------------------------------------------------------ attach (spec 0015 part D)
def _rotation_from_to(u: Arr, v: Arr) -> Arr:
    """Proper rotation turning unit vector ``u`` onto unit vector ``v``."""
    c = float(u @ v)
    if c > 1 - 1e-9:
        return np.eye(3)
    if c < -1 + 1e-9:
        # 180°: about any axis perpendicular to u
        perp = np.cross(u, [1.0, 0.0, 0.0])
        if np.linalg.norm(perp) < 1e-6:
            perp = np.cross(u, [0.0, 1.0, 0.0])
        k = perp / np.linalg.norm(perp)
        return 2 * np.outer(k, k) - np.eye(3)
    w = np.cross(u, v)
    k = np.array([[0, -w[2], w[1]], [w[2], 0, -w[0]], [-w[1], w[0], 0]])
    return np.eye(3) + k + k @ k / (1 + c)


@dataclass(frozen=True)
class AttachCandidate:
    connector: str  # of the new part
    target_connector: str
    rotation: Arr
    position: Arr


def attach_candidates(
    assembly: Assembly, cat: Catalogue, key: str, target: list[str]
) -> list[AttachCandidate]:
    """Ways to put catalogue part ``key`` onto a free connector of the part at ``target``: each
    compatible connector pair, both directions along the axis; the connectors meet at one point."""
    parts = {p.path: p for p in view(assembly, cat)}
    me = parts.get(tuple(target))
    if me is None:
        raise EditError(f"no part at {target}")
    used = {(tuple(p.instances), p.connector) for c in assembly.connections for p in (c.a, c.b)}
    new_part = cat.part(key)
    out: list[AttachCandidate] = []
    for b in me.connectors:
        if (tuple(target), b.id) in used:
            continue
        for a in new_part.connectors:
            if not are_compatible(a.type, b.type):
                continue
            a_pos = np.array(a.pose.position.as_tuple())
            a_axis = _unit(np.array(a.axis.as_tuple()))
            for sign in (1.0, -1.0):
                rot = _rotation_from_to(a_axis, sign * b.axis)
                out.append(AttachCandidate(a.id, b.id, rot, b.position - rot @ a_pos))
    return out


def attach(
    assembly: Assembly, cat: Catalogue, key: str, target: list[str], candidate: int = 0
) -> tuple[Assembly, list[str], int]:
    """Add ``key`` docked onto ``target`` (candidate ``candidate`` modulo the number of candidates);
    returns the assembly, the new part's path and the number of candidates (0: nothing fits)."""
    options = attach_candidates(assembly, cat, key, target)
    if not options:
        return assembly, [], 0
    c = options[candidate % len(options)]
    assembly, path = add(
        assembly, cat, key, (float(c.position[0]), float(c.position[1]), float(c.position[2]))
    )
    sub, inst = _parent_and_item(assembly, path)
    inst = inst.model_copy(
        update={"pose": Pose(position=inst.pose.position, orientation=_quat(c.rotation))}
    )
    assembly = _with_item(assembly, sub, inst, inst.id)
    conn = Connection(
        a=ConnectorPath(instances=path, connector=c.connector),
        b=ConnectorPath(instances=target, connector=c.target_connector),
    )
    assembly = assembly.model_copy(update={"connections": [*assembly.connections, conn]})
    return assembly, path, len(options)
