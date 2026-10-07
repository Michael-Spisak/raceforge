"""Derived data from an assembly: mass, CoG, inertia, geometry, kinematics, plausibility checks."""

import math
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from raceforge.construct.vehicle import VehicleSpec
from raceforge.core.assembly import Assembly
from raceforge.core.frames import iter_part_placements, quat_to_matrix
from raceforge.parts.catalogue import Catalogue, Category

G = 9.81
type Mat = npt.NDArray[np.float64]


@dataclass(frozen=True)
class PlacedPart:
    path: tuple[str, ...]
    key: str
    category: Category
    mass_kg: float
    rotation: Mat  # world rotation (3x3), proper
    origin: Mat  # world position of the part origin
    bbox_lo: Mat  # part-frame bbox
    bbox_hi: Mat

    @property
    def center(self) -> Mat:
        return self.origin + self.rotation @ ((self.bbox_lo + self.bbox_hi) / 2)

    @property
    def size(self) -> Mat:
        return self.bbox_hi - self.bbox_lo

    def world_bbox(self) -> tuple[Mat, Mat]:
        corners = np.array(
            [
                [x, y, z]
                for x in (self.bbox_lo[0], self.bbox_hi[0])
                for y in (self.bbox_lo[1], self.bbox_hi[1])
                for z in (self.bbox_lo[2], self.bbox_hi[2])
            ]
        )
        world = corners @ self.rotation.T + self.origin
        return world.min(axis=0), world.max(axis=0)

    def inertia_about(self, point: Mat) -> Mat:
        """World-frame inertia tensor of the part (solid box) about ``point``."""
        sx, sy, sz = self.size
        m = self.mass_kg
        local = np.diag([m * (sy**2 + sz**2), m * (sx**2 + sz**2), m * (sx**2 + sy**2)]) / 12
        d = self.center - point
        return self.rotation @ local @ self.rotation.T + m * (
            float(d @ d) * np.eye(3) - np.outer(d, d)
        )


def placed_parts(assembly: Assembly, cat: Catalogue) -> list[PlacedPart]:
    out: list[PlacedPart] = []
    for chain, inst, placement in iter_part_placements(assembly):
        key = cat.key_for_hash(inst.part.content_hash)
        entry = cat.entry(key)
        lo, hi = cat.bbox(key)
        out.append(
            PlacedPart(
                path=tuple(chain),
                key=key,
                category=entry.category,
                mass_kg=entry.mass_g / 1000,
                rotation=np.array(quat_to_matrix(placement.pose.orientation)),
                origin=np.array(placement.pose.position.as_tuple()),
                bbox_lo=np.array(lo),
                bbox_hi=np.array(hi),
            )
        )
    return out


def mass_properties(parts: list[PlacedPart]) -> tuple[float, Mat, Mat]:
    """Total mass, centre of gravity and inertia tensor about the CoG (world frame)."""
    m = sum(p.mass_kg for p in parts)
    if m <= 0:
        raise ValueError("assembly has no mass")
    cog = sum((p.mass_kg * p.center for p in parts), np.zeros(3)) / m
    inertia = sum((p.inertia_about(cog) for p in parts), np.zeros((3, 3)))
    return m, cog, inertia


@dataclass(frozen=True)
class Warning:
    code: str
    message: str


@dataclass(frozen=True)
class DerivedData:
    mass_kg: float
    cog_m: tuple[float, float, float]
    inertia: tuple[tuple[float, float, float], ...]
    mass_is_measured: bool
    wheelbase_m: float
    track_m: float
    wheel_radius_m: float
    ground_clearance_m: float
    size_m: tuple[float, float, float]
    steer_inner_rad: float
    steer_outer_rad: float
    turning_radius_m: float
    top_speed_m_s: float
    max_accel_m_s2: float
    rollover_speed_m_s: float
    battery_runtime_h: float | None
    warnings: list[Warning] = field(default_factory=list[Warning])


def ackermann_outer(inner: float, wheelbase: float, track: float, pct: float) -> float:
    """Outer wheel angle for an inner angle; blends parallel (0 %) and ideal Ackermann (100 %)."""
    if inner == 0:
        return 0.0
    ideal = math.atan(1 / (1 / math.tan(abs(inner)) + track / wheelbase))
    return math.copysign(abs(inner) + pct / 100 * (ideal - abs(inner)), inner)


