# pyright: reportMissingImports=false
# (python-ev3dev2 only exists on the brick; tests inject a fake.)
"""EV3 hardware access via python-ev3dev2 (only imported on the brick).

Every sysfs access costs roughly a millisecond on the EV3, so only configured devices are read,
motor setpoints are written only when they change, and slow values (battery, buttons) are read
every ``slow_every`` cycles.
"""

MOTOR_PORTS = "ABCD"
NO_ECHO = 0xFFFF
US_MAX_MM = 2550  # EV3 ultrasonic reports 255.0 cm when nothing is in range

DEFAULT_CONFIG = {
    "port": 47100,
    "rate_hz": 100,
    "failsafe_ms": 150,
    "steer_motor": "A",
    "steer_speed_sp": 800,  # motor deg/s used to move the steering to its target
    "drive_motors": ["B"],  # several drive motors get the same speed
    "drive_invert": [False],
    "sensors": {"1": "ultrasonic", "2": "ultrasonic", "3": "ultrasonic", "4": "gyro"},
    "estop_touch_port": None,  # e.g. "4" when a touch sensor is the hardware e-stop
    "slow_every": 25,
}

BUTTON_BITS = {"up": 0, "down": 1, "left": 2, "right": 3, "enter": 4, "backspace": 5}


class Ev3Hardware(object):
    def __init__(self, config):
        # Imported here so the package can be tested on a dev machine without ev3dev2.
        from ev3dev2 import motor, sensor
        from ev3dev2.button import Button
        from ev3dev2.display import Display
        from ev3dev2.led import Leds
        from ev3dev2.power import PowerSupply
        from ev3dev2.sensor import lego

        cfg = dict(DEFAULT_CONFIG)
        cfg.update(config)
        self.cfg = cfg
        out_ports = {
            "A": motor.OUTPUT_A,
            "B": motor.OUTPUT_B,
            "C": motor.OUTPUT_C,
            "D": motor.OUTPUT_D,
        }
        in_ports = {
            "1": sensor.INPUT_1,
            "2": sensor.INPUT_2,
            "3": sensor.INPUT_3,
            "4": sensor.INPUT_4,
        }

        self.motors = {}  # port letter -> Motor
        names = [cfg["steer_motor"]] + list(cfg["drive_motors"])
        for name in names:
            if name not in self.motors:
                m = motor.Motor(out_ports[name])
                m.reset()  # position 0 = steering centred at start-up
                self.motors[name] = m
        self.steer_motor = self.motors[cfg["steer_motor"]]
        invert = list(cfg.get("drive_invert", [])) + [False] * len(cfg["drive_motors"])
        self.drive_motors = [
            (self.motors[n], -1 if invert[i] else 1) for i, n in enumerate(cfg["drive_motors"])
        ]
        self.drive_max = min(m.max_speed for m, _ in self.drive_motors)

        self.ultrasonic = {}  # index 0..3 -> sensor
        self.touch = {}
        self.gyro = None
        for port, kind in sorted(cfg["sensors"].items()):
            idx = int(port) - 1
            if kind == "ultrasonic":
                s = lego.UltrasonicSensor(in_ports[port])
                s.mode = "US-DIST-CM"
                self.ultrasonic[idx] = s
            elif kind == "gyro":
                g = lego.GyroSensor(in_ports[port])
                g.mode = "GYRO-G&A"
                self.gyro = g
            elif kind == "touch":
                self.touch[idx] = lego.TouchSensor(in_ports[port])
            else:
                raise ValueError("unknown sensor kind %r on port %s" % (kind, port))
        estop = cfg.get("estop_touch_port")
        self.estop_idx = None if estop is None else int(estop) - 1
        if self.estop_idx is not None and self.estop_idx not in self.touch:
            raise ValueError("estop_touch_port %s is not configured as a touch sensor" % estop)

        self.buttons = Button()
        self.power = PowerSupply()
        self.leds = Leds()
        self.display = Display()
        self._cycle = 0
        self._battery_mv = 0
        self._buttons = 0
        self._steer_target = None
        self._drive_speed = None  # None = braked

    def read(self):
        motors = [(0, 0)] * 4
        for name, m in self.motors.items():
            motors[MOTOR_PORTS.index(name)] = (m.position, m.speed)
        us = [NO_ECHO] * 4
        for idx, s in self.ultrasonic.items():
            mm = int(s.value(0))  # US-DIST-CM has one decimal: value is cm * 10 = mm
            us[idx] = NO_ECHO if mm <= 0 or mm >= US_MAX_MM else mm
        touch = 0
        for idx, s in self.touch.items():
            if s.is_pressed:
                touch |= 1 << idx
        if self._cycle % self.cfg["slow_every"] == 0:
            self._battery_mv = int(self.power.measured_volts * 1000)
            bits = 0
            for b in self.buttons.buttons_pressed:
                if b in BUTTON_BITS:
                    bits |= 1 << BUTTON_BITS[b]
            self._buttons = bits
        self._cycle += 1
        reading = {
            "motors": motors,
            "ultrasonic_mm": us,
            "touch": touch,
            "buttons": self._buttons,
            "battery_mv": self._battery_mv,
            "estop": self.estop_idx is not None and bool(touch & (1 << self.estop_idx)),
        }
        if self.gyro is not None:
            reading["gyro_angle_deg"] = int(self.gyro.value(0))
            reading["gyro_rate_dps"] = int(self.gyro.value(1))
        return reading

    def steer(self, target_cdeg):
        target = round(target_cdeg / 100.0)
        if target != self._steer_target:
            self.steer_motor.run_to_abs_pos(
                position_sp=target, speed_sp=self.cfg["steer_speed_sp"], stop_action="hold"
            )
            self._steer_target = target

    def drive(self, speed_cps):
        speed = max(-self.drive_max, min(self.drive_max, int(speed_cps)))
        if speed != self._drive_speed:
            for m, sign in self.drive_motors:
                m.run_forever(speed_sp=sign * speed)
            self._drive_speed = speed

    def brake(self):
        if self._drive_speed is not None:
            for m, _ in self.drive_motors:
                m.stop(stop_action="brake")
            self._drive_speed = None

    def show(self, status):
        color = {"RUN": "GREEN", "STOPPED": "AMBER"}.get(status, "RED")
        self.leds.set_color("LEFT", color)
        self.leds.set_color("RIGHT", color)
        self.display.clear()
        self.display.text_pixels("RaceForge", clear_screen=False, x=0, y=0)
        self.display.text_pixels(status, clear_screen=False, x=0, y=40, font="helvB18")
        self.display.update()

    def shutdown(self):
        self._drive_speed = 0  # force a brake of all drive motors
        self.brake()
        self.steer_motor.stop(stop_action="coast")
