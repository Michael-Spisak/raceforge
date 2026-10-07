"""Assembly -> MuJoCo MJCF (spec 0002).

Bodies are rigid groups of parts (connected components of the connection graph without joint-role
connections). Components with a steering arm become knuckles, components with a tyre become wheels,
everything else is merged into the chassis. Steering: two knuckle hinges coupled by a polynomial
equality (Ackermann blend), a position servo on the left knuckle and a limited "play" hinge
per side.
Drive: DC-motor-like `general` actuators (torque = stall*u - stall/w0 * speed) on a 50/50 tendon
(differential) or on the wheel joint (locked / single axle body).
"""

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import numpy as np

from raceforge.construct.derive import (
    Mat,
    PlacedPart,
    ackermann_outer,
    derive,
    mass_properties,
    placed_parts,
)
from raceforge.construct.vehicle import VehicleSpec
from raceforge.core.assembly import Assembly, JointKind
from raceforge.parts.catalogue import Catalogue, Category

TIMESTEP = 0.002
COLLIDE_CATEGORIES = {
    Category.BEAM,
    Category.EV3_BRICK,
    Category.MOTOR,
    Category.SENSOR,
    Category.BOARD,
    Category.BATTERY,
    Category.STEERING_ARM,
    Category.DIFFERENTIAL,
}
SENSE_CATEGORIES = {Category.SENSOR}


def _f(v: float) -> str:
    s = f"{v:.6g}"
    return "0" if s in ("-0", "0", "-0.0") else s


def _vec(v: Mat | tuple[float, ...]) -> str:
    return " ".join(_f(float(x)) for x in v)


@dataclass
class _Body:
    name: str
    kind: str  # chassis | knuckle | wheel
    parts: list[PlacedPart] = field(default_factory=list[PlacedPart])


