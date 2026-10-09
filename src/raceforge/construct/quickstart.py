"""Parametric quick-start: parameters -> buildable LEGO car as a core Assembly (spec 0002).

Templates are Python functions per layout (RWD/AWD/FWD, always LEGO Ackermann steering in front).
Dimensions are grid-snapped: 1 stud = 8 mm. World frame: X forward, Y left, Z up;
rear axle at x = 0, ground at z = 0.
"""

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from raceforge.core.assembly import (
    Assembly,
    Connection,
    ConnectorPath,
    Item,
    JointKind,
    JointRole,
    PartInstance,
    Submodel,
    SubmodelInstance,
    SubmodelRole,
)
from raceforge.core.frames import apply, matrix_to_quat, quat_to_matrix
from raceforge.core.primitives import Pose, Quat, Vec3
from raceforge.parts.catalogue import Catalogue, Category

if TYPE_CHECKING:
    from raceforge.construct.vehicle import VehicleSpec

STUD = 0.008
AXLES = {
    3: "4519",
    4: "3705",
    5: "32073",
    6: "3706",
    7: "44294",
    8: "3707",
    9: "60485",
    10: "3737",
    12: "3708",
}
BEAMS = {3: "32523", 5: "32316", 7: "32524", 9: "40490", 11: "32525", 13: "41239", 15: "32278"}
GEARS = {12: "32270", 20: "32269", 36: "32498"}
WHEELS = {"56908+55976": ("56908", "55976")}
DIFF_GEARS = ("12-28", "20-28")
LOCKED_GEARS = ("12-20", "12-36", "20-12", "36-12")

type V3 = tuple[float, float, float]


class Layout(StrEnum):
    RWD = "rwd"
    AWD = "awd"
    FWD = "fwd"


class SensorSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["ev3_ultrasonic", "ev3_gyro", "ev3_touch", "lidar_2d"]
    preset: Literal["front", "left", "right", "rear", "center", "top"]
    offset_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)


def _default_sensors() -> list[SensorSpec]:
    return [
        SensorSpec(kind="ev3_ultrasonic", preset="front"),
        SensorSpec(kind="ev3_ultrasonic", preset="left"),
        SensorSpec(kind="ev3_ultrasonic", preset="right"),
    ]


