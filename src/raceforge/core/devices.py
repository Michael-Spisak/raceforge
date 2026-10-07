"""Device roles attached to parts: sensors, motors, batteries, boards (spec 0001).

Parameters default to typical datasheet values; calibration overrides them per car.
"""

import math
from typing import Annotated, Literal

from pydantic import Field, PositiveFloat, PositiveInt, StringConstraints

from raceforge.core.base import CoreModel

NonNegFloat = Annotated[float, Field(ge=0)]
Fraction = Annotated[float, Field(ge=0, le=1)]


class PortAssignment(CoreModel):
    host: Annotated[str, StringConstraints(pattern=r"^(ev3:[1-4]|board)$")]
    port: Annotated[str, StringConstraints(min_length=1, max_length=64)]


class _DeviceBase(CoreModel):
    model: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    port: PortAssignment | None = None


class NoParams(CoreModel):
    pass


class RangeSensorParams(CoreModel):
    range_min_m: NonNegFloat
    range_max_m: PositiveFloat
    rate_hz: PositiveFloat
    fov_rad: PositiveFloat
    noise_std_m: NonNegFloat = 0.01


class GyroParams(CoreModel):
    rate_hz: PositiveFloat = 100.0
    noise_std_rad_s: NonNegFloat = 0.005
    drift_rad_s: NonNegFloat = 0.001


class ColorSensorParams(CoreModel):
    rate_hz: PositiveFloat = 100.0


class MotorParams(CoreModel):
    nominal_v: PositiveFloat
    stall_torque_nm: PositiveFloat
    no_load_speed_rad_s: PositiveFloat
    gear_ratio: PositiveFloat = 1.0
    has_encoder: bool = True


class EncoderParams(CoreModel):
    counts_per_rev: PositiveInt


class Lidar2DParams(CoreModel):
    rate_hz: PositiveFloat = 10.0
    angular_res_rad: PositiveFloat = math.radians(0.8)
    range_min_m: NonNegFloat = 0.02
    range_max_m: PositiveFloat = 12.0
    noise_std_m: NonNegFloat = 0.01


class CameraIntrinsics(CoreModel):
    fx: PositiveFloat
    fy: PositiveFloat
    cx: float
    cy: float
    distortion: list[float] = Field(default_factory=list[float], max_length=14)


class CameraParams(CoreModel):
    width_px: PositiveInt = 640
    height_px: PositiveInt = 480
    fov_h_rad: PositiveFloat = math.radians(66.0)
    rate_hz: PositiveFloat = 30.0
    rolling_shutter: bool = True
    intrinsics: CameraIntrinsics | None = None


class ImuParams(CoreModel):
    rate_hz: PositiveFloat = 200.0
    gyro_noise_std_rad_s: NonNegFloat = 0.002
    accel_noise_std_m_s2: NonNegFloat = 0.02


class BatteryParams(CoreModel):
    nominal_v: PositiveFloat
    capacity_wh: PositiveFloat
    internal_resistance_ohm: NonNegFloat = 0.05
    max_current_a: PositiveFloat = 10.0


class ComputeBoardParams(CoreModel):
    cpu_cores: PositiveInt = 4
    ram_gb: PositiveFloat = 4.0
    has_npu: bool = False


class RadioParams(CoreModel):
    kind: Literal["wifi", "bluetooth", "espnow"]
    removable: bool = True


_EV3_US = RangeSensorParams(
    range_min_m=0.03, range_max_m=2.55, rate_hz=20.0, fov_rad=math.radians(30), noise_std_m=0.01
)
_TOF = RangeSensorParams(
    range_min_m=0.04, range_max_m=4.0, rate_hz=50.0, fov_rad=math.radians(27), noise_std_m=0.005
)
_EV3_LARGE = MotorParams(nominal_v=9.0, stall_torque_nm=0.40, no_load_speed_rad_s=17.3)
_EV3_MEDIUM = MotorParams(nominal_v=9.0, stall_torque_nm=0.12, no_load_speed_rad_s=27.2)


class Ev3Brick(_DeviceBase):
    type: Literal["ev3_brick"] = "ev3_brick"
    params: NoParams = Field(default_factory=NoParams)


class Ev3UltrasonicSensor(_DeviceBase):
    type: Literal["ev3_ultrasonic"] = "ev3_ultrasonic"
    params: RangeSensorParams = _EV3_US


class Ev3GyroSensor(_DeviceBase):
    type: Literal["ev3_gyro"] = "ev3_gyro"
    params: GyroParams = Field(default_factory=GyroParams)


class Ev3TouchSensor(_DeviceBase):
    type: Literal["ev3_touch"] = "ev3_touch"
    params: NoParams = Field(default_factory=NoParams)


class Ev3ColorSensor(_DeviceBase):
    type: Literal["ev3_color"] = "ev3_color"
    params: ColorSensorParams = Field(default_factory=ColorSensorParams)


class Ev3LargeMotor(_DeviceBase):
    type: Literal["ev3_large_motor"] = "ev3_large_motor"
    params: MotorParams = _EV3_LARGE


class Ev3MediumMotor(_DeviceBase):
    type: Literal["ev3_medium_motor"] = "ev3_medium_motor"
    params: MotorParams = _EV3_MEDIUM


class DcMotor(_DeviceBase):
    type: Literal["dc_motor"] = "dc_motor"
    params: MotorParams


class Encoder(_DeviceBase):
    type: Literal["encoder"] = "encoder"
    params: EncoderParams


class Lidar2D(_DeviceBase):
    type: Literal["lidar_2d"] = "lidar_2d"
    params: Lidar2DParams = Field(default_factory=Lidar2DParams)


class Camera(_DeviceBase):
    type: Literal["camera"] = "camera"
    params: CameraParams = Field(default_factory=CameraParams)


class TofSensor(_DeviceBase):
    type: Literal["tof"] = "tof"
    params: RangeSensorParams = _TOF


class ImuSensor(_DeviceBase):
    type: Literal["imu"] = "imu"
    params: ImuParams = Field(default_factory=ImuParams)


class Battery(_DeviceBase):
    type: Literal["battery"] = "battery"
    params: BatteryParams


class ComputeBoard(_DeviceBase):
    type: Literal["compute_board"] = "compute_board"
    params: ComputeBoardParams = Field(default_factory=ComputeBoardParams)


class EmergencyStop(_DeviceBase):
    type: Literal["emergency_stop"] = "emergency_stop"
    params: NoParams = Field(default_factory=NoParams)


class RadioModule(_DeviceBase):
    type: Literal["radio"] = "radio"
    params: RadioParams


Device = Annotated[
    Ev3Brick
    | Ev3UltrasonicSensor
    | Ev3GyroSensor
    | Ev3TouchSensor
    | Ev3ColorSensor
    | Ev3LargeMotor
    | Ev3MediumMotor
    | DcMotor
    | Encoder
    | Lidar2D
    | Camera
    | TofSensor
    | ImuSensor
    | Battery
    | ComputeBoard
    | EmergencyStop
    | RadioModule,
    Field(discriminator="type"),
]
