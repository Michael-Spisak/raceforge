"""Record simulated runs as RunLog + MCAP of TelemetryFrames (spec 0003, ADR-0013)."""

import hashlib
import json
import math
from pathlib import Path
from typing import IO

from mcap.writer import Writer

from raceforge.core.ids import new_object_id
from raceforge.core.io import json_schemas
from raceforge.core.primitives import BlobRef, Timestamp, VersionRef
from raceforge.core.telemetry import (
    BoolReading,
    Command,
    ImuSample,
    LoopStats,
    Measured,
    Mode,
    PowerStatus,
    RangeArray,
    RangeReading,
    RunKind,
    RunLog,
    SensorReading,
    TelemetryFrame,
)
from raceforge.sim.engine import CarCommand, Readings, Truth

TELEMETRY_TOPIC = "/telemetry"
TRUTH_TOPIC = "/truth"


def frame_from(
    seq: int,
    readings: Readings,
    cmd: CarCommand,
    state: str,
    loop_hz: float,
    channels: dict[str, float | int | bool | str] | None = None,
    mode: Mode = Mode.SIM,
) -> TelemetryFrame:
    sensors: dict[str, SensorReading] = {}
    for name, value in readings.ultrasonic_m.items():
        sensors[f"us_{name}"] = RangeReading(distance_m=None if value is None else value.value)
    if readings.lidar is not None:
        scan = readings.lidar
        inc = float(scan.angles_rad[1] - scan.angles_rad[0]) if len(scan.angles_rad) > 1 else 0.0
        sensors["lidar"] = RangeArray(
            angle_min_rad=float(scan.angles_rad[0]), angle_increment_rad=inc, ranges=scan.ranges_m
        )
    if readings.yaw_rate_rad_s is not None:
        sensors["gyro"] = ImuSample(yaw_rate_rad_s=readings.yaw_rate_rad_s.value)
    sensors["bumper"] = BoolReading(value=readings.bumper)
    return TelemetryFrame(
        t=Timestamp(mono_ns=round(readings.t * 1e9)),
        seq=seq,
        mode=mode,
        state=state,
        cmd=Command(steering_rad=cmd.steering_rad, speed_m_s=cmd.speed_m_s),
        meas=Measured(
            steering_rad=readings.steering_rad,
            speed_m_s=None if readings.speed_m_s is None else readings.speed_m_s.value,
            yaw_rate_rad_s=None
            if readings.yaw_rate_rad_s is None
            else readings.yaw_rate_rad_s.value,
            sensors=sensors,
        ),
        power=PowerStatus(ev3_battery_v=readings.battery_v),
        loop=LoopStats(rate_hz=loop_hz),
        channels=channels or {},
    )


class Recorder:
    """Streams telemetry (and optional ground truth) to an MCAP file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: IO[bytes] = path.open("wb")
        self._writer = Writer(self._file)
        self._writer.start(profile="", library="raceforge")
        schema = json_schemas()["telemetry.v1"]
        sid = self._writer.register_schema(
            "raceforge.TelemetryFrame", "jsonschema", json.dumps(schema).encode()
        )
        self._tel = self._writer.register_channel(TELEMETRY_TOPIC, "json", sid)
        truth_schema = {
            "type": "object",
            "properties": {
                k: {"type": "number"} for k in ("x", "y", "yaw", "speed_m_s", "yaw_rate_rad_s")
            },
        }
        tid = self._writer.register_schema(
            "raceforge.Truth", "jsonschema", json.dumps(truth_schema).encode()
        )
        self._truth = self._writer.register_channel(TRUTH_TOPIC, "json", tid)
        self.frames = 0

    def add(self, frame: TelemetryFrame, truth: Truth | None = None) -> None:
        ns = frame.t.mono_ns
        self._writer.add_message(
            self._tel,
            log_time=ns,
            publish_time=ns,
            data=frame.model_dump_json(by_alias=True).encode(),
            sequence=frame.seq,
        )
        if truth is not None:
            data = {k: (v if math.isfinite(v) else None) for k, v in truth.__dict__.items()}
            self._writer.add_message(
                self._truth,
                log_time=ns,
                publish_time=ns,
                data=json.dumps(data).encode(),
                sequence=frame.seq,
            )
        self.frames += 1

    def close(self) -> BlobRef:
        self._writer.finish()
        self._file.close()
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        return BlobRef(
            sha256=digest, size_bytes=self.path.stat().st_size, media_type="application/x-mcap"
        )


def read_frames(path: Path) -> list[TelemetryFrame]:
    from mcap.reader import make_reader

    out: list[TelemetryFrame] = []
    with path.open("rb") as f:
        for _schema, channel, message in make_reader(f).iter_messages(topics=[TELEMETRY_TOPIC]):
            _ = channel
            out.append(TelemetryFrame.model_validate_json(message.data))
    return out


def make_runlog(
    assembly: VersionRef,
    controller: VersionRef,
    file: BlobRef,
    track: VersionRef | None = None,
    race_setup_id: str | None = None,
    kind: RunKind = RunKind.SIM,
) -> RunLog:
    return RunLog(
        id=new_object_id(),
        kind=kind,
        assembly=assembly,
        track=track,
        race_setup_id=race_setup_id,
        controller=controller,
        started=Timestamp(mono_ns=0),
        file=file,
    )


__all__ = ["Recorder", "frame_from", "make_runlog", "read_frames"]
