"""The EV3-side loop: UDP frames from the board -> motors; sensors -> UDP frames to the board.

Safety on the EV3 itself (independent of the board):

- **Failsafe:** no valid command frame for ``failsafe_s`` (spec: 150 ms) -> drive motor brakes,
  LCD shows "LINK LOST". Driving resumes only with the next valid frame.
- **Local e-stop:** if a touch sensor is configured as e-stop and pressed, the drive motor brakes
  immediately and drive commands are ignored while it is pressed (the board is told via flags).

All hardware access goes through a ``hw`` object (``raceforge_ev3.hw.Ev3Hardware`` on the brick,
a fake in tests), and time through ``clock`` so the logic is testable on a dev machine.
"""

import socket
import time

from raceforge_ev3 import protocol as p


class Failsafe(object):
    """Expires when no ``feed()`` happened for ``timeout_s``. Starts expired (no link yet)."""

    def __init__(self, timeout_s=0.15):
        self.timeout_s = timeout_s
        self.last = None

    def feed(self, now):
        self.last = now

    def expired(self, now):
        return self.last is None or now - self.last > self.timeout_s


class Bridge(object):
    def __init__(self, hw, sock, clock=time.monotonic, failsafe_s=0.15, period_s=0.01):
        self.hw = hw
        self.sock = sock
        self.clock = clock
        self.failsafe = Failsafe(failsafe_s)
        self.period_s = period_s
        self.peer = None
        self.cmd = p.Command(flags=p.CMD_STOP)
        self.seq = 0
        self.t0 = clock()
        self.status = None  # last status shown on the LCD
        self.stats = {"rx": 0, "rx_bad": 0, "tx": 0, "failsafe_trips": 0}
        self._was_expired = True

    def _drain(self):
        """Read all pending datagrams; keep the newest valid command."""
        while True:
            try:
                data, addr = self.sock.recvfrom(256)
            except (BlockingIOError, socket.timeout):
                return
            except OSError:
                return
            cmd = p.Command.decode(data)
            if cmd is None:
                self.stats["rx_bad"] += 1
                continue
            # Ignore reordered (older) frames while the link is up. After a failsafe (e.g. the
            # board restarted and its sequence starts over) any valid frame is accepted.
            behind = (self.cmd.seq - cmd.seq) & 0xFFFFFFFF
            if 0 < behind < 1000 and not self.failsafe.expired(self.clock()):
                continue
            self.stats["rx"] += 1
            self.peer = addr
            self.cmd = cmd
            self.failsafe.feed(self.clock())

    def step(self):
        """One cycle: receive, apply (with failsafe and e-stop), read sensors, reply."""
        self._drain()
        now = self.clock()
        expired = self.failsafe.expired(now)
        if expired and not self._was_expired:
            self.stats["failsafe_trips"] += 1
        self._was_expired = expired
        reading = self.hw.read()
        estop = bool(reading.get("estop"))

        if expired:
            self.hw.brake()
            self._show("LINK LOST")
        elif estop or self.cmd.flags & (p.CMD_STOP | p.CMD_ESTOP):
            self.hw.brake()
            self.hw.steer(self.cmd.steer_target_cdeg)
            if estop:
                self._show("E-STOP")
            else:
                self._show("FAULT" if self.cmd.lcd == p.LCD_FAULT else "STOPPED")
        else:
            self.hw.steer(self.cmd.steer_target_cdeg)
            self.hw.drive(self.cmd.drive_speed_cps)
            self._show("RUN")

        flags = 0
        if not expired:
            flags |= p.ST_LINK_OK
        else:
            flags |= p.ST_FAILSAFE
        if estop:
            flags |= p.ST_ESTOP_PRESSED
        self.seq = (self.seq + 1) & 0xFFFFFFFF
        frame = p.Sensors(
            seq=self.seq,
            t_ms=int((now - self.t0) * 1000),
            ack_seq=self.cmd.seq,
            motors=reading.get("motors", [(0, 0)] * 4),
            ultrasonic_mm=reading.get("ultrasonic_mm", [p.NO_ECHO] * 4),
            gyro_rate_dps=reading.get("gyro_rate_dps", 0),
            gyro_angle_deg=reading.get("gyro_angle_deg", 0),
            touch=reading.get("touch", 0),
            buttons=reading.get("buttons", 0),
            battery_mv=reading.get("battery_mv", 0),
            flags=flags,
        )
        if self.peer is not None:
            try:
                self.sock.sendto(frame.encode(), self.peer)
                self.stats["tx"] += 1
            except OSError:
                pass
        return frame

    def _show(self, status):
        if status != self.status:
            self.status = status
            self.hw.show(status)

    def run(self, should_stop=lambda: False):
        next_t = self.clock()
        try:
            while not should_stop():
                self.step()
                next_t += self.period_s
                delay = next_t - self.clock()
                if delay > 0:
                    time.sleep(delay)
                else:
                    next_t = self.clock()  # overrun: do not try to catch up
        finally:
            self.hw.brake()
            self.hw.show("STOPPED")


def open_socket(port, host="0.0.0.0"):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.setblocking(False)
    return s
