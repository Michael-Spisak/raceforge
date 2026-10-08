"""Binary frames between the board and the EV3 (mirror of ``car_runtime/rf-proto/src/ev3.rs``).

Little endian, CRC-16/CCITT-FALSE trailer over everything before it::

    header: magic u16 = 0x5246 | version u8 = 1 | kind u8 | seq u32 | t_ms u32
    CommandFrame (kind 1, board -> EV3): steer_target_cdeg i16 | drive_speed_cps i16 | flags u8
                                         | led u8 | lcd u8 | reserved u8
    SensorFrame  (kind 2, EV3 -> board): ack_seq u32 | 4 x (tacho i32, speed_cps i16)
                                         | 4 x ultrasonic_mm u16 (0xFFFF = none) | gyro_rate_dps i16
                                         | gyro_angle_deg i32 | touch u8 | buttons u8
                                         | battery_mv u16 | flags u8 | reserved u8
"""

import struct

MAGIC = 0x5246
VERSION = 1
KIND_COMMAND = 1
KIND_SENSOR = 2
NO_ECHO = 0xFFFF

_HEADER = "<HBBII"
_COMMAND = struct.Struct(_HEADER + "hhBBBB")
_SENSOR = struct.Struct(_HEADER + "I" + "ih" * 4 + "HHHH" + "hi" + "BBH" + "BB")
COMMAND_LEN = _COMMAND.size + 2
SENSOR_LEN = _SENSOR.size + 2

# Command flags (board -> EV3).
CMD_STOP = 1 << 0
CMD_ESTOP = 1 << 1
CMD_RACE = 1 << 2

# Status flags (EV3 -> board).
ST_LINK_OK = 1 << 0
ST_ESTOP_PRESSED = 1 << 1
ST_FAILSAFE = 1 << 2

# LCD codes (rf-ev3 ``lcd`` module).
LCD_RUN = 0
LCD_STOPPED = 1
LCD_FAULT = 2


def crc16_ccitt(data):
    crc = 0xFFFF
    for byte in bytearray(data):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


class Command(object):
    __slots__ = ("drive_speed_cps", "flags", "lcd", "led", "seq", "steer_target_cdeg", "t_ms")

    def __init__(
        self, seq=0, t_ms=0, steer_target_cdeg=0, drive_speed_cps=0, flags=0, led=0, lcd=0
    ):
        self.seq = seq
        self.t_ms = t_ms
        self.steer_target_cdeg = steer_target_cdeg
        self.drive_speed_cps = drive_speed_cps
        self.flags = flags
        self.led = led
        self.lcd = lcd

    def __eq__(self, other):
        return isinstance(other, Command) and all(
            getattr(self, k) == getattr(other, k) for k in self.__slots__
        )

    def __repr__(self):
        return (
            "Command(" + ", ".join(k + "=" + repr(getattr(self, k)) for k in self.__slots__) + ")"
        )

    def encode(self):
        body = _COMMAND.pack(
            MAGIC,
            VERSION,
            KIND_COMMAND,
            self.seq & 0xFFFFFFFF,
            self.t_ms & 0xFFFFFFFF,
            self.steer_target_cdeg,
            self.drive_speed_cps,
            self.flags,
            self.led,
            self.lcd,
            0,
        )
        return body + struct.pack("<H", crc16_ccitt(body))

    @classmethod
    def decode(cls, data):
        """Return a Command, or None if the frame is invalid (length, magic, version, kind, CRC)."""
        f = _check(data, COMMAND_LEN, KIND_COMMAND)
        if f is None:
            return None
        v = _COMMAND.unpack(f[: _COMMAND.size])
        return cls(v[3], v[4], v[5], v[6], v[7], v[8], v[9])


class Sensors(object):
    __slots__ = (
        "ack_seq",
        "battery_mv",
        "buttons",
        "flags",
        "gyro_angle_deg",
        "gyro_rate_dps",
        "motors",
        "seq",
        "t_ms",
        "touch",
        "ultrasonic_mm",
    )

    def __init__(self, **kw):
        self.seq = kw.get("seq", 0)
        self.t_ms = kw.get("t_ms", 0)
        self.ack_seq = kw.get("ack_seq", 0)
        self.motors = kw.get("motors", [(0, 0)] * 4)  # (tacho, speed_cps) for ports A..D
        self.ultrasonic_mm = kw.get("ultrasonic_mm", [NO_ECHO] * 4)  # sensor ports 1..4
        self.gyro_rate_dps = kw.get("gyro_rate_dps", 0)
        self.gyro_angle_deg = kw.get("gyro_angle_deg", 0)
        self.touch = kw.get("touch", 0)
        self.buttons = kw.get("buttons", 0)
        self.battery_mv = kw.get("battery_mv", 0)
        self.flags = kw.get("flags", 0)

    def __eq__(self, other):
        return isinstance(other, Sensors) and all(
            list(getattr(self, k)) == list(getattr(other, k))
            if k in ("motors", "ultrasonic_mm")
            else getattr(self, k) == getattr(other, k)
            for k in self.__slots__
        )

    def __repr__(self):
        return (
            "Sensors(" + ", ".join(k + "=" + repr(getattr(self, k)) for k in self.__slots__) + ")"
        )

    def encode(self):
        values = [
            MAGIC,
            VERSION,
            KIND_SENSOR,
            self.seq & 0xFFFFFFFF,
            self.t_ms & 0xFFFFFFFF,
            self.ack_seq & 0xFFFFFFFF,
        ]
        for tacho, speed in self.motors:
            values += [_i32(tacho), _i16(speed)]
        values += [_u16(u) for u in self.ultrasonic_mm]
        values += [
            _i16(self.gyro_rate_dps),
            _i32(self.gyro_angle_deg),
            self.touch & 0xFF,
            self.buttons & 0xFF,
            _u16(self.battery_mv),
            self.flags & 0xFF,
            0,
        ]
        body = _SENSOR.pack(*values)
        return body + struct.pack("<H", crc16_ccitt(body))

    @classmethod
    def decode(cls, data):
        f = _check(data, SENSOR_LEN, KIND_SENSOR)
        if f is None:
            return None
        v = _SENSOR.unpack(f[: _SENSOR.size])
        motors = [(v[6 + 2 * i], v[7 + 2 * i]) for i in range(4)]
        return cls(
            seq=v[3],
            t_ms=v[4],
            ack_seq=v[5],
            motors=motors,
            ultrasonic_mm=list(v[14:18]),
            gyro_rate_dps=v[18],
            gyro_angle_deg=v[19],
            touch=v[20],
            buttons=v[21],
            battery_mv=v[22],
            flags=v[23],
        )


def _check(data, length, kind):
    if len(data) < length:
        return None
    f = bytes(data[:length])
    magic, version, k = struct.unpack("<HBB", f[:4])
    if magic != MAGIC or version != VERSION or k != kind:
        return None
    if struct.unpack("<H", f[-2:])[0] != crc16_ccitt(f[:-2]):
        return None
    return f


def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else int(x)


def _i16(x):
    return _clamp(x, -0x8000, 0x7FFF)


def _u16(x):
    return _clamp(x, 0, 0xFFFF)


def _i32(x):
    return _clamp(x, -0x80000000, 0x7FFFFFFF)
