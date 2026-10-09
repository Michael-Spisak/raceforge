"""Battery and power model (spec 0024): sag under load, state of charge, brownout."""

from dataclasses import dataclass

import numpy as np

from raceforge.core.devices import BatteryParams, MotorParams

# 2S Li-ion open-circuit voltage over state of charge (EV3 pack class); scaled by nominal_v / 7.4.
_SOC = np.array([0.0, 0.1, 0.2, 0.5, 0.8, 1.0])
_OCV = np.array([6.0, 6.9, 7.24, 7.6, 8.0, 8.4])
_REF_NOMINAL_V = 7.4

EV3_PACK = BatteryParams(
    nominal_v=7.4, capacity_wh=15.0, internal_resistance_ohm=0.25, max_current_a=6.0
)

LOW_SOC = 0.10
BROWNOUT_FRACTION = 0.80
RECOVER_FRACTION = 0.85


@dataclass(frozen=True)
class BatteryState:
    soc: float
    volts: float
    amps: float
    browned_out: bool


class BatteryModel:
    """One pack feeding the drive motors of a car."""

    def __init__(
        self, params: BatteryParams, motor: MotorParams, motors: int = 1, soc: float = 1.0
    ) -> None:
        self.params = params
        self.motor_nominal_v = motor.nominal_v
        self.k_e = motor.nominal_v / motor.no_load_speed_rad_s  # V per rad/s (motor shaft)
        self.r_motor = motor.nominal_v * self.k_e / motor.stall_torque_nm
        self.motors = motors
        self.soc = min(1.0, max(0.0, soc))
        self.amps = 0.0
        self.browned_out = False
        self.low_reported = False

    def ocv(self) -> float:
        return float(np.interp(self.soc, _SOC, _OCV)) * self.params.nominal_v / _REF_NOMINAL_V

    @property
    def volts(self) -> float:
        return max(0.0, self.ocv() - self.amps * self.params.internal_resistance_ohm)

    def drive_scale(self) -> float:
        """Fraction of the motors' nominal-voltage output that is available now (0 on brownout)."""
        if self.browned_out:
            return 0.0
        return min(1.0, self.volts / self.motor_nominal_v)

    def step(self, duty: float, motor_speed_rad_s: float, dt: float) -> bool:
        """Update with the applied duty and motor shaft speed; True when a brownout starts."""
        scale = self.drive_scale()
        v_motor = abs(duty) * scale * self.motor_nominal_v
        i_motor = max(0.0, (v_motor - self.k_e * abs(motor_speed_rad_s)) / self.r_motor)
        self.amps = min(i_motor * self.motors, self.params.max_current_a)
        self.soc = max(
            0.0, self.soc - self.volts * self.amps * dt / (self.params.capacity_wh * 3600)
        )
        started = False
        v = self.volts
        cutoff = BROWNOUT_FRACTION * self.params.nominal_v
        if not self.browned_out and (v < cutoff or self.soc <= 0.0):
            self.browned_out = started = True
        elif (
            self.browned_out
            and self.soc > 0.0
            and self.ocv() > RECOVER_FRACTION * self.params.nominal_v
        ):
            self.browned_out = False
        return started

    def state(self) -> BatteryState:
        return BatteryState(self.soc, self.volts, self.amps, self.browned_out)
