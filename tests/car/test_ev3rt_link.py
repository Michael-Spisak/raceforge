"""Spec 0011: the laptop side of the EV3RT link (frames, COBS) and the bench check, end to end
against the real C bridge logic (ev3rt/tests/host_bridge.c, simulated motors) over a pseudo
terminal."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from raceforge_ev3 import protocol as ev3dev_protocol

from raceforge.car.ev3_serial import Command, Sensor, SerialLink, cobs_decode, cobs_encode
from raceforge.car.ev3rt_check import bench_check

EV3RT = Path(__file__).resolve().parents[2] / "ev3rt"
CC = shutil.which("cc")


def test_command_wire_bytes_match_c_and_rust() -> None:
    """Golden bytes asserted in car_runtime/rf-proto/src/cobs.rs and ev3rt/tests/test_bridge.c."""
    c = Command(
        seq=7, t_ms=1234, steer_target_cdeg=-1500, drive_speed_cps=360, flags=1, led=2, lcd=1
    )
    wire = cobs_encode(c.encode()) + b"\x00"
    assert wire.hex() == "064652010107010103d204010824fa680101020103244f00"


def test_sensor_frame_decodes_what_the_ev3dev_bridge_encodes() -> None:
    s = ev3dev_protocol.Sensors(
        seq=9,
        t_ms=5000,
        ack_seq=7,
        motors=[(1000, -200), (-50, 360), (0, 0), (5, 0)],
        ultrasonic_mm=[250, ev3dev_protocol.NO_ECHO, 2550, 0],
        gyro_rate_dps=-12,
        gyro_angle_deg=-370,
        touch=0b0001,
        buttons=0b010001,
        battery_mv=7900,
        flags=1,
    )
    got = Sensor.decode(cobs_decode(cobs_encode(s.encode())) or b"")
    assert (got.seq, got.ack_seq, got.tacho, got.speed_cps) == (
        9,
        7,
        [1000, -50, 0, 5],
        [-200, 360, 0, 0],
    )
    assert got.ultrasonic_mm == [250, None, 2550, 0] and got.gyro_angle_deg == -370
    assert got.pressed() == ["up", "enter"] and got.battery_mv == 7900


@pytest.mark.parametrize(
    "data", [b"", b"\x00", b"\x00\x00", bytes(range(1, 255)), bytes(600), bytes(range(256)) * 3]
)
def test_cobs_round_trip(data: bytes) -> None:
    enc = cobs_encode(data)
    assert 0 not in enc and cobs_decode(enc) == data


@pytest.mark.skipif(CC is None or os.name != "posix", reason="needs a C compiler and ptys")
def test_bench_check_against_the_c_bridge(tmp_path: Path) -> None:
    """Link, latency, sensors, buttons, steering, drive and the 150 ms link-loss failsafe."""
    subprocess.run(
        ["make", "-s", "-C", str(EV3RT / "tests"), f"OUT={tmp_path}", f"{tmp_path}/host_bridge"],
        check=True,
    )
    master, slave = os.openpty()
    proc = subprocess.Popen(
        [str(tmp_path / "host_bridge"), os.ttyname(slave)], stderr=subprocess.PIPE
    )
    os.set_blocking(master, False)
    link = SerialLink(master)
    lines: list[str] = []
    try:
        steps = bench_check(link, drive=True, out=lines.append)
    finally:
        link.close()
        os.close(slave)
        proc.kill()
        proc.wait()
    failed = [s for s in steps if not s.ok]
    assert {s.name for s in steps} >= {"link", "steering", "drive", "failsafe"}
    assert not failed, "\n".join(lines)
