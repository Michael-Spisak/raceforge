"""Bench check of an EV3 running the RaceForge EV3RT bridge (spec 0011), from a laptop.

``raceforge car ev3rt check --port /dev/cu.…`` replaces the car's board for a first hardware test:
link and frame rate, battery, buttons, sensors, steering, and with ``--drive`` (wheels off the
ground!) a short drive plus the link-loss failsafe (the bridge must brake within 150 ms when the
commands stop).
"""

import contextlib
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass

from raceforge.car.ev3_serial import ST_FAILSAFE, Command, LinkError, Sensor, SerialLink

Out = Callable[[str], None]
RATE_HZ = 50
STEER_TEST_DEG = 30  # steering motor degrees left and right of the start position
DRIVE_TEST_CPS = 360  # drive motor degrees per second (one wheel turn per second at 1:1)


@dataclass
class Step:
    name: str
    ok: bool
    detail: str


class Bench:
    def __init__(self, link: SerialLink, steer_port: int = 0, drive_port: int = 1) -> None:
        self.link = link
        self.steer = steer_port
        self.drive = drive_port
        self.last: Sensor | None = None

    def run(self, seconds: float, send: bool = True, **cmd: int) -> list[Sensor]:
        """Send ``cmd`` at :data:`RATE_HZ` for ``seconds`` (or stay silent) and collect frames."""
        frames: list[Sensor] = []
        end = time.monotonic() + seconds
        next_tx = time.monotonic()
        while (now := time.monotonic()) < end:
            if send and now >= next_tx:
                self.link.send(self.link.command(**cmd))
                next_tx += 1 / RATE_HZ
            frames += self.link.receive(timeout_s=max(0.0, min(next_tx, end) - now))
        if frames:
            self.last = frames[-1]
        return frames


def _us(v: int | None) -> str:
    return "-" if v is None else f"{v / 1000:.2f} m"


def bench_check(link: SerialLink, drive: bool = False, out: Out = print) -> list[Step]:
    b = Bench(link)
    steps: list[Step] = []

    def step(name: str, ok: bool, detail: str) -> None:
        steps.append(Step(name, ok, detail))
        out(f"[{'OK' if ok else 'FAIL'}] {name}: {detail}")

    # 1. Link: frames come back at ~100 Hz while we command STOP.
    frames = b.run(2.0, flags=1)
    rate = len(frames) / 2.0
    step(
        "link",
        rate > 50,
        f"{rate:.0f} frames/s from the EV3 (expect ~100), {link.stats.bad} bad",
    )
    if not frames:
        out(
            "no frames: is raceforge_bridge running on the brick (LCD 'RaceForge EV3RT') and is "
            "this the right port? Bluetooth: pair first, the LCD shows the link state."
        )
        return steps
    rtt = link.stats.rtt_ms
    if rtt:
        step("latency", statistics.median(rtt) < 60, f"median {statistics.median(rtt):.0f} ms")
    s = frames[-1]
    step(
        "battery",
        s.battery_mv > 6500,
        f"{s.battery_mv / 1000:.2f} V (below 6.5 V the motors are weak)",
    )
    sensors = ", ".join(
        f"port {i + 1}: {_us(v)}" for i, v in enumerate(s.ultrasonic_mm) if v is not None
    )
    step("ultrasonic", True, sensors or "no ultrasonic configured / no echo")
    step(
        "gyro + touch",
        True,
        f"rate {s.gyro_rate_dps} deg/s, angle {s.gyro_angle_deg} deg, touch bits {s.touch:04b}",
    )

    # 2. Buttons: ask the user to press ENTER within 10 s.
    out("press the EV3's centre button (ENTER) now ...")
    seen = False
    end = time.monotonic() + 10
    while time.monotonic() < end and not seen:
        seen = any("enter" in f.pressed() for f in b.run(0.2, flags=1))
    step("buttons", seen, "ENTER seen" if seen else "ENTER not seen within 10 s")

    # 3. Steering: the bridge holds the target while STOPPED, so this is safe on the bench.
    start = b.last.tacho[b.steer] if b.last else 0
    reached: list[int] = []
    for target in (STEER_TEST_DEG, -STEER_TEST_DEG, 0):
        fr = b.run(1.0, flags=1, steer_target_cdeg=(start + target) * 100)
        reached.append(fr[-1].tacho[b.steer] - start if fr else 0)
    errs = [abs(r - t) for r, t in zip(reached, (STEER_TEST_DEG, -STEER_TEST_DEG, 0), strict=True)]
    step(
        "steering",
        max(errs) <= 5,
        f"targets +{STEER_TEST_DEG}/-{STEER_TEST_DEG}/0 deg -> reached {reached} deg (motor)",
    )

    if not drive:
        out("drive test skipped (add --drive with the wheels off the ground)")
        b.run(0.3, flags=1)
        return steps

    # 4. Drive: wheels off the ground!
    fr = b.run(2.0, flags=0, lcd=0, drive_speed_cps=DRIVE_TEST_CPS)
    speed = statistics.median(f.speed_cps[b.drive] for f in fr[len(fr) // 2 :]) if fr else 0
    step(
        "drive",
        abs(abs(speed) - DRIVE_TEST_CPS) < DRIVE_TEST_CPS * 0.25,
        f"target {DRIVE_TEST_CPS} deg/s, measured {speed:.0f} deg/s (sign = wiring direction)",
    )

    # 5. Failsafe: stop sending while driving; the bridge must brake on its own.
    before = b.last.speed_cps[b.drive] if b.last else 0
    silent = b.run(0.6, send=False)
    tripped = any(f.flags & ST_FAILSAFE for f in silent)
    still = silent[-1].speed_cps[b.drive] if silent else before
    step(
        "failsafe",
        tripped and abs(still) < abs(before) * 0.3 + 30,
        f"commands stopped for 0.6 s: failsafe {'on' if tripped else 'NOT on'}, "
        f"speed {before} -> {still} deg/s",
    )
    b.run(0.5, flags=1)  # back to STOPPED
    return steps


def open_and_check(device: str, baud: int, drive: bool, out: Out = print) -> bool:
    try:
        link = SerialLink.open(device, baud)
    except LinkError as exc:
        out(f"error: {exc}")
        return False
    try:
        steps = bench_check(link, drive=drive, out=out)
    finally:
        with contextlib.suppress(OSError):
            link.send(Command(seq=link.seq + 1, t_ms=0))  # leave the car STOPPED
        link.close()
    ok = bool(steps) and all(s.ok for s in steps)
    out("all checks passed" if ok else "some checks failed (see above)")
    return ok
