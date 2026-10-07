"""Small valid example objects used across core tests."""

import hashlib
from datetime import date

from raceforge.core.assembly import (
    Assembly,
    Axis,
    Connection,
    ConnectorPath,
    Item,
    JointKind,
    JointRole,
    PartInstance,
    Submodel,
    SubmodelInstance,
)
from raceforge.core.connectors import ConnectorType, Gender
from raceforge.core.devices import Ev3UltrasonicSensor, PortAssignment
from raceforge.core.ids import new_object_id
from raceforge.core.io import content_hash
from raceforge.core.parts import Connector, Part, PartSource, PriceInfo
from raceforge.core.primitives import (
    BlobRef,
    Money,
    Pose,
    Pose2D,
    Segment2D,
    Timestamp,
    Vec2,
    Vec3,
    VersionRef,
)
from raceforge.core.telemetry import (
    Command,
    LoopStats,
    Measured,
    Mode,
    RangeArray,
    RangeReading,
    RunKind,
    RunLog,
    TelemetryFrame,
)
from raceforge.core.track import ClassDef, Polygon2D, RaceSetup, Track, TrackObject, Wall

SHA = hashlib.sha256(b"x").hexdigest()


def blob() -> BlobRef:
    return BlobRef(sha256=SHA, size_bytes=1, media_type="application/octet-stream")


def ref(part: Part | None = None) -> VersionRef:
    return VersionRef(
        object_id=new_object_id(),
        semver="1.0.0",
        content_hash=content_hash(part) if part is not None else SHA,
    )


def beam() -> Part:
    return Part(
        source=PartSource.LEGO_LDRAW,
        ldraw_id="32524",
        name="Technic Beam 7",
        mass_kg=0.0026,
        price=PriceInfo(price=Money(cents=12), observed_on=date(2026, 10, 7)),
        connectors=[
            Connector(id="h1", type=ConnectorType.PIN_HOLE, gender=Gender.FEMALE),
            Connector(
                id="h2",
                type=ConnectorType.PIN_HOLE,
                gender=Gender.FEMALE,
                pose=Pose(position=Vec3(x=0.008)),
            ),
        ],
        verified=True,
    )


def pin() -> Part:
    return Part(
        source=PartSource.LEGO_LDRAW,
        ldraw_id="2780",
        name="Technic Pin with Friction",
        mass_kg=0.0002,
        connectors=[
            Connector(id="a", type=ConnectorType.PIN, gender=Gender.MALE, length_m=0.008),
            Connector(id="b", type=ConnectorType.PIN, gender=Gender.MALE, length_m=0.008),
        ],
    )


def ultrasonic() -> Part:
    return Part(
        source=PartSource.DEVICE,
        ldraw_id="95652",
        name="EV3 Ultrasonic Sensor",
        mass_kg=0.026,
        device=Ev3UltrasonicSensor(model="45504", port=PortAssignment(host="ev3:1", port="1")),
    )


def assembly() -> tuple[Assembly, dict[str, Part]]:
    b, p = beam(), pin()
    parts = {content_hash(b): b, content_hash(p): p}
    side = Submodel(
        id="side",
        name="Side frame",
        items=[
            PartInstance(id="beam", part=ref(b)),
            PartInstance(id="pin", part=ref(p), pose=Pose(position=Vec3(y=0.02))),
        ],
    )
    root_items: list[Item] = [
        SubmodelInstance(id="left", submodel="side", pose=Pose(position=Vec3(y=0.05))),
        SubmodelInstance(
            id="right", submodel="side", pose=Pose(position=Vec3(y=-0.05)), mirrored=Axis.Y
        ),
    ]
    root = Submodel(id="car", name="Car", items=root_items)
    asm = Assembly(
        root="car",
        submodels={"car": root, "side": side},
        connections=[
            Connection(
                a=ConnectorPath(instances=["left", "beam"], connector="h1"),
                b=ConnectorPath(instances=["left", "pin"], connector="a"),
            )
        ],
        joints=[JointRole(connection=0, role=JointKind.WHEEL_AXLE)],
    )
    return asm, parts


def track() -> Track:
    return Track(
        floor=Polygon2D(points=[Vec2(x=0, y=0), Vec2(x=10, y=0), Vec2(x=10, y=2), Vec2(x=0, y=2)]),
        walls=[Wall(points=[Vec2(x=0, y=0), Vec2(x=10, y=0)], height_m=2.5)],
        classes=[ClassDef(id="bin", name="Trash bin", builtin=True, lidar_reflectivity=0.5)],
        objects=[
            TrackObject(
                id="bin-1",
                class_id="bin",
                pose=Pose(position=Vec3(x=5, y=1.5)),
                size=Vec3(x=0.3, y=0.3, z=0.6),
            )
        ],
        race_setups=[
            RaceSetup(
                id="main",
                name="Main",
                start_line=Segment2D(a=Vec2(x=1, y=0), b=Vec2(x=1, y=2)),
                finish_line=Segment2D(a=Vec2(x=9, y=0), b=Vec2(x=9, y=2)),
                direction=Vec2(x=1, y=0),
                start_grid=[Pose2D(x=0.5, y=0.5), Pose2D(x=0.5, y=1.5)],
            )
        ],
        mesh=blob(),
    )


def frame(seq: int = 0) -> TelemetryFrame:
    return TelemetryFrame(
        t=Timestamp(mono_ns=1_000_000 * seq, wall_offset_ns=1_759_000_000_000_000_000),
        seq=seq,
        mode=Mode.TEST,
        state="straight",
        cmd=Command(steering_rad=0.05, speed_m_s=0.8),
        meas=Measured(
            speed_m_s=0.79,
            sensors={
                "us_front": RangeReading(distance_m=1.2),
                "lidar": RangeArray(
                    angle_min_rad=-3.14, angle_increment_rad=0.1745, ranges=[1.0] * 35 + [None]
                ),
            },
        ),
        loop=LoopStats(rate_hz=50.0),
        channels={"pid.error": 0.01, "wall.left_m": 0.42, "debug.flag": True, "note": "ok"},
    )


def runlog() -> RunLog:
    return RunLog(
        id=new_object_id(),
        kind=RunKind.SIM,
        assembly=ref(),
        track=ref(),
        race_setup_id="main",
        controller=ref(),
        started=Timestamp(mono_ns=0),
        file=blob(),
    )