@dataclass(frozen=True)
class ModelInfo:
    """Names in the generated model, for SimIO and tests."""

    steer_actuator: str
    steer_joint_left: str
    steer_joint_right: str
    play_joints: tuple[str, str]
    drive_actuators: tuple[str, ...]
    wheel_joints: dict[str, str]  # wheel body -> joint
    sensor_sites: dict[str, str]  # part instance path -> site name
    max_steer_rad: float
    steer_rate_rad_s: float


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[tuple[str, ...], tuple[str, ...]] = {}

    def find(self, a: tuple[str, ...]) -> tuple[str, ...]:
        self.parent.setdefault(a, a)
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def union(self, a: tuple[str, ...], b: tuple[str, ...]) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def build_mjcf(assembly: Assembly, cat: Catalogue, spec: VehicleSpec) -> tuple[str, ModelInfo]:
    """Return deterministic MJCF XML and the names needed to drive the model."""
    parts = placed_parts(assembly, cat)
    by_path = {p.path: p for p in parts}
    roles = {j.connection: j.role for j in assembly.joints}
    uf = _UnionFind()
    for p in parts:
        uf.find(p.path)
    for n, conn in enumerate(assembly.connections):
        if n not in roles:
            uf.union(tuple(conn.a.instances), tuple(conn.b.instances))

    comps: dict[tuple[str, ...], list[PlacedPart]] = {}
    for p in parts:
        comps.setdefault(uf.find(p.path), []).append(p)

    def kind_of(members: list[PlacedPart]) -> str:
        cats = {m.category for m in members}
        if Category.TYRE in cats:
            return "wheel"
        if Category.STEERING_ARM in cats:
            return "knuckle"
        return "chassis"

    chassis = _Body("chassis", "chassis")
    knuckles: dict[tuple[str, ...], _Body] = {}
    wheels: dict[tuple[str, ...], _Body] = {}
    for root, members in sorted(comps.items()):
        k = kind_of(members)
        if k == "chassis":
            chassis.parts.extend(members)
        elif k == "knuckle":
            knuckles[root] = _Body("", "knuckle", members)
        else:
            wheels[root] = _Body("", "wheel", members)

    # Name knuckles/wheels by position.
    def center(body: _Body, cat_filter: Category | None = None) -> Mat:
        ps = [p for p in body.parts if cat_filter is None or p.category is cat_filter] or body.parts
        return np.mean([p.center for p in ps], axis=0)

    for kb in knuckles.values():
        kb.name = "knuckle_left" if center(kb)[1] > 0 else "knuckle_right"
    tyre_x = sorted({round(float(center(w, Category.TYRE)[0]), 4) for w in wheels.values()})
    front_x = max(tyre_x)
    for wb in wheels.values():
        c = center(wb, Category.TYRE)
        tyres = [p for p in wb.parts if p.category is Category.TYRE]
        pos = "front" if abs(c[0] - front_x) < 1e-3 and len(tyre_x) > 1 else "rear"
        side = "" if len(tyres) > 1 else ("_left" if c[1] > 0 else "_right")
        wb.name = f"wheel_{pos}{side}"

    # Joints between components.
    def comp_of(path: tuple[str, ...]) -> tuple[str, ...]:
        return uf.find(path)

    knuckle_pivot: dict[str, tuple[Mat, Mat]] = {}
    wheel_parent: dict[str, str] = {}
    wheel_axis: dict[str, Mat] = {}
    for n, conn in enumerate(assembly.connections):
        role = roles.get(n)
        if role is None:
            continue
        a, b = tuple(conn.a.instances), tuple(conn.b.instances)
        ca, cb = comp_of(a), comp_of(b)
        if role is JointKind.STEERING_PIVOT:
            for k_root, other, path, cid in (
                (ca, cb, a, conn.a.connector),
                (cb, ca, b, conn.b.connector),
            ):
                if k_root in knuckles and other not in knuckles and other not in wheels:
                    pos, axis = _connector_world(by_path[path], cat, cid)
                    knuckle_pivot.setdefault(knuckles[k_root].name, (pos, axis))
        elif role is JointKind.WHEEL_AXLE:
            for w_root, other, path, cid in (
                (ca, cb, a, conn.a.connector),
                (cb, ca, b, conn.b.connector),
            ):
                if w_root not in wheels:
                    continue
                name = wheels[w_root].name
                parent = knuckles[other].name if other in knuckles else "chassis"
                if other in wheels:
                    continue
                if name not in wheel_parent or (
                    parent != "chassis" and wheel_parent[name] == "chassis"
                ):
                    wheel_parent[name] = parent
                    wheel_axis[name] = _connector_world(by_path[path], cat, cid)[1]

    for wb in wheels.values():
        wheel_parent.setdefault(wb.name, "chassis")
        wheel_axis.setdefault(wb.name, np.array([0.0, 1.0, 0.0]))

    if len(knuckles) != 2 or len(knuckle_pivot) != 2:
        raise ValueError("expected exactly two steering knuckles with pivots")

    derived = derive(assembly, cat, spec)
    return _emit(
        assembly,
        cat,
        spec,
        chassis,
        knuckles,
        wheels,
        knuckle_pivot,
        wheel_parent,
        wheel_axis,
        derived,
    )


def _connector_world(part: PlacedPart, cat: Catalogue, connector_id: str) -> tuple[Mat, Mat]:
    conn = cat.part(part.key).connector(connector_id)
    assert conn is not None
    pos = part.origin + part.rotation @ np.array(conn.pose.position.as_tuple())
    axis = part.rotation @ np.array(conn.axis.as_tuple())
    return pos, axis / np.linalg.norm(axis)


def _inertial(
    el: ET.Element, parts: list[PlacedPart], frame_origin: Mat, min_mass: float = 1e-4
) -> None:
    if parts:
        m, cog, inertia = mass_properties(parts)
    else:
        m, cog, inertia = min_mass, frame_origin, np.eye(3) * 1e-8
    i = inertia + np.eye(3) * 1e-9
    ET.SubElement(
        el,
        "inertial",
        pos=_vec(cog - frame_origin),
        mass=_f(max(m, min_mass)),
        fullinertia=_vec((i[0, 0], i[1, 1], i[2, 2], i[0, 1], i[0, 2], i[1, 2])),
    )


