"""Deploy bundle for the car runtime (spec 0005 "Deploy").

A bundle is a directory the Rust runtime (``rf-runtime --bundle DIR``) runs from::

    bundle.json        manifest (this module's :class:`BundleManifest`)
    controller.py      the controller file, unchanged
    controller.yaml    its parameters (optional)

Every file is listed with its SHA-256 in the manifest; the runtime refuses to start when a hash does
not match, so the car always drives exactly what was deployed. Field names and units mirror
``car_runtime/rf-runtime/src/manifest.rs``.
"""

import hashlib
import ipaddress
import shutil
import time
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

import raceforge
from raceforge.control.controller import load_controller
from raceforge.control.types import RobotInfo

MANIFEST = "bundle.json"
MotorPort = Literal["A", "B", "C", "D"]
SensorPort = Literal["1", "2", "3", "4"]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FileRef(_Model):
    file: str
    sha256: Sha256


class RobotSpec(_Model):
    """``RobotInfo`` as the runtime hands it to the controller (SI units)."""

    car_name: str = Field(min_length=1)
    sensors: list[str]
    max_steer_rad: float = Field(gt=0)
    max_speed_m_s: float = Field(gt=0)
    wheelbase_m: float = Field(gt=0)
    track_m: float = Field(gt=0)
    control_rate_hz: float = Field(ge=20, le=100)

    @classmethod
    def from_info(cls, info: RobotInfo) -> "RobotSpec":
        return cls(
            car_name=info.car_name,
            sensors=list(info.sensors),
            max_steer_rad=info.max_steer_rad,
            max_speed_m_s=info.max_speed_m_s,
            wheelbase_m=info.wheelbase_m,
            track_m=info.track_m,
            control_rate_hz=info.control_rate_hz,
        )


class Ev3Spec(_Model):
    """How the car is wired to the EV3 and how motor units map to SI."""

    addr: str = "10.42.0.3:47100"  # EV3 program (ev3_side) on the USB-Ethernet gadget
    local: str = "0.0.0.0:47101"
    steer_motor: MotorPort = "A"
    drive_motor: MotorPort = "B"
    steer_motor_deg_per_rad: float  # gear ratio, sign included
    drive_counts_per_m: float  # tacho degrees per metre travelled, sign included
    ultrasonic: dict[str, SensorPort] = Field(default_factory=dict[str, SensorPort])
    gyro: bool = True
    estop_touch_port: SensorPort | None = None
    link_timeout_ms: int = Field(default=100, ge=20, le=1000)
    # EV3 button that restarts the controller after a fault when held for 1 s.
    resume_button: Literal["up", "down", "left", "right", "enter", "backspace"] = "enter"


class LidarSpec(_Model):
    """LD06/LD19 on a UART. ``critical``: no scan for ``timeout_ms`` stops the car;
    ``optional``: the car continues at half speed without LiDAR data."""

    device: str = "/dev/ttyUSB0"
    mount_offset_rad: float = 0.0  # CCW angle of the sensor's zero mark from the forward axis
    policy: Literal["critical", "optional"] = "critical"
    timeout_ms: int = Field(default=300, ge=150, le=2000)


class TelemetrySpec(_Model):
    """Live telemetry + teleop WebSocket server (test mode only; never started in race mode).

    Anything that can reach a non-loopback address can steer the car, so binding to one requires
    a token; clients connect to ``ws://<car>:<port>/?token=<token>``.
    """

    bind: str = "0.0.0.0:8765"
    rate_hz: float = Field(default=20.0, ge=1, le=50)
    token: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{16,128}$")] | None = None
    max_clients: int = Field(default=4, ge=1, le=16)

    @model_validator(mode="after")
    def _token_unless_loopback(self) -> "TelemetrySpec":
        host, _, port = self.bind.rpartition(":")
        try:
            ip = ipaddress.ip_address(host.strip("[]"))
            port_ok = 0 <= int(port) <= 65535
        except ValueError as e:
            raise ValueError(f"bind must be IP:port, got {self.bind!r}") from e
        if not port_ok:
            raise ValueError(f"bad port in {self.bind!r}")
        if not ip.is_loopback and self.token is None:
            raise ValueError("a token is required when binding to a non-loopback address")
        return self


UsbId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{4}:[0-9a-f]{4}$")]


class RuntimeSpec(_Model):
    mode: Literal["test", "race"] = "test"
    deadline_ms: float = Field(default=15.0, gt=0, le=50)
    test_speed_limit_m_s: float | None = Field(default=None, gt=0)
    # Race mode refuses to arm while any radio may be active (spec 0005 AC5). USB dongles that
    # do not advertise the wireless USB class are listed here as "vendor:product" (lowercase hex).
    radio_usb_ids: list[UsbId] = Field(default_factory=list[UsbId])


class BundleManifest(_Model):
    schema_: Literal["car_bundle"] = Field(default="car_bundle", alias="schema")
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1)
    created_wall_ns: int
    raceforge_version: str
    controller: FileRef
    params: FileRef | None = None
    robot: RobotSpec
    ev3: Ev3Spec
    lidar: LidarSpec | None = None
    telemetry: TelemetrySpec | None = None
    runtime: RuntimeSpec = RuntimeSpec()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_bundle(
    out_dir: Path,
    controller: Path,
    robot: RobotSpec,
    ev3: Ev3Spec,
    runtime: RuntimeSpec | None = None,
    params: Path | None = None,
    lidar: LidarSpec | None = None,
    name: str | None = None,
    telemetry: TelemetrySpec | None = None,
) -> BundleManifest:
    """Copy controller (+ params) into ``out_dir`` and write the manifest with file hashes.

    The controller is loaded once with its parameters, so a broken file or invalid parameters
    fail here on the dev machine instead of on the car.
    """
    params = params if params is not None else controller.with_suffix(".yaml")
    load_controller(controller, params if params.is_file() else None)
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(controller, out_dir / "controller.py")
    params_ref = None
    if params.is_file():
        shutil.copyfile(params, out_dir / "controller.yaml")
        copied = out_dir / "controller.yaml"
        params_ref = FileRef(file="controller.yaml", sha256=sha256_file(copied))
    manifest = BundleManifest(
        name=name or controller.stem,
        created_wall_ns=time.time_ns(),
        raceforge_version=raceforge.__version__,
        controller=FileRef(file="controller.py", sha256=sha256_file(out_dir / "controller.py")),
        params=params_ref,
        robot=robot,
        ev3=ev3,
        lidar=lidar,
        telemetry=telemetry,
        runtime=runtime or RuntimeSpec(),
    )
    (out_dir / MANIFEST).write_text(
        manifest.model_dump_json(by_alias=True, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def load_manifest(bundle_dir: Path) -> BundleManifest:
    return BundleManifest.model_validate_json((bundle_dir / MANIFEST).read_text(encoding="utf-8"))


def verify_bundle(bundle_dir: Path) -> list[str]:
    """Problems found (missing files, hash mismatches); empty when the bundle is intact."""
    m = load_manifest(bundle_dir)
    problems: list[str] = []
    for ref in [m.controller] + ([m.params] if m.params else []):
        path = bundle_dir / ref.file
        if not path.is_file():
            problems.append(f"missing {ref.file}")
        elif sha256_file(path) != ref.sha256:
            problems.append(f"hash mismatch: {ref.file}")
    return problems