class QuickStartParams(BaseModel):
    """Inputs of the quick-start generator (spec 0002); invalid values list valid ones."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    layout: Layout = Layout.RWD
    wheelbase_studs: int = 15
    track_studs: int = 11
    wheel: str = "56908+55976"
    differential: bool = True
    drive_motor: Literal["ev3_large", "ev3_medium", "dc_motor"] = "ev3_large"
    drive_gears: str | None = None  # default depends on `differential`
    steering_motor: Literal["ev3_medium", "servo"] = "ev3_medium"
    max_steer_deg: float = Field(default=30.0, ge=15.0, le=40.0)
    ackermann_pct: float = Field(default=60.0, ge=0.0, le=100.0)
    steering_play_deg: float = Field(default=3.0, ge=0.0, le=15.0)
    sensors: list[SensorSpec] = Field(default_factory=_default_sensors)
    board: Literal["raspberry_pi_5", "orange_pi_5", "none"] = "raspberry_pi_5"
    battery: Literal["powerbank", "none"] = "powerbank"
    measured_mass_kg: float | None = Field(default=None, gt=0)
    measured_cog_mm: tuple[float, float, float] | None = None

    @model_validator(mode="after")
    def _check(self) -> "QuickStartParams":
        _choose("wheelbase_studs", self.wheelbase_studs, list(range(11, 22)))
        _choose("track_studs", self.track_studs, list(range(9, 16)))
        _choose("wheel", self.wheel, list(WHEELS))
        options = DIFF_GEARS if self.differential else LOCKED_GEARS
        if self.drive_gears is not None:
            _choose("drive_gears", self.drive_gears, list(options))
        return self

    @property
    def gears(self) -> str:
        return self.drive_gears or ("12-28" if self.differential else "12-20")

    @property
    def gear_ratio(self) -> float:
        """Motor revolutions per wheel revolution."""
        driver, driven = (int(t) for t in self.gears.split("-"))
        return driven / driver


def _choose[T: (int, str)](name: str, value: T, valid: list[T]) -> None:
    if value in valid:
        return
    if isinstance(value, int):
        nums = [v for v in valid if isinstance(v, int)]
        nearest = sorted(nums, key=lambda v: abs(v - value))[:2]
        hint = f"nearest valid: {sorted(nearest)} (range {min(nums)}-{max(nums)})"
    else:
        hint = f"valid: {valid}"
    raise ValueError(f"{name}={value!r} is not possible with this template; {hint}")


def rot_z(deg: float) -> Quat:
    h = math.radians(deg) / 2
    return Quat(w=math.cos(h), x=0.0, y=0.0, z=math.sin(h))


def _add(a: V3, b: V3) -> V3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a: V3, b: V3) -> V3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(a: V3, k: float) -> V3:
    return (a[0] * k, a[1] * k, a[2] * k)


def _dot(a: V3, b: V3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: V3, b: V3) -> V3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a: V3) -> float:
    return math.sqrt(_dot(a, a))


def _align(u: V3, v: V3) -> Quat:
    """Shortest-arc rotation that turns unit vector ``u`` into unit vector ``v``."""
    c, d = _cross(u, v), _dot(u, v)
    if d < -1 + 1e-9:  # opposite: half turn about any axis perpendicular to u
        axis = _cross(u, (0.0, 0.0, 1.0) if abs(u[2]) < 0.9 else (1.0, 0.0, 0.0))
        n = _norm(axis)
        return Quat(w=0.0, x=axis[0] / n, y=axis[1] / n, z=axis[2] / n)
    w = 1 + d
    n = math.sqrt(w * w + _dot(c, c))
    return Quat(w=w / n, x=c[0] / n, y=c[1] / n, z=c[2] / n)


# Standing beam: its length runs up (core Z), its holes point sideways (core Y).
POST = matrix_to_quat(((0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)))
X_AXIS: V3 = (1.0, 0.0, 0.0)


@dataclass
class _Module:
    id: str
    name: str
    role: SubmodelRole
    items: list[Item] = field(default_factory=list[Item])


class _Builder:
    """Places catalogue parts in world coordinates and records connections and joint roles.

    Joints between axial connectors (pins, axles and their holes) are built from the world pose of
    the hole they go into, so connected connectors always share one axis line.
    """

    def __init__(self, cat: Catalogue) -> None:
        self.cat = cat
        self.modules: dict[str, _Module] = {}
        self.where: dict[str, str] = {}  # instance id -> module id
        self.poses: dict[str, tuple[str, V3, Quat]] = {}  # instance id -> (key, origin, rotation)
        self.connections: list[Connection] = []
        self.joints: list[JointRole] = []
        self.counter: dict[str, int] = {}

    def module(self, mid: str, name: str, role: SubmodelRole) -> None:
        self.modules[mid] = _Module(mid, name, role)

    def place(
        self, mid: str, key: str, center: V3, rot: Quat | None = None, name: str | None = None
    ) -> str:
        """Place part ``key`` so that its bounding-box centre lands on ``center``."""
        q = rot or Quat()
        lo, hi = self.cat.bbox(key)
        local_center = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2)
        origin = _sub(center, apply(quat_to_matrix(q), local_center))
        return self._put(mid, key, origin, q, name)

    def place_at(
        self, mid: str, key: str, connector: str, target: V3, rot: Quat | None = None
    ) -> str:
        """Place part ``key`` so that its connector ``connector`` lands on ``target``."""
        q = rot or Quat()
        conn = self.cat.part(key).connector(connector)
        assert conn is not None, f"{key} has no connector {connector}"
        origin = _sub(target, apply(quat_to_matrix(q), conn.pose.position.as_tuple()))
        return self._put(mid, key, origin, q, None)

    def _put(self, mid: str, key: str, origin: V3, q: Quat, name: str | None) -> str:
        cat_entry = self.cat.entry(key)
        base = name or f"{cat_entry.category.value}"
        n = self.counter.get(base, 0) + 1
        self.counter[base] = n
        iid = f"{base}-{n}".replace("_", "-")
        pose = Pose(position=Vec3(x=origin[0], y=origin[1], z=origin[2]), orientation=q)
        self.modules[mid].items.append(PartInstance(id=iid, part=self.cat.ref(key), pose=pose))
        self.where[iid] = mid
        self.poses[iid] = (key, origin, q)
        return iid

    def world(self, iid: str, connector: str) -> tuple[V3, V3]:
        """World position and axis of connector ``connector`` of instance ``iid``."""
        key, origin, q = self.poses[iid]
        conn = self.cat.part(key).connector(connector)
        assert conn is not None, f"{key} has no connector {connector}"
        m = quat_to_matrix(q)
        return _add(origin, apply(m, conn.pose.position.as_tuple())), apply(m, conn.axis.as_tuple())

    def top(self, iid: str) -> float:
        """World height of the top of an instance placed with a rotation about Z only."""
        key, origin, _ = self.poses[iid]
        return origin[2] + self.cat.bbox(key)[1][2]

    def connect(self, a: str, ac: str, b: str, bc: str, role: JointKind | None = None) -> None:
        conn = Connection(
            a=ConnectorPath(instances=[self.where[a], a], connector=ac),
            b=ConnectorPath(instances=[self.where[b], b], connector=bc),
        )
        self.connections.append(conn)
        if role is not None:
            self.joints.append(JointRole(connection=len(self.connections) - 1, role=role))

    def pin(
        self, mid: str, a: tuple[str, str], b: tuple[str, str], role_b: JointKind | None = None
    ) -> str:
        """Join two coaxial holes one stud apart with a friction pin (``e1`` in ``a``, ``e2`` in
        ``b``)."""
        pa, axis = self.world(*a)
        pb, _ = self.world(*b)
        d = _sub(pb, pa)
        assert abs(_norm(d) - STUD) < 1e-9 and _norm(_cross(d, axis)) < 1e-9, (a, b)
        p = self.place_at(mid, "2780", "e1", pa, _align(X_AXIS, _scale(d, 1 / STUD)))
        self.connect(p, "e1", *a)
        self.connect(b[0], b[1], p, "e2", role_b)
        return p

    def axle_through(self, mid: str, holes: list[tuple[str, str]]) -> str:
        """Put the shortest catalogue axle through the coaxial ``holes``; each hole is recorded
        against the nearest axle connector (``e1``, ``mid`` or ``e2``)."""
        p0, axis = self.world(*holes[0])
        ts: list[float] = []
        for h in holes:
            p, ax = self.world(*h)
            t = _dot(_sub(p, p0), axis)
            assert _norm(_cross(ax, axis)) < 1e-9, h
            assert _norm(_sub(p, _add(p0, _scale(axis, t)))) < 1e-9, h
            ts.append(t)
        lo, hi = min(ts), max(ts)
        n, key = _axle_key(round((hi - lo) / STUD, 6) + 1)
        mid_t = (lo + hi) / 2
        axle = self.place_at(mid, key, "mid", _add(p0, _scale(axis, mid_t)), _align(X_AXIS, axis))
        half = (n - 1) * STUD / 2
        for h, t in zip(holes, ts, strict=True):
            ends = (("e1", -half), ("mid", 0.0), ("e2", half))
            cid = min(ends, key=lambda e: abs(e[1] - (t - mid_t)))[0]
            self.connect(axle, cid, *h)
        return axle

    def assembly(self) -> Assembly:
        submodels = {
            m.id: Submodel(id=m.id, name=m.name, role=m.role, items=list(m.items))
            for m in self.modules.values()
        }
        root_items: list[Item] = [
            SubmodelInstance(id=m.id, submodel=m.id) for m in self.modules.values()
        ]
        submodels["car"] = Submodel(id="car", name="Quick-start car", items=root_items)
        return Assembly(
            root="car", submodels=submodels, connections=self.connections, joints=self.joints
        )


def _axle_key(min_studs: float) -> tuple[int, str]:
    for n in sorted(AXLES):
        if n >= min_studs:
            return n, AXLES[n]
    raise ValueError(f"no axle long enough ({min_studs:.1f} studs)")


def _beam_key(min_holes: int) -> tuple[int, str]:
    for n in sorted(BEAMS):
        if n >= min_holes:
            return n, BEAMS[n]
    raise ValueError(f"no beam with {min_holes} holes")


@dataclass(frozen=True)
class QuickStartResult:
    assembly: Assembly
    params: QuickStartParams
    wheel_radius_m: float
    wheel_width_m: float


def _free_hole(preferred: float, used: set[int], lo: int, hi: int) -> int:
    """Rail hole index nearest to ``preferred`` that is not in ``used`` (and mark it used)."""
    x = min((i for i in range(lo, hi + 1) if i not in used), key=lambda i: (abs(i - preferred), i))
    used.add(x)
    return x


def _body_back(cat: Catalogue, key: str) -> Quat:
    """Turn about Z that lays a motor along X with its output forward and its body behind."""
    lo, hi = cat.bbox(key)
    return rot_z(90) if lo[1] + hi[1] > 0 else rot_z(-90)


def generate(params: QuickStartParams, cat: Catalogue | None = None) -> QuickStartResult:
    """Build the quick-start car described by ``params``."""
    cat = cat or Catalogue.load()
    b = _Builder(cat)
    rim_key, tyre_key = WHEELS[params.wheel]
    tyre = cat.entry(tyre_key).wheel
    assert tyre is not None
    r, w = tyre.radius_mm / 1000, tyre.width_mm / 1000
    wb, tr = params.wheelbase_studs, params.track_studs
    front_x = wb * STUD
    wheel_y = tr * STUD / 2
    rail_y = math.floor((wheel_y - w / 2 - 0.012) / (STUD / 2)) * (STUD / 2)
    if rail_y < STUD:
        raise ValueError("track too narrow for the wheel width")

    for mid, name, role in (
        ("chassis", "Chassis", SubmodelRole.CHASSIS),
        ("steering", "Front steering", SubmodelRole.STEERING),
        ("drive", "Drive", SubmodelRole.DRIVE),
        ("electronics", "Electronics", SubmodelRole.ELECTRONICS),
        ("sensors", "Sensors", SubmodelRole.SENSOR_MAST),
    ):
        b.module(mid, name, role)

    # --- Chassis rails: two standing beams along X (holes along Y at axle height z = r) with
    # pin holes from x = -1 stud to x = wb + 1 studs. Long rails get a second beam beside the first
    # (inside unless the rails are too close together), pinned at hole x = 12.
    rails: dict[int, list[tuple[str, int]]] = {}  # side -> [(beam id, x index of hole h1)]
    holes_needed = wb + 3
    ext_y = rail_y - STUD if rail_y >= 2 * STUD else rail_y + STUD
    for side in (1, -1):
        if holes_needed <= 15:
            _, key = _beam_key(holes_needed)
            rid = b.place_at("chassis", key, "h1", (-STUD, side * rail_y, r), rot_z(-90))
            rails[side] = [(rid, -1)]
        else:
            rid = b.place_at("chassis", BEAMS[15], "h1", (-STUD, side * rail_y, r), rot_z(-90))
            _, key2 = _beam_key(wb - 10)
            rid2 = b.place_at("chassis", key2, "h1", (12 * STUD, side * ext_y, r), rot_z(-90))
            b.pin("chassis", (rid, "h14"), (rid2, "h1"))
            rails[side] = [(rid, -1), (rid2, 12)]

    def rail_hole(side: int, x_index: int) -> tuple[str, str]:
        for rid, start in reversed(rails[side]):
            if x_index >= start:
                return rid, f"h{x_index - start + 1}"
        raise ValueError("hole outside rails")

    # Rail holes taken by axles or pins; the rest are handed out with _free_hole.
    ev3_drive = params.drive_motor != "dc_motor"
    ev3_steer = params.steering_motor == "ev3_medium"
    used = {-1, 0, wb + 1} | ({12} if holes_needed > 15 else set())
    steer_x = wb - 5
    if ev3_steer:
        used.add(steer_x)
    if ev3_drive and params.layout in (Layout.FWD, Layout.AWD):
        used.add(wb - 6)

    # Cross members are axles through both rails at x = -1 and x = wb + 1; parts that hang on them
    # (motor brackets, steering connectors, sensors) add their holes before the axles are made.
    rear_cross = [rail_hole(1, -1), rail_hole(-1, -1)]
    front_cross = [rail_hole(1, wb + 1), rail_hole(-1, wb + 1)]

    # --- Axle modules
    def wheel(mid: str, x: float, side: int) -> str:
        rim = b.place(mid, rim_key, (x, side * wheel_y, r))
        tyre_id = b.place(mid, tyre_key, (x, side * wheel_y, r))
        b.connect(tyre_id, "rim", rim, "tyre")
        return rim

    rear_driven = params.layout in (Layout.RWD, Layout.AWD)
    front_driven = params.layout in (Layout.FWD, Layout.AWD)
    driver_teeth, driven_teeth = (int(t) for t in params.gears.split("-"))
    y_k = wheel_y - 2 * STUD  # steering pivots (and CV joints)

    def drive_train(mid: str, x: float, front: bool) -> None:
        """Motor along X behind the axle at ``x``, driving a pinion that meshes with the
        differential (or the driven gear of a locked axle) at right angles."""
        motor_key = {"ev3_large": "95658", "ev3_medium": "99455", "dc_motor": "dcmotor"}[
            params.drive_motor
        ]
        pinion_x = x - (driven_teeth + driver_teeth) * 0.00025 - 0.004
        pinion = b.place_at(mid, GEARS[driver_teeth], "hub", (pinion_x, 0.0, r), rot_z(90))
        shaft_n, shaft_key = (4, AXLES[4]) if front else (3, AXLES[3])
        shaft_e2 = x - 2 * STUD
        out_x = shaft_e2 - (shaft_n - 1) * STUD
        shaft = b.place_at(mid, shaft_key, "e2", (shaft_e2, 0.0, r))
        b.connect(pinion, "hub", shaft, "e2")
        motor = b.place_at(mid, motor_key, "out", (out_x, 0.0, r), _body_back(cat, motor_key))
        b.connect(motor, "out", shaft, "e1", JointKind.DRIVE_MOTOR)
        if not ev3_drive:
            return  # held by its fixed mount under the EV3 brick (see electronics)
        if front:  # the cross hole one stud behind the output sits on an axle through the rails
            mount_x = round(b.world(motor, "mount")[0][0] / STUD)
            b.axle_through(mid, [rail_hole(1, mount_x), (motor, "mount"), rail_hole(-1, mount_x)])
            return
        # Rear: the motor hangs behind the rails between two brackets (5-hole beams beside it)
        # that sit on the rear cross axle; a second axle goes through the brackets and the motor.
        lo, hi = cat.bbox(motor_key)
        half_w = max(abs(lo[0]), abs(hi[0]))
        bracket_y = math.ceil((half_w + 0.004) / (STUD / 2) - 1e-9) * (STUD / 2)
        while abs(bracket_y - rail_y) < STUD - 1e-9:
            bracket_y += STUD / 2
        brackets = [
            b.place_at(mid, BEAMS[5], "h1", (-STUD, side * bracket_y, r), rot_z(90))
            for side in (1, -1)
        ]
        rear_cross[1:1] = [(br, "h1") for br in brackets]
        b.axle_through(mid, [(brackets[0], "h5"), (motor, "mount"), (brackets[1], "h5")])

    def driven_axle(x: float, x_index: int, steered: bool) -> list[str]:
        """Rear-style axle (through the rails) or, if ``steered``, half shafts to CV joints."""
        rims: list[str] = []
        mid = "drive"
        half_studs = (wheel_y + w / 2) / STUD + 0.5
        if params.differential:
            hub_key = b.place(mid, "62821", (x, 0.0, r))
            gear = hub_key
        else:
            hub_key = b.place(mid, "6538a", (x, STUD, r), rot_z(90))
            gear = b.place(mid, GEARS[driven_teeth], (x, -STUD, r))
        drive_train(mid, x, front=steered)
        for side in (1, -1):
            n, key = _axle_key(half_studs if not steered else max(3.0, wheel_y / STUD - 2))
            inner = STUD if steered else 0.0
            center_y = side * (inner + n * STUD / 2)
            axle = b.place(mid, key, (x, center_y, r), rot_z(90))
            if params.differential:
                b.connect(
                    axle,
                    "e1" if side > 0 else "e2",
                    hub_key,
                    "axle_r" if side > 0 else "axle_l",
                    JointKind.WHEEL_AXLE,
                )
            else:
                b.connect(axle, "e1" if side > 0 else "e2", hub_key, "a" if side > 0 else "b")
                if side < 0:
                    b.connect(gear, "hub", axle, "mid")
            if not steered:
                rid, hole = rail_hole(side, x_index)
                b.connect(axle, "mid", rid, hole, JointKind.WHEEL_AXLE)
                rim = wheel(mid, x, side)
                b.connect(rim, "hub", axle, "e2" if side > 0 else "e1")
                rims.append(rim)
            else:
                cv_male = b.place_at(
                    mid, "52731", "joint", (x, side * y_k, r), rot_z(0 if side > 0 else 180)
                )
                b.connect(cv_male, "hub", axle, "e2" if side > 0 else "e1")
                rims.append(cv_male)
        return rims

    # Rear axle
    if rear_driven:
        driven_axle(0.0, 0, steered=False)
    else:  # free-rolling rear wheels, one stub axle per side through the rail
        for side in (1, -1):
            n, key = _axle_key((wheel_y - rail_y + w / 2) / STUD + 1)
            axle = b.place(
                "chassis", key, (0.0, side * (rail_y + n * STUD / 2 - STUD / 2), r), rot_z(90)
            )
            rid, hole = rail_hole(side, 0)
            b.connect(axle, "e1" if side > 0 else "e2", rid, hole, JointKind.WHEEL_AXLE)
            rim = wheel("chassis", 0.0, side)
            b.connect(rim, "hub", axle, "e2" if side > 0 else "e1")

    # Front steering: each steering arm turns on a vertical kingpin that stands in a perpendicular
    # connector (6536) on the front cross axle; the wheel stub sits one stud above and outboard of
    # the kingpin, on the wheel axis. Tie rod and steering motor as before.
    cv_males = driven_axle(front_x, wb, steered=True) if front_driven else []
    knuckles: dict[int, str] = {}
    for side in (1, -1):
        conn = b.place_at("steering", "6536", "axle", (front_x + STUD, side * y_k, r))
        front_cross.insert(len(front_cross) // 2, (conn, "axle"))
        k = b.place_at(
            "steering",
            "32069",
            "pivot",
            (front_x, side * y_k, r - STUD),
            rot_z(0 if side > 0 else 180),
        )
        knuckles[side] = k
        b.pin("steering", (conn, "pin"), (k, "pivot"), JointKind.STEERING_PIVOT)
        rim = wheel("steering", front_x, side)
        if front_driven:
            cv = b.place_at(
                "steering",
                "52730",
                "axle",
                (front_x, side * wheel_y, r),
                rot_z(0 if side > 0 else 180),
            )
            b.connect(cv, "axle", k, "stub", JointKind.WHEEL_AXLE)
            b.connect(rim, "hub", cv, "axle")
            b.connect(cv, "joint", cv_males[0 if side > 0 else 1], "joint", JointKind.WHEEL_AXLE)
        else:
            stub_at, _ = b.world(k, "stub")
            stub = b.place_at("steering", "4519", "e1", stub_at, rot_z(90 if side > 0 else -90))
            b.connect(stub, "e1", k, "stub", JointKind.WHEEL_AXLE)
            b.connect(rim, "hub", stub, "mid")
    tie = b.place("steering", "32293", (front_x - 2 * STUD, 0.0, r + STUD / 2))
    b.connect(tie, "a", knuckles[-1], "link", JointKind.STEERING_PIVOT)
    b.connect(tie, "b", knuckles[1], "link", JointKind.STEERING_PIVOT)
    if ev3_steer:  # medium motor along X on an axle through the rails, pinion on a shaft in front
        steer_motor = b.place_at(
            "steering", "99455", "mount", (steer_x * STUD, 0.0, r), _body_back(cat, "99455")
        )
        b.axle_through(
            "steering", [rail_hole(1, steer_x), (steer_motor, "mount"), rail_hole(-1, steer_x)]
        )
        out_at, _ = b.world(steer_motor, "out")
        steer_shaft = b.place_at("steering", "4519", "e1", out_at)
    else:  # servo with a vertical output, held by its fixed mount on the EV3 brick
        steer_motor = b.place_at(
            "steering", "servo", "out", (front_x - 2 * STUD, 0.0, r + 2 * STUD)
        )
        out_at, _ = b.world(steer_motor, "out")
        steer_shaft = b.place_at("steering", "4519", "e1", out_at, _align(X_AXIS, (0.0, 0.0, 1.0)))
    _, shaft_axis = b.world(steer_shaft, "mid")
    steer_gear = b.place_at(
        "steering",
        GEARS[12],
        "hub",
        b.world(steer_shaft, "mid")[0],
        _align((0.0, 1.0, 0.0), shaft_axis),
    )
    b.connect(steer_motor, "out", steer_shaft, "e1", JointKind.STEERING_MOTOR)
    b.connect(steer_gear, "hub", steer_shaft, "mid")

    # --- Electronics: EV3 brick on an axle through its rear holes (m3/m4), carried by two standing
    # beams pinned outside the rails; board on top, power bank behind. The brick sits a little ahead
    # of mid-wheelbase to balance the drive motor hanging behind the rear axle.
    brick_x = _free_hole(wb / 2 - 4, used, -1, wb + 1)
    posts: list[str] = []
    for side in (1, -1):
        rid, hole = rail_hole(side, brick_x)
        rail_at, _ = b.world(rid, hole)
        post = b.place_at(
            "electronics", BEAMS[3], "h1", (rail_at[0], rail_at[1] + side * STUD, r), POST
        )
        b.pin("electronics", (rid, hole), (post, "h1"))
        posts.append(post)
    brick_m3 = cat.part("95646c01").connector("m3")
    assert brick_m3 is not None
    m3_y = apply(quat_to_matrix(rot_z(90)), brick_m3.pose.position.as_tuple())[1]
    brick = b.place_at(
        "electronics", "95646c01", "m3", (brick_x * STUD, m3_y, r + 2 * STUD), rot_z(90)
    )
    b.axle_through(
        "electronics", [(posts[0], "h3"), (brick, "m4"), (brick, "m3"), (posts[1], "h3")]
    )
    brick_center_x = b.poses[brick][1][0]
    if not ev3_steer:
        b.connect(steer_motor, "mount", brick, "front")
    for motor_inst in [
        i.id for m in b.modules.values() for i in m.items if isinstance(i, PartInstance)
    ]:
        if (
            motor_inst.startswith("motor")
            and b.cat.key_for_hash(_hash_of(b, motor_inst)) == "dcmotor"
        ):
            b.connect(motor_inst, "mount", brick, "bottom")
    top_z = b.top(brick)
    if params.board != "none":
        key = "rpi5" if params.board == "raspberry_pi_5" else "orangepi5"
        blo, bhi = cat.bbox(key)
        board = b.place("electronics", key, (brick_center_x, 0.0, top_z + (bhi[2] - blo[2]) / 2))
        b.connect(board, "mount", brick, "top")
    if params.battery == "powerbank":
        plo, phi = cat.bbox("powerbank")
        pb = b.place(
            "electronics",
            "powerbank",
            (brick_center_x - 0.09, 0.0, r + STUD + (phi[2] - plo[2]) / 2),
        )
        b.connect(pb, "mount", brick, "rear")

    # --- Sensors. EV3 sensors: front/rear/center ones hang by their cross hole (`mount`) on an
    # axle through the rails (the cross axles for front/rear), left/right ones are pinned by their
    # `back` hole to the outside of a rail. A sensor with an offset is placed freely, without a
    # mount joint. The LiDAR has a fixed mount on the EV3 brick.
    key_of = {
        "ev3_ultrasonic": "95652",
        "ev3_gyro": "99380",
        "ev3_touch": "95648",
        "lidar_2d": "ld06",
    }
    board_h = 0.0
    if params.board != "none":
        bk = "rpi5" if params.board == "raspberry_pi_5" else "orangepi5"
        board_h = cat.bbox(bk)[1][2] - cat.bbox(bk)[0][2]
    for spec in params.sensors:
        key = key_of[spec.kind]
        offset = (spec.offset_mm[0] / 1000, spec.offset_mm[1] / 1000, spec.offset_mm[2] / 1000)
        if spec.preset == "top" or key == "ld06":
            if spec.preset == "top":  # on top of the electronics stack: free 360° view
                llo, lhi = cat.bbox(key)
                z = top_z + board_h + (lhi[2] - llo[2]) / 2 + 0.002
                center: V3 = (brick_center_x, 0.0, z)
            else:
                center = {
                    "front": ((wb + 2) * STUD, 0.0, r + 2 * STUD),
                    "rear": (-2 * STUD, 0.0, r + 2 * STUD),
                    "left": (wb / 2 * STUD, rail_y + 2.5 * STUD, r + 2 * STUD),
                    "right": (wb / 2 * STUD, -rail_y - 2.5 * STUD, r + 2 * STUD),
                    "center": (wb / 2 * STUD, 0.0, r + 2 * STUD),
                }[spec.preset]
            s = b.place("sensors", key, _add(center, offset))
            b.connect(s, "mount", brick, "top")
            continue
        free = offset != (0.0, 0.0, 0.0)
        if spec.preset in ("left", "right"):
            side = 1 if spec.preset == "left" else -1
            x_idx = _free_hole(wb // 2, used, -1, wb + 1)
            rid, hole = rail_hole(side, x_idx)
            rail_at, _ = b.world(rid, hole)
            target = (rail_at[0], rail_at[1] + side * STUD, r)
            s = b.place_at(
                "sensors", key, "back", _add(target, offset), rot_z(180 if side > 0 else 0)
            )
            if not free:
                b.pin("sensors", (rid, hole), (s, "back"))
            continue
        if spec.preset == "front":
            x_idx, yaw, cross = wb + 1, 90.0, front_cross
        elif spec.preset == "rear":
            x_idx, yaw, cross = -1, -90.0, rear_cross
        else:  # center: on its own axle through the rails
            x_idx, yaw, cross = _free_hole(wb // 2, used, -1, wb + 1), 90.0, None
        target = (x_idx * STUD, 0.0, r)
        s = b.place_at("sensors", key, "mount", _add(target, offset), rot_z(yaw))
        if free:
            continue
        if cross is None:
            b.axle_through("sensors", [rail_hole(1, x_idx), (s, "mount"), rail_hole(-1, x_idx)])
        else:
            cross.insert(len(cross) // 2, (s, "mount"))

    b.axle_through("chassis", rear_cross)
    b.axle_through("chassis", front_cross)

    return QuickStartResult(assembly=b.assembly(), params=params, wheel_radius_m=r, wheel_width_m=w)


def _hash_of(b: _Builder, instance_id: str) -> str:
    mid = b.where[instance_id]
    for item in b.modules[mid].items:
        if isinstance(item, PartInstance) and item.id == instance_id:
            return item.part.content_hash
    raise KeyError(instance_id)


__all__ = ["Category", "Layout", "QuickStartParams", "QuickStartResult", "SensorSpec", "generate"]


def vehicle_spec(result: QuickStartResult, cat: Catalogue | None = None) -> "VehicleSpec":
    """Mechanics of a quick-start car (steering, drives) for derive/MJCF."""
    from raceforge.construct.vehicle import DriveSpec, VehicleSpec
    from raceforge.core.devices import MotorParams

    cat = cat or Catalogue.load()
    p = result.params

    def motor_params(key: str) -> MotorParams:
        dev = cat.part(key).device
        assert dev is not None and isinstance(dev.params, MotorParams)
        return dev.params

    drive_key = {"ev3_large": "95658", "ev3_medium": "99455", "dc_motor": "dcmotor"}[p.drive_motor]
    steer_key = "99455" if p.steering_motor == "ev3_medium" else "servo"
    axles = []
    if p.layout in (Layout.RWD, Layout.AWD):
        axles.append(0.0)
    if p.layout in (Layout.FWD, Layout.AWD):
        axles.append(p.wheelbase_studs * STUD)
    drives = [DriveSpec(x, motor_params(drive_key), p.gear_ratio, p.differential) for x in axles]
    return VehicleSpec(
        max_steer_rad=math.radians(p.max_steer_deg),
        ackermann_pct=p.ackermann_pct,
        steering_play_rad=math.radians(p.steering_play_deg),
        steering_motor=motor_params(steer_key),
        drives=drives,
        measured_mass_kg=p.measured_mass_kg,
        measured_cog_m=None
        if p.measured_cog_mm is None
        else tuple(v / 1000 for v in p.measured_cog_mm),  # type: ignore[arg-type]
    )
