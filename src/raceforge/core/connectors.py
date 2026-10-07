"""Connector types and the LEGO/printed-part compatibility table (spec 0001)."""

from enum import StrEnum


class ConnectorType(StrEnum):
    PIN_HOLE = "pin_hole"
    AXLE_HOLE = "axle_hole"
    PIN = "pin"
    AXLE = "axle"
    STUD = "stud"
    ANTI_STUD = "anti_stud"
    SCREW_HOLE = "screw_hole"
    FIXED_MOUNT = "fixed_mount"


class Gender(StrEnum):
    MALE = "male"
    FEMALE = "female"
    NEUTRAL = "neutral"


_C = ConnectorType
_COMPATIBLE: frozenset[frozenset[ConnectorType]] = frozenset(
    {
        frozenset({_C.PIN, _C.PIN_HOLE}),
        frozenset({_C.AXLE, _C.AXLE_HOLE}),
        frozenset({_C.AXLE, _C.PIN_HOLE}),  # axle through a round hole: rotating joint
        frozenset({_C.STUD, _C.ANTI_STUD}),
        frozenset({_C.SCREW_HOLE}),  # bolted together
        frozenset({_C.FIXED_MOUNT}),
        frozenset({_C.FIXED_MOUNT, _C.SCREW_HOLE}),
    }
)


def are_compatible(a: ConnectorType, b: ConnectorType) -> bool:
    """Return True if connectors of type ``a`` and ``b`` can be joined."""
    return frozenset({a, b}) in _COMPATIBLE
