"""Compose a track and several cars into one MuJoCo world (spec 0003)."""

import itertools
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from raceforge.construct.derive import DerivedData, derive, placed_parts
from raceforge.construct.vehicle import VehicleSpec
from raceforge.core.assembly import Assembly
from raceforge.core.devices import Device
from raceforge.core.track import DoorState, Track
from raceforge.parts.catalogue import Catalogue
from raceforge.sim.mj import mujoco
from raceforge.sim.mjcf import TIMESTEP, ModelInfo, add_floor, build_mjcf

WALL_HALF_THICKNESS = 0.02
CAR_SURFACE = "car"


@dataclass(frozen=True)
class CarEntry:
    name: str  # e.g. "ego", "opp1"
    assembly: Assembly
    spec: VehicleSpec
    start_slot: int = 0
    builtin_driver: bool = False  # opponents driven by the built-in centreline follower


@dataclass(frozen=True)
class SensorMount:
    name: str  # friendly: "front", "left", "right", "rear", "lidar", "gyro", "touch"
    device: Device
    site: str  # prefixed site name in the compiled model


@dataclass
class CarHandle:
    entry: CarEntry
    prefix: str
    info: ModelInfo
    derived: DerivedData
    sensors: list[SensorMount]
    chassis_body: int = -1


@dataclass
class World:
    model: Any  # mujoco.MjModel
    track: Track
    race_setup_id: str
    cars: dict[str, CarHandle]
    surface_of_geom: list[str]  # class name per geom id
    reflectivity_of_geom: list[float]
    centreline: np.ndarray  # (N, 2) midpoints of the race setup checkpoints
    door_open: dict[str, bool] = field(default_factory=dict[str, bool])


def _point_in_polygon(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _quat_yaw(yaw: float) -> list[float]:
    return [math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2)]


def _track_xml(
    track: Track, setup_id: str, rng: np.random.Generator, randomise: bool
) -> tuple[str, dict[str, str], dict[str, bool]]:
    """MJCF of the static track; returns xml, geom name -> surface class, door states."""
    root = ET.Element("mujoco", model="track")
    world = ET.SubElement(root, "worldbody")
    surface: dict[str, str] = {}
    glass = [
        [(p.x, p.y) for p in s.polygon.points] for s in track.surfaces if s.material == "glass"
    ]
    for wi, wall in enumerate(track.walls):
        pts = [(p.x, p.y) for p in wall.points]
        for si, ((x0, y0), (x1, y1)) in enumerate(itertools.pairwise(pts)):
            length = math.hypot(x1 - x0, y1 - y0)
            if length < 1e-6:
                continue
            dx, dy = (x1 - x0) / length, (y1 - y0) / length
            # free space is on the left of the polyline -> wall body to the right
            cx = (x0 + x1) / 2 + dy * WALL_HALF_THICKNESS
            cy = (y0 + y1) / 2 - dx * WALL_HALF_THICKNESS
            name = f"wall_{wi}_{si}"
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            is_glass = any(_point_in_polygon(mx, my, g) for g in glass)
            surface[name] = "glass" if is_glass else "wall"
            ET.SubElement(
                world,
                "geom",
                name=name,
                type="box",
                pos=f"{cx:.5g} {cy:.5g} {wall.height_m / 2:.5g}",
                quat=" ".join(f"{v:.6g}" for v in _quat_yaw(math.atan2(dy, dx))),
                size=f"{length / 2 + 0.005:.5g} {WALL_HALF_THICKNESS} {wall.height_m / 2:.5g}",
                contype="1",
                conaffinity="6",
                group="2",
                rgba="0.55 0.75 0.9 0.35" if is_glass else "0.9 0.88 0.82 1",
            )
    setup = next(r for r in track.race_setups if r.id == setup_id)
    doors: dict[str, bool] = {}
    for obj in track.objects:
        pos = obj.pose.position
        q = obj.pose.orientation
        yaw = math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))
        x, y = pos.x, pos.y
        if obj.class_id == "door":
            state = setup.door_states.get(obj.id, DoorState.CLOSED)
            is_open = state is DoorState.OPEN or (state is DoorState.RANDOM and rng.random() < 0.5)
            doors[obj.id] = is_open
            if is_open:
                yaw += math.pi / 2
        elif obj.randomisation is not None and randomise:
            rr = obj.randomisation
            if rng.random() > rr.presence_prob:
                continue
            x += rng.uniform(-rr.position_xy_m, rr.position_xy_m)
            y += rng.uniform(-rr.position_xy_m, rr.position_xy_m)
            yaw += rng.uniform(-rr.yaw_rad, rr.yaw_rad)
        name = f"obj_{obj.id}"
        surface[name] = obj.class_id
        ET.SubElement(
            world,
            "geom",
            name=name,
            type="box",
            pos=f"{x:.5g} {y:.5g} {pos.z:.5g}",
            quat=" ".join(f"{v:.6g}" for v in _quat_yaw(yaw)),
            size=f"{obj.size.x / 2:.5g} {obj.size.y / 2:.5g} {obj.size.z / 2:.5g}",
            contype="1",
            conaffinity="6",
            group="2",
            rgba="0.6 0.45 0.3 1",
        )
    return ET.tostring(root, encoding="unicode"), surface, doors


