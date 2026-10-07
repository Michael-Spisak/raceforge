"""Hypothesis strategies for core models (AC1 property-based round trips)."""

import math

from hypothesis import strategies as st

from raceforge.core.assembly import Assembly, Axis, Item, PartInstance, Submodel, SubmodelInstance
from raceforge.core.connectors import ConnectorType, Gender
from raceforge.core.ids import new_object_id
from raceforge.core.parts import Connector, Part, PartSource
from raceforge.core.primitives import Pose, Pose2D, Quat, Timestamp, Vec2, Vec3, VersionRef
from raceforge.core.telemetry import (
    Command,
    LoopStats,
    Measured,
    Mode,
    RangeReading,
    TelemetryFrame,
)
from raceforge.core.track import Polygon2D, Track, Wall

finite = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)
positive = st.floats(min_value=1e-6, max_value=1e3, allow_nan=False, allow_infinity=False)
local_ids = st.from_regex(r"\A[a-z0-9][a-z0-9_-]{0,10}\Z")
hexes = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)

vec2 = st.builds(Vec2, x=finite, y=finite)
vec3 = st.builds(Vec3, x=finite, y=finite, z=finite)


@st.composite
def quats(draw: st.DrawFn) -> Quat:
    comps = draw(st.lists(st.floats(-1, 1, allow_nan=False), min_size=4, max_size=4))
    n = math.sqrt(sum(c * c for c in comps))
    if n < 1e-3:
        return Quat()
    w, x, y, z = (c / n for c in comps)
    return Quat(w=w, x=x, y=y, z=z)


poses = st.builds(Pose, position=vec3, orientation=quats())
version_refs = st.builds(
    VersionRef,
    object_id=st.builds(new_object_id),
    semver=st.from_regex(r"\A(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]?)\.(0|[1-9][0-9]?)\Z"),
    content_hash=hexes,
)


@st.composite
def parts(draw: st.DrawFn) -> Part:
    ids = draw(st.lists(local_ids, unique=True, max_size=5))
    connectors = [
        Connector(
            id=i,
            type=draw(st.sampled_from(ConnectorType)),
            gender=draw(st.sampled_from(Gender)),
            pose=draw(poses),
        )
        for i in ids
    ]
    return Part(
        source=PartSource.PRINTED,
        name=draw(st.text(min_size=1, max_size=30).filter(lambda s: s.strip() != "")),
        mass_kg=draw(st.none() | positive),
        connectors=connectors,
    )


@st.composite
def assemblies(draw: st.DrawFn) -> Assembly:
    leaf_items: list[Item] = [
        PartInstance(id=i, part=draw(version_refs), pose=draw(poses))
        for i in draw(st.lists(local_ids, unique=True, min_size=1, max_size=5))
    ]
    leaf = Submodel(id="leaf", name="Leaf", items=leaf_items)
    root_items: list[Item] = [
        SubmodelInstance(
            id=f"i{n}",
            submodel="leaf",
            pose=draw(poses),
            mirrored=draw(st.none() | st.sampled_from(Axis)),
        )
        for n in range(draw(st.integers(1, 3)))
    ]
    root = Submodel(id="root", name="Root", items=root_items)
    return Assembly(root="root", submodels={"root": root, "leaf": leaf})


@st.composite
def tracks(draw: st.DrawFn) -> Track:
    floor = Polygon2D(points=draw(st.lists(vec2, min_size=3, max_size=8)))
    walls = draw(
        st.lists(st.builds(Wall, points=st.lists(vec2, min_size=2, max_size=5)), max_size=4)
    )
    return Track(floor=floor, walls=walls)


channel_values = st.one_of(
    st.booleans(), st.integers(-(2**31), 2**31), finite, st.text(max_size=10)
)

frames = st.builds(
    TelemetryFrame,
    t=st.builds(Timestamp, mono_ns=st.integers(0, 2**62)),
    seq=st.integers(0, 2**31),
    mode=st.sampled_from(Mode),
    state=st.from_regex(r"\A[a-z_]{1,12}\Z"),
    cmd=st.builds(Command, steering_rad=finite, speed_m_s=finite),
    meas=st.builds(
        Measured,
        sensors=st.dictionaries(
            local_ids, st.builds(RangeReading, distance_m=st.none() | positive)
        ),
    ),
    loop=st.builds(LoopStats, rate_hz=positive),
    channels=st.dictionaries(
        st.from_regex(r"\A[a-z][a-z0-9_.]{0,10}\Z"), channel_values, max_size=8
    ),
)

_ = Pose2D  # re-exported for future strategies