def _box_geoms(el: ET.Element, parts: list[PlacedPart], frame_origin: Mat) -> None:
    for p in sorted(parts, key=lambda q: q.path):
        if p.category not in COLLIDE_CATEGORIES:
            continue
        q = _rot_to_quat(p.rotation)
        ET.SubElement(
            el,
            "geom",
            type="box",
            pos=_vec(p.center - frame_origin),
            quat=_vec(q),
            size=_vec(np.maximum(p.size / 2, 1e-4)),
            contype="2",
            conaffinity="1",
            group="1",
            rgba="0.6 0.6 0.65 1",
        )


def _rot_to_quat(m: Mat) -> tuple[float, float, float, float]:
    from raceforge.core.frames import matrix_to_quat

    q = matrix_to_quat(
        ((m[0, 0], m[0, 1], m[0, 2]), (m[1, 0], m[1, 1], m[1, 2]), (m[2, 0], m[2, 1], m[2, 2]))
    )
    return (q.w, q.x, q.y, q.z)


def _ackermann_poly(spec: VehicleSpec, wheelbase: float, track: float) -> list[float]:
    """Polynomial right(left) for MuJoCo joint equality (degree 4)."""
    lim = spec.max_steer_rad + spec.steering_play_rad
    xs = np.linspace(-lim, lim, 81)

    def right(left: float) -> float:
        if left >= 0:  # left turn: left wheel is inner, right is outer
            return ackermann_outer(left, wheelbase, track, spec.ackermann_pct)
        # right turn: left is outer -> solve inner(right) numerically
        target = abs(left)
        lo, hi = 0.0, math.pi / 2 - 1e-3
        for _ in range(60):
            mid = (lo + hi) / 2
            if abs(ackermann_outer(mid, wheelbase, track, spec.ackermann_pct)) < target:
                lo = mid
            else:
                hi = mid
        return -(lo + hi) / 2

    ys = np.array([right(float(x)) for x in xs])
    coeffs = np.polynomial.polynomial.polyfit(xs, ys, 4)
    return [float(c) for c in coeffs]


