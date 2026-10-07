"""Spec 0005 AC8: EV3-side loop with fake hardware, socket and clock (failsafe, e-stop, replies)."""

from typing import Any

import pytest
from raceforge_ev3 import protocol as p
from raceforge_ev3.bridge import Bridge, Failsafe

BOARD = ("10.42.0.1", 50000)


class FakeClock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


class FakeSocket:
    def __init__(self) -> None:
        self.inbox: list[tuple[bytes, Any]] = []
        self.sent: list[tuple[bytes, Any]] = []

    def recvfrom(self, n: int) -> tuple[bytes, Any]:
        if not self.inbox:
            raise BlockingIOError
        return self.inbox.pop(0)

    def sendto(self, data: bytes, addr: Any) -> None:
        self.sent.append((data, addr))


class FakeHw:
    def __init__(self) -> None:
        self.reading: dict[str, Any] = {"ultrasonic_mm": [500, p.NO_ECHO, 0, 0], "battery_mv": 7800}
        self.calls: list[tuple[str, Any]] = []
        self.shown: list[str] = []

    def read(self) -> dict[str, Any]:
        return dict(self.reading)

    def steer(self, cdeg: int) -> None:
        self.calls.append(("steer", cdeg))

    def drive(self, cps: int) -> None:
        self.calls.append(("drive", cps))

    def brake(self) -> None:
        self.calls.append(("brake", None))

    def show(self, status: str) -> None:
        self.shown.append(status)

    def last(self, kind: str) -> Any:
        return next(v for k, v in reversed(self.calls) if k == kind)


@pytest.fixture
def rig() -> tuple[Bridge, FakeHw, FakeSocket, FakeClock]:
    hw, sock, clock = FakeHw(), FakeSocket(), FakeClock()
    return Bridge(hw, sock, clock=clock), hw, sock, clock


def cmd(seq: int, steer: int = 1000, speed: int = 300, flags: int = 0, lcd: int = 0) -> bytes:
    return p.Command(
        seq=seq, steer_target_cdeg=steer, drive_speed_cps=speed, flags=flags, lcd=lcd
    ).encode()


def test_failsafe_timer() -> None:
    f = Failsafe(0.15)
    assert f.expired(0.0)  # no link yet
    f.feed(1.0)
    assert not f.expired(1.15)
    assert f.expired(1.151)


def test_no_link_brakes_and_sends_nothing(rig: Any) -> None:
    bridge, hw, sock, _ = rig
    frame = bridge.step()
    assert hw.calls == [("brake", None)]
    assert hw.shown == ["LINK LOST"]
    assert frame.flags & p.ST_FAILSAFE
    assert sock.sent == []  # no peer known yet


def test_valid_command_drives_and_replies_with_ack(rig: Any) -> None:
    bridge, hw, sock, _ = rig
    sock.inbox.append((cmd(5), BOARD))
    bridge.step()
    assert hw.last("steer") == 1000 and hw.last("drive") == 300
    assert hw.shown[-1] == "RUN"
    data, addr = sock.sent[-1]
    assert addr == BOARD
    reply = p.Sensors.decode(data)
    assert reply is not None
    assert reply.ack_seq == 5
    assert reply.flags & p.ST_LINK_OK and not reply.flags & p.ST_FAILSAFE
    assert reply.ultrasonic_mm == [500, p.NO_ECHO, 0, 0]
    assert reply.battery_mv == 7800


def test_failsafe_brakes_after_150ms_and_recovers(rig: Any) -> None:
    bridge, hw, sock, clock = rig
    sock.inbox.append((cmd(1), BOARD))
    bridge.step()
    clock.t += 0.14
    bridge.step()
    assert hw.calls[-1] == ("drive", 300) or hw.last("drive") == 300
    assert hw.shown[-1] == "RUN"
    clock.t += 0.02  # 160 ms since the last valid frame
    frame = bridge.step()
    assert hw.calls[-1] == ("brake", None)
    assert hw.shown[-1] == "LINK LOST"
    assert frame.flags & p.ST_FAILSAFE
    assert bridge.stats["failsafe_trips"] == 1
    # A restarted board (sequence starts over) is accepted after the failsafe.
    sock.inbox.append((cmd(1, speed=200), BOARD))
    bridge.step()
    assert hw.last("drive") == 200 and hw.shown[-1] == "RUN"


def test_corrupted_frames_do_not_feed_the_failsafe(rig: Any) -> None:
    bridge, hw, sock, clock = rig
    sock.inbox.append((cmd(1), BOARD))
    bridge.step()
    bad = bytearray(cmd(2))
    bad[-1] ^= 0xFF
    for _ in range(20):
        clock.t += 0.01
        sock.inbox.append((bytes(bad), BOARD))
        bridge.step()
    assert hw.shown[-1] == "LINK LOST"
    assert bridge.stats["rx_bad"] == 20


def test_stop_and_fault_flags_brake(rig: Any) -> None:
    bridge, hw, sock, _ = rig
    sock.inbox.append((cmd(1, flags=p.CMD_STOP, lcd=p.LCD_FAULT), BOARD))
    bridge.step()
    # Brake the drive, keep holding the steering target.
    assert hw.calls[-2:] == [("brake", None), ("steer", 1000)]
    assert ("drive", 300) not in hw.calls
    assert hw.shown[-1] == "FAULT"
    sock.inbox.append((cmd(2, flags=p.CMD_STOP, lcd=p.LCD_STOPPED), BOARD))
    bridge.step()
    assert hw.shown[-1] == "STOPPED"


def test_local_estop_overrides_drive_commands(rig: Any) -> None:
    bridge, hw, sock, _ = rig
    hw.reading["estop"] = True
    sock.inbox.append((cmd(1), BOARD))
    frame = bridge.step()
    assert ("drive", 300) not in hw.calls
    assert hw.calls[-1] == ("steer", 1000) and ("brake", None) in hw.calls
    assert hw.shown[-1] == "E-STOP"
    assert frame.flags & p.ST_ESTOP_PRESSED


def test_newest_frame_wins_and_reordered_frames_are_ignored(rig: Any) -> None:
    bridge, hw, sock, _ = rig
    sock.inbox += [(cmd(10, speed=100), BOARD), (cmd(11, speed=110), BOARD)]
    bridge.step()
    assert hw.last("drive") == 110
    sock.inbox.append((cmd(9, speed=90), BOARD))  # late, older frame
    bridge.step()
    assert hw.last("drive") == 110
    assert bridge.cmd.seq == 11


def test_lcd_only_redrawn_on_change(rig: Any) -> None:
    bridge, hw, sock, clock = rig
    for seq in range(1, 6):
        clock.t += 0.01
        sock.inbox.append((cmd(seq), BOARD))
        bridge.step()
    assert hw.shown == ["RUN"]


def test_run_brakes_on_exit(rig: Any) -> None:
    bridge, hw, _, _ = rig
    n = {"steps": 0}

    def stop_after_three() -> bool:
        n["steps"] += 1
        return n["steps"] > 3

    bridge.period_s = 0.0
    bridge.run(stop_after_three)
    assert hw.calls[-1] == ("brake", None)
    assert hw.shown[-1] == "STOPPED"
