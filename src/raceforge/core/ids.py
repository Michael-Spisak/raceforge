"""UUIDv7 object identifiers (Python 3.12 has no ``uuid.uuid7``)."""

import os
import time
import uuid

UUID7_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"


def new_object_id(timestamp_ms: int | None = None) -> str:
    """Return a new RFC 9562 UUIDv7 as a lowercase string (time-ordered by milliseconds)."""
    ms = time.time_ns() // 1_000_000 if timestamp_ms is None else timestamp_ms
    if not 0 <= ms < 1 << 48:
        raise ValueError("timestamp_ms out of range for UUIDv7")
    rand = int.from_bytes(os.urandom(10), "big")
    rand_a = rand >> 68 & 0xFFF
    rand_b = rand & ((1 << 62) - 1)
    value = ms << 80 | 0x7 << 76 | rand_a << 64 | 0b10 << 62 | rand_b
    return str(uuid.UUID(int=value))
