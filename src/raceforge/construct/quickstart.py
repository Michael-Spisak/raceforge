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
from raceforge.core.frames import apply, quat_to_matrix
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
    kind: Literal["ev3_ultrasonic", "ev3_gyro", "ev3_touch"]
    preset: Literal["front", "left", "right", "rear", "center"]
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


@dataclass
class _Module:
    id: str
    name: str
    role: SubmodelRole
    items: list[Item] = field(default_factory=list[Item])


class _Builder:
    """Places catalogue parts in world coordinates and records connections and joint roles."""

    def __init__(self, cat: Catalogue) -> None:
        self.cat = cat
        self.modules: dict[str, _Module] = {}
        self.where: dict[str, str] = {}  # instance id -> module id
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
        return iid

    def connect(self, a: str, ac: str, b: str, bc: str, role: JointKind | None = None) -> None:
        conn = Connection(
            a=ConnectorPath(instances=[self.where[a], a], connector=ac),
            b=ConnectorPath(instances=[self.where[b], b], connector=bc),
        )
        self.connections.append(conn)
        if role is not None:
            self.joints.append(JointRole(connection=len(self.connections) - 1, role=role))

    def pin(self, mid: str, at: V3, a: str, ac: str, b: str, bc: str) -> None:
        """Join two pin holes with a friction pin placed at ``at``."""
        p = self.place(mid, "2780", at, rot_z(90))
        self.connect(p, "e1", a, ac)
        self.connect(p, "e2", b, bc)

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

    # --- Chassis rails (holes from x = -1 stud to x = wb + 1 studs), cross members
    rails: dict[int, list[tuple[str, int]]] = {}  # side -> [(beam id, x index of hole h1)]
    holes_needed = wb + 3
    for side in (1, -1):
        y = side * rail_y
        if holes_needed <= 15:
            n, key = _beam_key(holes_needed)
            rid = b.place_at("chassis", key, "h1", (-STUD, y, r), rot_z(-90))
            rails[side] = [(rid, -1)]
        else:
            rid = b.place_at("chassis", BEAMS[15], "h1", (-STUD, y, r), rot_z(-90))
            _, key2 = _beam_key(max(3, holes_needed - 13))
            rid2 = b.place_at("chassis", key2, "h1", (12 * STUD, y, r + STUD), rot_z(-90))
            b.pin("chassis", (12 * STUD, y, r + STUD / 2), rid, "h14", rid2, "h1")
            rails[side] = [(rid, -1), (rid2, 12)]

    def rail_hole(side: int, x_index: int) -> tuple[str, str]:
        for rid, start in reversed(rails[side]):
            if x_index >= start:
                return rid, f"h{x_index - start + 1}"
        raise ValueError("hole outside rails")

    span_holes = round(2 * rail_y / STUD) + 1
    for x_idx in (-1, wb + 1):
        n, key = _beam_key(span_holes if span_holes % 2 else span_holes + 1)
        cross = b.place("chassis", key, (x_idx * STUD, 0.0, r + STUD))
        for side in (1, -1):
            rid, hole = rail_hole(side, x_idx)
            b.pin(
                "chassis",
                (x_idx * STUD, side * rail_y, r + STUD / 2),
                rid,
                hole,
                cross,
                "h1" if side < 0 else f"h{n}",
            )

    # --- Axle modules
    def wheel(mid: str, x: float, side: int) -> str:
        rim = b.place(mid, rim_key, (x, side * wheel_y, r))
        tyre_id = b.place(mid, tyre_key, (x, side * wheel_y, r))
        b.connect(tyre_id, "rim", rim, "tyre")
        return rim

    rear_driven = params.layout in (Layout.RWD, Layout.AWD)
    front_driven = params.layout in (Layout.FWD, Layout.AWD)
    driver_teeth, driven_teeth = (int(t) for t in params.gears.split("-"))

    def drive_motor(mid: str, out_at: V3, axis: Literal["x", "y"]) -> str:
        key = {"ev3_large": "95658", "ev3_medium": "99455", "dc_motor": "dcmotor"}[
            params.drive_motor
        ]
        rot = rot_z(-90) if axis == "x" else Quat()
        motor = b.place_at(mid, key, "out", out_at, rot)
        if key == "dcmotor":
            return motor
        rid, hole = rail_hole(1, 0)
        b.pin(mid, (out_at[0], rail_y, r), motor, "mount", rid, hole)
        return motor

    def driven_axle(x: float, x_index: int, steered: bool) -> list[str]:
        """Rear-style axle (through the rails) or, if ``steered``, half shafts to CV joints."""
        rims: list[str] = []
        driven = (steered and front_driven) or (not steered and rear_driven)
        mid = "drive" if driven else "chassis"
        if not driven:
            return rims
        half_studs = (wheel_y + w / 2) / STUD + 0.5
        if params.differential:
            diff = b.place(mid, "62821", (x, 0.0, r))
            hub_key = diff
            gear_center: V3 = (x - (driven_teeth + driver_teeth) * 0.00025 - 0.004, 0.0, r)
            gear = b.place(mid, GEARS[driver_teeth], gear_center, rot_z(90))
            shaft_n, shaft_key = _axle_key(4)
            shaft = b.place(mid, shaft_key, (gear_center[0] - shaft_n * STUD / 2 + STUD, 0.0, r))
            b.connect(gear, "hub", shaft, "e2")
            motor = drive_motor(mid, (gear_center[0] - shaft_n * STUD + STUD, 0.0, r), "x")
            b.connect(motor, "out", shaft, "e1", JointKind.DRIVE_MOTOR)
        else:
            joiner = b.place(mid, "6538a", (x, STUD, r), rot_z(90))
            hub_key = joiner
            gear = b.place(mid, GEARS[driven_teeth], (x, -STUD, r))
            pitch = (driver_teeth + driven_teeth) * 0.0005
            m_gear = b.place(mid, GEARS[driver_teeth], (x - pitch, -STUD, r))
            shaft_n, shaft_key = _axle_key(4)
            shaft = b.place(
                mid, shaft_key, (x - pitch, -STUD - shaft_n * STUD / 2 + STUD, r), rot_z(90)
            )
            b.connect(m_gear, "hub", shaft, "e2")
            motor = drive_motor(mid, (x - pitch, -STUD - shaft_n * STUD + STUD, r), "y")
            b.connect(motor, "out", shaft, "e1", JointKind.DRIVE_MOTOR)
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
                cv_male = b.place(mid, "52731", (x, side * (wheel_y - 3 * STUD), r), rot_z(0))
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

    # Front steering (Ackermann knuckles, tie rod, steering motor)
    cv_males = driven_axle(front_x, wb, steered=True) if front_driven else []
    knuckles: dict[int, str] = {}
    for side in (1, -1):
        k = b.place("steering", "32069", (front_x, side * (wheel_y - 2 * STUD), r))
        knuckles[side] = k
        rid, hole = rail_hole(side, wb)
        pivot_pin = b.place(
            "steering", "2780", (front_x, side * (wheel_y - 2 * STUD), r + STUD), rot_z(90)
        )
        b.connect(pivot_pin, "e1", rid, hole)
        b.connect(k, "pivot", pivot_pin, "e2", JointKind.STEERING_PIVOT)
        rim = wheel("steering", front_x, side)
        if front_driven:
            cv = b.place("steering", "52730", (front_x, side * (wheel_y - STUD), r), rot_z(0))
            b.connect(cv, "axle", k, "stub", JointKind.WHEEL_AXLE)
            b.connect(rim, "hub", cv, "axle")
            b.connect(cv, "joint", cv_males[0 if side > 0 else 1], "joint", JointKind.WHEEL_AXLE)
        else:
            stub = b.place("steering", "4519", (front_x, side * (wheel_y - STUD / 2), r), rot_z(90))
            b.connect(stub, "e1", k, "stub", JointKind.WHEEL_AXLE)
            b.connect(rim, "hub", stub, "e2")
    tie = b.place("steering", "32293", (front_x - 2 * STUD, 0.0, r + STUD / 2))
    b.connect(tie, "a", knuckles[-1], "link", JointKind.STEERING_PIVOT)
    b.connect(tie, "b", knuckles[1], "link", JointKind.STEERING_PIVOT)
    steer_key = "99455" if params.steering_motor == "ev3_medium" else "servo"
    steer_motor = b.place_at("steering", steer_key, "out", (front_x - 2 * STUD, 0.0, r + 2 * STUD))
    steer_shaft = b.place("steering", "4519", (front_x - 2 * STUD, 0.0, r + 1.5 * STUD), rot_z(90))
    steer_gear = b.place("steering", GEARS[12], (front_x - 2 * STUD, STUD, r + 1.5 * STUD))
    b.connect(steer_motor, "out", steer_shaft, "e1", JointKind.STEERING_MOTOR)
    b.connect(steer_gear, "hub", steer_shaft, "mid")
    if steer_key == "99455":
        rid, hole = rail_hole(1, wb - 2)
        b.pin("steering", (front_x - 2 * STUD, rail_y, r), steer_motor, "mount", rid, hole)

    # --- Electronics: EV3 brick on the rails, board on top, power bank behind
    lo, hi = cat.bbox("95646c01")
    brick_h = hi[2] - lo[2]
    brick = b.place(
        "electronics", "95646c01", (front_x / 2, 0.0, r + STUD + brick_h / 2 + 0.002), rot_z(90)
    )
    for side, mount in ((1, "m1"), (-1, "m2")):
        rid, hole = rail_hole(side, wb // 2)
        b.pin(
            "electronics", ((wb // 2) * STUD, side * rail_y, r + STUD / 2), brick, mount, rid, hole
        )
    if steer_key == "servo":
        b.connect(steer_motor, "mount", brick, "front")
    for motor_inst in [
        i.id for m in b.modules.values() for i in m.items if isinstance(i, PartInstance)
    ]:
        if (
            motor_inst.startswith("motor")
            and b.cat.key_for_hash(_hash_of(b, motor_inst)) == "dcmotor"
        ):
            b.connect(motor_inst, "mount", brick, "bottom")
    top_z = r + STUD + brick_h + 0.002
    if params.board != "none":
        key = "rpi5" if params.board == "raspberry_pi_5" else "orangepi5"
        blo, bhi = cat.bbox(key)
        board = b.place("electronics", key, (front_x / 2, 0.0, top_z + (bhi[2] - blo[2]) / 2))
        b.connect(board, "mount", brick, "top")
    if params.battery == "powerbank":
        plo, phi = cat.bbox("powerbank")
        pb = b.place(
            "electronics", "powerbank", (front_x / 2 - 0.09, 0.0, r + STUD + (phi[2] - plo[2]) / 2)
        )
        b.connect(pb, "mount", brick, "rear")

    # --- Sensors
    key_of = {"ev3_ultrasonic": "95652", "ev3_gyro": "99380", "ev3_touch": "95648"}
    for spec in params.sensors:
        preset: dict[str, tuple[V3, float, int]] = {
            "front": (((wb + 2) * STUD, 0.0, r + 2 * STUD), 90.0, wb + 1),
            "rear": ((-2 * STUD, 0.0, r + 2 * STUD), -90.0, -1),
            "left": ((wb / 2 * STUD, rail_y + 2.5 * STUD, r + 2 * STUD), 180.0, wb // 2 - 2),
            "right": ((wb / 2 * STUD, -rail_y - 2.5 * STUD, r + 2 * STUD), 0.0, wb // 2 - 2),
            "center": ((wb / 2 * STUD, 0.0, r + 2 * STUD), 0.0, wb // 2),
        }
        center, yaw, hole_x = preset[spec.preset]
        center = _add(
            center, (spec.offset_mm[0] / 1000, spec.offset_mm[1] / 1000, spec.offset_mm[2] / 1000)
        )
        s = b.place("sensors", key_of[spec.kind], center, rot_z(yaw))
        side = -1 if spec.preset == "right" else 1
        rid, hole = rail_hole(side, max(-1, min(wb + 1, hole_x)))
        b.pin("sensors", (center[0], side * rail_y, r + STUD), s, "mount", rid, hole)

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
