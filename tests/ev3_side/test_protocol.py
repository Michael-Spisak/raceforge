"""Spec 0005 AC8: EV3-side protocol, byte-compatible with rf-proto (golden frames shared)."""

import re
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st
from raceforge_ev3 import DEFAULT_PORT
from raceforge_ev3 import protocol as p

# The same bytes are asserted in car_runtime/rf-proto/src/ev3.rs (golden_frames_match_python).
GOLDEN_COMMAND = "46520101070000006400000034125efe05010200c66e"
GOLDEN_SENSOR = (
    "465201020a000000c80000000700000040e20100100000000000000000000000000000000000"
    "0000a401ffff00000000d2fff9ffffff0803b81e01007f78"
)


def golden_command() -> p.Command:
    return p.Command(
        seq=7, t_ms=100, steer_target_cdeg=0x1234, drive_speed_cps=-418, flags=5, led=1, lcd=2
    )


def golden_sensor() -> p.Sensors:
    return p.Sensors(
        seq=10,
        t_ms=200,
        ack_seq=7,
        motors=[(123456, 16), (0, 0), (0, 0), (0, 0)],
        ultrasonic_mm=[420, p.NO_ECHO, 0, 0],
        gyro_rate_dps=-46,
        gyro_angle_deg=-7,
        touch=8,
        buttons=3,
        battery_mv=7864,
        flags=1,
    )


def test_golden_frames() -> None:
    assert golden_command().encode().hex() == GOLDEN_COMMAND
    assert golden_sensor().encode().hex() == GOLDEN_SENSOR
    assert len(bytes.fromhex(GOLDEN_COMMAND)) == p.COMMAND_LEN
    assert len(bytes.fromhex(GOLDEN_SENSOR)) == p.SENSOR_LEN


def test_crc_check_value() -> None:
    assert p.crc16_ccitt(b"123456789") == 0x29B1


@given(
    st.integers(0, 2**32 - 1),
    st.integers(-(2**15), 2**15 - 1),
    st.integers(-(2**15), 2**15 - 1),
    st.integers(0, 255),
)
def test_command_roundtrip(seq: int, steer: int, speed: int, flags: int) -> None:
    c = p.Command(seq=seq, steer_target_cdeg=steer, drive_speed_cps=speed, flags=flags)
    assert p.Command.decode(c.encode()) == c


@given(
    st.lists(
        st.tuples(st.integers(-(2**31), 2**31 - 1), st.integers(-(2**15), 2**15 - 1)),
        min_size=4,
        max_size=4,
    ),
    st.lists(st.integers(0, 0xFFFF), min_size=4, max_size=4),
    st.integers(0, 2**32 - 1),
)
def test_sensor_roundtrip(motors: list[tuple[int, int]], us: list[int], ack: int) -> None:
    f = p.Sensors(seq=1, ack_seq=ack, motors=motors, ultrasonic_mm=us, battery_mv=7000)
    assert p.Sensors.decode(f.encode()) == f


@given(st.integers(0, p.SENSOR_LEN - 1), st.integers(0, 7))
def test_any_bit_flip_is_rejected(idx: int, bit: int) -> None:
    data = bytearray(golden_sensor().encode())
    data[idx] ^= 1 << bit
    assert p.Sensors.decode(bytes(data)) is None


@given(st.binary(max_size=64))
def test_garbage_never_raises(data: bytes) -> None:
    p.Command.decode(data)
    p.Sensors.decode(data)


def test_wrong_kind_and_out_of_range_values() -> None:
    assert p.Command.decode(golden_sensor().encode()) is None
    assert p.Sensors.decode(golden_command().encode()) is None
    # Out-of-range values are clamped, never raise on the brick.
    f = p.Sensors(ultrasonic_mm=[70000, -5, 0, 0], gyro_rate_dps=99999, motors=[(2**40, 0)] * 4)
    d = p.Sensors.decode(f.encode())
    assert d is not None
    assert d.ultrasonic_mm == [0xFFFF, 0, 0, 0]
    assert d.gyro_rate_dps == 0x7FFF
    assert d.motors[0][0] == 2**31 - 1


def test_constants_match_rust() -> None:
    root = Path(__file__).parents[2] / "car_runtime"
    ev3_rs = (root / "rf-ev3" / "src" / "lib.rs").read_text()
    m = re.search(r"pub const DEFAULT_PORT: u16 = (\d+);", ev3_rs)
    assert m is not None and int(m.group(1)) == DEFAULT_PORT
    for name, value in [("RUN", p.LCD_RUN), ("STOPPED", p.LCD_STOPPED), ("FAULT", p.LCD_FAULT)]:
        assert re.search(rf"pub const {name}: u8 = {value};", ev3_rs), name
