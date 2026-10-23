"""Talk to the EV3RT bridge app from a computer (spec 0011): rf-proto v1 frames over a serial link.

The car runtime (Rust) is the normal peer of the bridge. This module lets a laptop take the
board's place for bench tests: Bluetooth SPP (the brick paired with the computer) or a USB-to-UART
adapter on sensor port 1. Frames are bit-identical to ``ev3rt/common/rf_proto.c`` and
``car_runtime/rf-proto`` (COBS-framed, 0x00 delimiter, little endian, CRC-16/CCITT-FALSE).
POSIX only (termios): macOS and Linux.
"""

import contextlib
import os
import select
import struct
import termios
import time
import tty
from dataclasses import dataclass, field

MAGIC = 0x5246
VERSION = 1
KIND_COMMAND = 1
KIND_SENSOR = 2
NO_ECHO = 0xFFFF
CMD_STOP = 1 << 0
CMD_ESTOP = 1 << 1
ST_LINK_OK = 1 << 0
ST_ESTOP_PRESSED = 1 << 1
ST_FAILSAFE = 1 << 2
BUTTONS = ("up", "down", "left", "right", "enter", "back")

_HEADER = "<HBBII"
_COMMAND = struct.Struct(_HEADER + "hhBBBB")
_SENSOR = struct.Struct(_HEADER + "I" + "ih" * 4 + "HHHH" + "hi" + "BBH" + "BB")


class LinkError(RuntimeError):
    pass


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def cobs_encode(data: bytes) -> bytes:
    out = bytearray([0])
    code_at, code = 0, 1
    for byte in data:
        if byte == 0:
            out[code_at] = code
            code_at, code = len(out), 1
            out.append(0)
            continue
        out.append(byte)
        code += 1
        if code == 0xFF:
            out[code_at] = code
            code_at, code = len(out), 1
            out.append(0)
    out[code_at] = code
    return bytes(out)


def cobs_decode(data: bytes) -> bytes | None:
    """Inverse of :func:`cobs_encode` (without the delimiter); ``None`` for an invalid packet."""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        code = data[i]
        if code == 0:
            return None
        block = data[i + 1 : i + code]
        if len(block) != code - 1:
            return None
        out += block
        i += code
        if code < 0xFF and i < n:
            out.append(0)
    return bytes(out)


@dataclass
class Command:
    seq: int
    t_ms: int
    steer_target_cdeg: int = 0  # steering motor target, centi-degrees of motor rotation
    drive_speed_cps: int = 0  # drive motor target, tacho degrees per second
    flags: int = CMD_STOP
    led: int = 0
    lcd: int = 1  # 0 run, 1 stopped, 2 fault

    def encode(self) -> bytes:
        body = _COMMAND.pack(
            MAGIC,
            VERSION,
            KIND_COMMAND,
            self.seq & 0xFFFFFFFF,
            self.t_ms & 0xFFFFFFFF,
            max(-32768, min(32767, self.steer_target_cdeg)),
            max(-32768, min(32767, self.drive_speed_cps)),
            self.flags,
            self.led,
            self.lcd,
            0,
        )
        return body + struct.pack("<H", crc16_ccitt(body))


@dataclass
class Sensor:
    seq: int
    t_ms: int
    ack_seq: int
    tacho: list[int]
    speed_cps: list[int]
    ultrasonic_mm: list[int | None]
    gyro_rate_dps: int
    gyro_angle_deg: int
    touch: int
    buttons: int
    battery_mv: int
    flags: int

    @classmethod
    def decode(cls, data: bytes) -> "Sensor":
        if len(data) != _SENSOR.size + 2:
            raise LinkError(f"sensor frame of {len(data)} bytes")
        if crc16_ccitt(data[:-2]) != struct.unpack("<H", data[-2:])[0]:
            raise LinkError("sensor frame CRC")
        v = _SENSOR.unpack(data[:-2])
        if v[0] != MAGIC or v[1] != VERSION or v[2] != KIND_SENSOR:
            raise LinkError("not a sensor frame")
        motors = v[6:14]
        return cls(
            seq=v[3],
            t_ms=v[4],
            ack_seq=v[5],
            tacho=list(motors[0::2]),
            speed_cps=list(motors[1::2]),
            ultrasonic_mm=[None if u == NO_ECHO else u for u in v[14:18]],
            gyro_rate_dps=v[18],
            gyro_angle_deg=v[19],
            touch=v[20],
            buttons=v[21],
            battery_mv=v[22],
            flags=v[23],
        )

    def pressed(self) -> list[str]:
        return [name for i, name in enumerate(BUTTONS) if self.buttons & (1 << i)]


@dataclass
class LinkStats:
    frames: int = 0
    bad: int = 0
    rtt_ms: list[float] = field(default_factory=list[float])


class SerialLink:
    """rf-proto over a serial device (``/dev/cu.*`` on macOS; ``/dev/rfcomm0`` or a UART on
    Linux)."""

    def __init__(self, fd: int) -> None:
        """Use :meth:`open` for a device; ``fd`` is an open, non-blocking file descriptor."""
        self.fd = fd
        self.buf = bytearray()
        self.stats = LinkStats()
        self.seq = 0
        self.t0 = time.monotonic()
        self._sent: dict[int, float] = {}

    @classmethod
    def open(cls, device: str, baud: int = 115_200) -> "SerialLink":
        try:
            fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as exc:
            raise LinkError(f"cannot open {device}: {exc.strerror}") from exc
        try:
            tty.setraw(fd)
            attrs = termios.tcgetattr(fd)
            speed = getattr(termios, f"B{baud}", termios.B115200)
            attrs[4] = attrs[5] = speed  # ispeed, ospeed (ignored by Bluetooth SPP)
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        except termios.error as exc:
            os.close(fd)
            raise LinkError(f"{device} is not a serial port: {exc}") from exc
        return cls(fd)

    def close(self) -> None:
        os.close(self.fd)

    def send(self, cmd: Command) -> None:
        os.write(self.fd, cobs_encode(cmd.encode()) + b"\x00")
        self._sent[cmd.seq] = time.monotonic()
        if len(self._sent) > 500:
            for k in sorted(self._sent)[:250]:
                del self._sent[k]

    def command(self, **kw: int) -> Command:
        self.seq += 1
        return Command(seq=self.seq, t_ms=int((time.monotonic() - self.t0) * 1000), **kw)

    def receive(self, timeout_s: float = 0.0) -> list[Sensor]:
        """Sensor frames that arrived (waits up to ``timeout_s`` for the first bytes)."""
        r, _, _ = select.select([self.fd], [], [], timeout_s)
        if r:
            with contextlib.suppress(BlockingIOError):
                self.buf += os.read(self.fd, 4096)
        frames: list[Sensor] = []
        while b"\x00" in self.buf:
            i = self.buf.index(0)
            packet, self.buf = bytes(self.buf[:i]), self.buf[i + 1 :]
            raw = cobs_decode(packet) if packet else None
            if raw is None:
                self.stats.bad += packet != b""
                continue
            try:
                s = Sensor.decode(raw)
            except LinkError:
                self.stats.bad += 1
                continue
            self.stats.frames += 1
            sent = self._sent.get(s.ack_seq)
            if sent is not None:
                self.stats.rtt_ms.append((time.monotonic() - sent) * 1000)
            frames.append(s)
        return frames