def _sensor_name(direction: np.ndarray, kind: str, used: set[str]) -> str:
    if kind == "lidar_2d":
        base = "lidar"
    elif kind == "ev3_gyro":
        base = "gyro"
    elif kind == "ev3_touch":
        base = "touch"
    elif abs(direction[0]) >= abs(direction[1]):
        base = "front" if direction[0] > 0 else "rear"
    else:
        base = "left" if direction[1] > 0 else "right"
    name, k = base, 2
    while name in used:
        name, k = f"{base}{k}", k + 1
    used.add(name)
    return name


def build_world(
    track: Track,
    cars: list[CarEntry],
    cat: Catalogue,
    race_setup_id: str = "main",
    seed: int = 0,
    randomise_objects: bool = False,
) -> World:
    rng = np.random.default_rng([seed, 1])
    setup = next((r for r in track.race_setups if r.id == race_setup_id), None)
    if setup is None:
        raise ValueError(f"track has no race setup {race_setup_id!r}")
    if len(cars) > len(setup.start_grid):
        raise ValueError(f"{len(cars)} cars but only {len(setup.start_grid)} start slots")

    floor_friction = next((s.friction for s in track.surfaces if s.material == "floor"), 0.8)
    root = ET.Element("mujoco", model="raceforge_world")
    ET.SubElement(root, "compiler", angle="radian", inertiafromgeom="false", autolimits="true")
    ET.SubElement(root, "option", timestep=f"{TIMESTEP}", integrator="implicitfast")
    ET.SubElement(root, "visual").append(ET.Element("global", offwidth="1280", offheight="960"))
    world_el = ET.SubElement(root, "worldbody")
    add_floor(root, world_el, floor_friction)
    spec = mujoco.MjSpec.from_string(ET.tostring(root, encoding="unicode"))

    track_xml, surface_by_name, doors = _track_xml(track, race_setup_id, rng, randomise_objects)
    spec.attach(
        mujoco.MjSpec.from_string(track_xml), frame=spec.worldbody.add_frame(), prefix="track/"
    )

    handles: dict[str, CarHandle] = {}
    for entry in cars:
        if entry.name in handles or "/" in entry.name:
            raise ValueError(f"invalid or duplicate car name {entry.name!r}")
        xml, info = build_mjcf(entry.assembly, cat, entry.spec, standalone=False)
        car_spec = mujoco.MjSpec.from_string(xml)
        slot = setup.start_grid[entry.start_slot]
        frame = spec.worldbody.add_frame(pos=[slot.x, slot.y, 0.0], quat=_quat_yaw(slot.theta))
        prefix = f"{entry.name}/"
        spec.attach(car_spec, frame=frame, prefix=prefix)
        parts = {p.path: p for p in placed_parts(entry.assembly, cat)}
        used: set[str] = set()
        mounts: list[SensorMount] = []
        for path, site in sorted(info.sensor_sites.items()):
            part = parts[tuple(path.split("/"))]
            device = cat.part(part.key).device
            assert device is not None
            entry_cat = cat.entry(part.key)
            direction = np.zeros(3)
            if entry_cat.sense_axis_ld is not None:
                from raceforge.core.frames import ldraw_point_to_core

                d = np.array(ldraw_point_to_core(entry_cat.sense_axis_ld))
                direction = part.rotation @ (d / np.linalg.norm(d))
            mounts.append(
                SensorMount(_sensor_name(direction, device.type, used), device, prefix + site)
            )
        handles[entry.name] = CarHandle(
            entry=entry,
            prefix=prefix,
            info=info,
            derived=derive(entry.assembly, cat, entry.spec),
            sensors=mounts,
        )

    # Car start slots must not overlap in the world: MuJoCo resolves it, but warn early.
    model = spec.compile()
    surfaces: list[str] = []
    reflect: list[float] = []
    classes = {c.id: c for c in track.classes}
    for gid in range(model.ngeom):
        name = model.geom(gid).name
        if name.startswith("track/"):
            cls = surface_by_name.get(name.removeprefix("track/"), "wall")
        elif name == "floor":
            cls = "floor"
        else:
            cls = CAR_SURFACE
        surfaces.append(cls)
        refl = {"glass": 0.1, "wall": 0.85, "floor": 0.8, CAR_SURFACE: 0.6}.get(cls)
        if refl is None:
            cdef = classes.get(cls)
            refl = cdef.lidar_reflectivity if cdef and cdef.lidar_reflectivity is not None else 0.7
        reflect.append(refl)
    for h in handles.values():
        h.chassis_body = model.body(h.prefix + "chassis").id
    centre = np.array([[(c.a.x + c.b.x) / 2, (c.a.y + c.b.y) / 2] for c in setup.checkpoints])
    return World(model, track, race_setup_id, handles, surfaces, reflect, centre, doors)
