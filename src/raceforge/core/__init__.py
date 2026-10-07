"""raceforge.core — shared data contracts (spec 0001: docs/specs/0001-core-schemas.md).

Conventions: right-handed Z-up frame, SI units, unit quaternions (w, x, y, z), UUIDv7 ids,
canonical JSON with SHA-256 content hashes, strict validation, versioned schemas with migrations.
"""

from raceforge.core.assembly import Assembly, AssemblyError, Submodel
from raceforge.core.io import content_hash, dump, dump_fast, load, load_as
from raceforge.core.parts import Part
from raceforge.core.telemetry import RunLog, TelemetryFrame
from raceforge.core.track import Track

__all__ = [
    "Assembly",
    "AssemblyError",
    "Part",
    "RunLog",
    "Submodel",
    "TelemetryFrame",
    "Track",
    "content_hash",
    "dump",
    "dump_fast",
    "load",
    "load_as",
]
