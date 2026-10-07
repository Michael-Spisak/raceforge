"""Shared builders for simulation tests."""

from raceforge.construct.quickstart import QuickStartParams, SensorSpec, generate, vehicle_spec
from raceforge.core.primitives import Pose2D, Segment2D, Vec2
from raceforge.core.track import Polygon2D, RaceSetup, Track, Wall
from raceforge.parts.catalogue import Catalogue
from raceforge.sim.engine import Simulation
from raceforge.sim.world import CarEntry, build_world

ALL_SENSORS = [
    SensorSpec(kind="lidar_2d", preset="top"),
    SensorSpec(kind="ev3_ultrasonic", preset="front"),
    SensorSpec(kind="ev3_ultrasonic", preset="left"),
    SensorSpec(kind="ev3_ultrasonic", preset="right"),
    SensorSpec(kind="ev3_gyro", preset="center"),
]


def car(cat: Catalogue, **kw: object) -> CarEntry:
    params = QuickStartParams.model_validate({"drive_gears": "20-28", "sensors": ALL_SENSORS, **kw})
    res = generate(params, cat)
    return CarEntry("ego", res.assembly, vehicle_spec(res, cat))


def box_room(width: float = 4.0, length: float = 6.0, glass: bool = False) -> Track:
    """Rectangular room (free space left of each wall); car starts at (1, width/2)."""
    corners = [
        Vec2(x=0, y=0),
        Vec2(x=length, y=0),
        Vec2(x=length, y=width),
        Vec2(x=0, y=width),
        Vec2(x=0, y=0),
    ]
    from raceforge.core.track import SurfaceRegion

    surfaces = []
    if glass:
        poly = Polygon2D(
            points=[
                Vec2(x=length - 0.1, y=-0.1),
                Vec2(x=length + 0.1, y=-0.1),
                Vec2(x=length + 0.1, y=width + 0.1),
                Vec2(x=length - 0.1, y=width + 0.1),
            ]
        )
        surfaces.append(
            SurfaceRegion(polygon=poly, friction=1.0, material="glass", lidar_reflectivity=0.1)
        )
    cps = [
        Segment2D(a=Vec2(x=x, y=0), b=Vec2(x=x, y=width)) for x in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0)
    ]
    setup = RaceSetup(
        id="main",
        name="room",
        start_line=cps[1],
        finish_line=cps[-1],
        direction=Vec2(x=1, y=0),
        laps=1,
        start_grid=[Pose2D(x=1.0, y=width / 2), Pose2D(x=1.0, y=width / 2 + 1.0)],
        checkpoints=cps,
    )
    return Track(
        floor=Polygon2D(points=corners[:-1]),
        walls=[Wall(points=corners)],
        race_setups=[setup],
        surfaces=surfaces,
    )


def sim_in(track: Track, cat: Catalogue, entries: list[CarEntry], seed: int = 0) -> Simulation:
    return Simulation(build_world(track, entries, cat, seed=seed), seed=seed)