def derive(assembly: Assembly, cat: Catalogue, spec: VehicleSpec) -> DerivedData:
    parts = placed_parts(assembly, cat)
    m, cog, inertia = mass_properties(parts)
    measured = False
    if spec.measured_mass_kg is not None:
        inertia = inertia * (spec.measured_mass_kg / m)
        m, measured = spec.measured_mass_kg, True
    if spec.measured_cog_m is not None:
        cog, measured = np.array(spec.measured_cog_m), True

    tyres = [p for p in parts if p.category is Category.TYRE]
    if len(tyres) < 3:
        raise ValueError("an assembly needs at least three tyres to derive vehicle geometry")
    centers = np.array([p.center for p in tyres])
    radius = float(max(p.size.max() for p in tyres) / 2)
    xs, ys = centers[:, 0], centers[:, 1]
    wheelbase = float(xs.max() - xs.min())
    track = float(ys.max() - ys.min())

    lows = [
        p.world_bbox()[0]
        for p in parts
        if p.category is not Category.TYRE and p.category is not Category.WHEEL_RIM
    ]
    highs = [p.world_bbox()[1] for p in parts]
    all_lo = np.minimum.reduce([p.world_bbox()[0] for p in parts])
    all_hi = np.maximum.reduce(highs)
    clearance = float(min(lo[2] for lo in lows))

    inner = spec.max_steer_rad
    outer = abs(ackermann_outer(inner, wheelbase, track, spec.ackermann_pct))
    turning_radius = (
        wheelbase / math.tan(outer) + track / 2
    )  # outer front-axle-centre approximation

    top_speed = (
        min(d.motor.no_load_speed_rad_s / d.gear_ratio for d in spec.drives) * radius
        if spec.drives
        else 0.0
    )
    traction_force = (
        sum(0.5 * d.motor.stall_torque_nm * d.gear_ratio / radius for d in spec.drives) * 0.7
    )
    accel = min(traction_force / m, spec.tyre_friction * G)
    h = float(cog[2])
    rollover = math.sqrt(G * spec.target_curve_radius_m * track / (2 * h)) if h > 0 else math.inf

    runtime = _battery_runtime_h(parts, cat)
    warnings: list[Warning] = []
    if rollover < top_speed:
        warnings.append(
            Warning(
                "rollover",
                f"CoG {h * 1000:.0f} mm high: tips over above {rollover:.2f} m/s in a "
                f"{spec.target_curve_radius_m:.1f} m curve (top speed {top_speed:.2f} m/s)",
            )
        )
    if turning_radius > spec.corridor_min_width_m:
        warnings.append(
            Warning(
                "turning_radius",
                f"turning radius {turning_radius:.2f} m exceeds the narrowest "
                f"corridor width {spec.corridor_min_width_m:.2f} m",
            )
        )
    if accel < 0.5:
        warnings.append(
            Warning("weak_drive", f"max acceleration {accel:.2f} m/s² is below 0.5 m/s²")
        )
    if runtime is not None and runtime < 0.5:
        warnings.append(
            Warning("battery_runtime", f"expected runtime {runtime * 60:.0f} min is below 30 min")
        )
    steer_load = spec.tyre_friction * m * G / 4 * radius * 0.3 * 2
    if spec.steering_motor.stall_torque_nm * 0.5 < steer_load:
        warnings.append(
            Warning(
                "weak_steering",
                f"steering motor torque may be too low (needs ~{steer_load:.2f} N·m at standstill)",
            )
        )

    return DerivedData(
        mass_kg=m,
        cog_m=(float(cog[0]), float(cog[1]), float(cog[2])),
        inertia=tuple(tuple(float(v) for v in row) for row in inertia),  # type: ignore[misc]
        mass_is_measured=measured,
        wheelbase_m=wheelbase,
        track_m=track,
        wheel_radius_m=radius,
        ground_clearance_m=clearance,
        size_m=tuple(float(v) for v in (all_hi - all_lo)),  # type: ignore[arg-type]
        steer_inner_rad=inner,
        steer_outer_rad=outer,
        turning_radius_m=turning_radius,
        top_speed_m_s=top_speed,
        max_accel_m_s2=accel,
        rollover_speed_m_s=rollover,
        battery_runtime_h=runtime,
        warnings=warnings,
    )


# Typical average power draw during driving (W), used for the runtime estimate.
_POWER_W = {Category.EV3_BRICK: 1.5, Category.MOTOR: 2.0, Category.SENSOR: 0.2, Category.BOARD: 6.0}
_EV3_BATTERY_WH = 15.2  # 2050 mAh at 7.4 V


def _battery_runtime_h(parts: list[PlacedPart], cat: Catalogue) -> float | None:
    """Hours until the first battery is empty (EV3 battery vs. power bank for the board)."""
    ev3_load = sum(_POWER_W.get(p.category, 0.0) for p in parts if p.category is not Category.BOARD)
    runtimes: list[float] = []
    if any(p.category is Category.EV3_BRICK for p in parts):
        runtimes.append(_EV3_BATTERY_WH / max(ev3_load, 0.1))
    board_load = sum(_POWER_W[Category.BOARD] for p in parts if p.category is Category.BOARD)
    banks = [p for p in parts if p.category is Category.BATTERY]
    if board_load and banks:
        capacity = 0.0
        for bank in banks:
            dev = cat.part(bank.key).device
            if dev is not None and dev.type == "battery":
                capacity += dev.params.capacity_wh * 0.85
        runtimes.append(capacity / board_load)
    return min(runtimes) if runtimes else None