def _emit(
    assembly: Assembly,
    cat: Catalogue,
    spec: VehicleSpec,
    chassis: _Body,
    knuckles: dict[tuple[str, ...], _Body],
    wheels: dict[tuple[str, ...], _Body],
    pivots: dict[str, tuple[Mat, Mat]],
    wheel_parent: dict[str, str],
    wheel_axis: dict[str, Mat],
    derived: object,
) -> tuple[str, ModelInfo]:
    from raceforge.construct.derive import DerivedData

    assert isinstance(derived, DerivedData)
    root = ET.Element("mujoco", model="raceforge_car")
    ET.SubElement(root, "compiler", angle="radian", inertiafromgeom="false", autolimits="true")
    ET.SubElement(root, "option", timestep=_f(TIMESTEP), integrator="implicitfast")
    default = ET.SubElement(root, "default")
    ET.SubElement(default, "joint", damping="0.0005", armature="0.0001")
    asset = ET.SubElement(root, "asset")
    ET.SubElement(
        asset,
        "texture",
        name="grid",
        type="2d",
        builtin="checker",
        width="256",
        height="256",
        rgb1="0.85 0.85 0.85",
        rgb2="0.75 0.75 0.75",
    )
    ET.SubElement(asset, "material", name="floor", texture="grid", texrepeat="20 20")
    world = ET.SubElement(root, "worldbody")
    ET.SubElement(world, "light", pos="0 0 3", dir="0 0 -1", directional="true")
    ET.SubElement(
        world,
        "geom",
        name="floor",
        type="plane",
        size="25 25 0.1",
        material="floor",
        contype="1",
        conaffinity="6",
        friction=_vec((spec.tyre_friction, 0.005, 0.0001)),
    )

    origin = np.zeros(3)
    car = ET.SubElement(world, "body", name="chassis", pos="0 0 0.002")
    ET.SubElement(car, "freejoint", name="root")
    _inertial(car, chassis.parts, origin)
    _box_geoms(car, chassis.parts, origin)
    ET.SubElement(car, "site", name="imu", pos=_vec(np.array(derived.cog_m)), size="0.004")

    sensor_sites: dict[str, str] = {}
    for p in sorted(chassis.parts, key=lambda q: q.path):
        if p.category in SENSE_CATEGORIES:
            entry = cat.entry(p.key)
            site = "site_" + p.path[-1].replace("-", "_")
            attrs = {"name": site, "pos": _vec(p.center), "size": "0.003"}
            if entry.sense_axis_ld is not None:
                from raceforge.core.frames import ldraw_point_to_core

                d = np.array(ldraw_point_to_core(entry.sense_axis_ld))
                d = p.rotation @ (d / np.linalg.norm(d))
                attrs["zaxis"] = _vec(d)
            ET.SubElement(car, "site", attrib=attrs)
            sensor_sites["/".join(p.path)] = site

    play = spec.steering_play_rad / 2
    lim = spec.max_steer_rad
    knuckle_els: dict[str, ET.Element] = {}
    for kb in sorted(knuckles.values(), key=lambda b: b.name):
        pos, axis = pivots[kb.name]
        side = kb.name.split("_")[1]
        axis = axis if axis[2] >= 0 else -axis
        cmd = ET.SubElement(car, "body", name=f"steer_cmd_{side}", pos=_vec(pos))
        _inertial(cmd, [], pos)
        ET.SubElement(
            cmd,
            "joint",
            name=f"steer_{side}",
            type="hinge",
            axis=_vec(axis),
            range=_vec((-lim - 0.2, lim + 0.2)),
        )
        body = ET.SubElement(cmd, "body", name=kb.name)
        ET.SubElement(
            body,
            "joint",
            name=f"play_{side}",
            type="hinge",
            axis=_vec(axis),
            range=_vec((-play - 1e-6, play + 1e-6)),
            damping="0.002",
        )
        _inertial(body, kb.parts, pos)
        _box_geoms(body, kb.parts, pos)
        knuckle_els[kb.name] = body
        knuckle_els[f"_origin_{kb.name}"] = cmd  # keeps pivot position

    wheel_joints: dict[str, str] = {}
    for wb in sorted(wheels.values(), key=lambda b: b.name):
        parent_name = wheel_parent[wb.name]
        parent_el = car if parent_name == "chassis" else knuckle_els[parent_name]
        parent_origin = origin if parent_name == "chassis" else pivots[parent_name][0]
        tyres = sorted((p for p in wb.parts if p.category is Category.TYRE), key=lambda q: q.path)
        c = np.mean([t.center for t in tyres], axis=0)
        axis = wheel_axis[wb.name]
        axis = axis if axis[1] >= 0 else -axis
        body = ET.SubElement(parent_el, "body", name=wb.name, pos=_vec(c - parent_origin))
        jname = f"{wb.name}_spin"
        ET.SubElement(body, "joint", name=jname, type="hinge", axis=_vec(axis), damping="0.0002")
        _inertial(body, wb.parts, c)
        for t in tyres:
            radius = float(t.size.max() / 2)
            half_w = float(t.size.min() / 2)
            ET.SubElement(
                body,
                "geom",
                type="cylinder",
                pos=_vec(t.center - c),
                zaxis=_vec(axis),
                size=_vec((radius, half_w)),
                contype="4",
                conaffinity="1",
                condim="6",
                friction=_vec((spec.tyre_friction, 0.005, 0.0001)),
                rgba="0.1 0.1 0.1 1",
            )
        wheel_joints[wb.name] = jname

    # Equalities: Ackermann coupling, locked axles.
    equality = ET.SubElement(root, "equality")
    poly = _ackermann_poly(spec, derived.wheelbase_m, derived.track_m)
    ET.SubElement(
        equality, "joint", joint1="steer_right", joint2="steer_left", polycoef=_vec(tuple(poly))
    )

    tendon = ET.SubElement(root, "tendon")
    actuator = ET.SubElement(root, "actuator")
    sm = spec.steering_motor
    ET.SubElement(
        actuator,
        "position",
        name="steer",
        joint="steer_left",
        kp=_f(sm.stall_torque_nm * 4),
        kv=_f(sm.stall_torque_nm * 0.05),
        ctrlrange=_vec((-lim, lim)),
        forcerange=_vec((-sm.stall_torque_nm, sm.stall_torque_nm)),
    )
    drive_names: list[str] = []
    for n, d in enumerate(spec.drives):
        driven = sorted(
            (
                w
                for w in wheels.values()
                if abs(
                    float(
                        np.mean([p.center for p in w.parts if p.category is Category.TYRE], axis=0)[
                            0
                        ]
                    )
                    - d.axle_x_m
                )
                < 0.004
            ),
            key=lambda w: w.name,
        )
        if not driven:
            raise ValueError(f"no wheels found at drive axle x={d.axle_x_m:.3f} m")
        stall, w0 = d.motor.stall_torque_nm, d.motor.no_load_speed_rad_s
        name = f"drive_{n}"
        common = {
            "name": name,
            "gear": _f(d.gear_ratio),
            "ctrlrange": "-1 1",
            "dyntype": "none",
            "gaintype": "fixed",
            "biastype": "affine",
            "gainprm": _f(stall),
            "biasprm": _vec((0, 0, -stall / w0)),
        }
        if len(driven) == 1:
            ET.SubElement(
                actuator, "general", attrib={"joint": wheel_joints[driven[0].name], **common}
            )
        elif d.differential:
            t = ET.SubElement(tendon, "fixed", name=f"diff_{n}")
            for w in driven:
                ET.SubElement(t, "joint", joint=wheel_joints[w.name], coef="0.5")
            ET.SubElement(actuator, "general", attrib={"tendon": f"diff_{n}", **common})
        else:
            ET.SubElement(
                equality,
                "joint",
                joint1=wheel_joints[driven[1].name],
                joint2=wheel_joints[driven[0].name],
                polycoef="0 1 0 0 0",
            )
            ET.SubElement(
                actuator, "general", attrib={"joint": wheel_joints[driven[0].name], **common}
            )
        drive_names.append(name)

    sensor = ET.SubElement(root, "sensor")
    ET.SubElement(sensor, "gyro", name="imu_gyro", site="imu")
    ET.SubElement(sensor, "jointpos", name="steer_pos", joint="steer_left")
    for jname in sorted(wheel_joints.values()):
        ET.SubElement(sensor, "jointvel", name=f"{jname}_vel", joint=jname)

    ET.indent(root, space="  ")
    xml = ET.tostring(root, encoding="unicode") + "\n"
    info = ModelInfo(
        steer_actuator="steer",
        steer_joint_left="steer_left",
        steer_joint_right="steer_right",
        play_joints=("play_left", "play_right"),
        drive_actuators=tuple(drive_names),
        wheel_joints=wheel_joints,
        sensor_sites=sensor_sites,
        max_steer_rad=lim,
        steer_rate_rad_s=sm.no_load_speed_rad_s,
    )
    return xml, info


class RateLimiter:
    """Limits how fast a commanded value may change (models the steering motor speed)."""

    def __init__(self, max_rate: float, value: float = 0.0) -> None:
        self.max_rate = max_rate
        self.value = value

    def step(self, target: float, dt: float) -> float:
        delta = max(-self.max_rate * dt, min(self.max_rate * dt, target - self.value))
        self.value += delta
        return self.value
