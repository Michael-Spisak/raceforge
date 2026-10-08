"""Controller host process for the real car (spec 0005, ADR-0014).

The Rust core (``rf-core``) owns timing, safety and the watchdog. It starts this process with
``python -m raceforge.car.host --socket PATH --controller FILE [--params YAML]`` and exchanges
one JSON object per line over the Unix domain socket (shapes: ``car_runtime/rf-proto/src/ipc.rs``):

- core -> host: ``hello`` (RobotInfo), ``obs`` (Observation, every tick), ``shutdown``
- host -> core: ``ready``, ``cmd`` (Command + emitted channels + notes), ``error`` (traceback)

The controller is loaded exactly like ``raceforge sim`` does and talks to :class:`RealIO`, which
implements the ``RobotIO`` protocol of spec 0004, so the same file runs unchanged.
"""

import argparse
import json
import socket
import sys
import traceback
from pathlib import Path
from typing import Any, TextIO

from raceforge.control.controller import ControllerHost, load_controller
from raceforge.control.types import (
    Command,
    LidarScan,
    Mode,
    Observation,
    PoseEstimate,
    RobotInfo,
)

type Json = dict[str, Any]


def robot_info_from_json(d: Json) -> RobotInfo:
    return RobotInfo(
        car_name=str(d["car_name"]),
        sensors=tuple(str(s) for s in d["sensors"]),
        max_steer_rad=float(d["max_steer_rad"]),
        max_speed_m_s=float(d["max_speed_m_s"]),
        wheelbase_m=float(d["wheelbase_m"]),
        track_m=float(d["track_m"]),
        control_rate_hz=float(d["control_rate_hz"]),
    )


def _opt_float(v: Any) -> float | None:
    return None if v is None else float(v)


def observation_from_json(d: Json) -> Observation:
    lidar = None
    if d.get("lidar") is not None:
        ld: Json = d["lidar"]
        lidar = LidarScan(
            angles_rad=tuple(float(a) for a in ld["angles_rad"]),
            ranges_m=tuple(_opt_float(r) for r in ld["ranges_m"]),
            t_s=float(ld["t_s"]),
        )
    pose = None
    if d.get("pose_estimate") is not None:
        p: Json = d["pose_estimate"]
        pose = PoseEstimate(
            float(p["x_m"]), float(p["y_m"]), float(p["heading_rad"]), float(p["confidence"])
        )
    us: Json = d.get("ultrasonic_m") or {}
    bumper: Json = d.get("bumper") or {}
    return Observation(
        t_s=float(d["t_s"]),
        dt_s=float(d["dt_s"]),
        ultrasonic_m={str(k): _opt_float(v) for k, v in us.items()},
        lidar=lidar,
        yaw_rate_rad_s=_opt_float(d.get("yaw_rate_rad_s")),
        heading_rad=_opt_float(d.get("heading_rad")),
        speed_m_s=_opt_float(d.get("speed_m_s")),
        steering_rad=_opt_float(d.get("steering_rad")),
        bumper={str(k): bool(v) for k, v in bumper.items()},
        battery_v=_opt_float(d.get("battery_v")),
        pose_estimate=pose,
        mode=Mode(d.get("mode", "test")),
    )


class RealIO:
    """``RobotIO`` for the real car: observations come from and commands go to the Rust core."""

    def __init__(self, info: RobotInfo) -> None:
        self._info = info
        self.obs: Observation | None = None
        self.cmd = Command()
        self.channels: dict[str, float | int | bool | str] = {}
        self.notes: list[Json] = []

    @property
    def info(self) -> RobotInfo:
        return self._info

    def read(self) -> Observation:
        if self.obs is None:
            raise RuntimeError("no observation received yet")
        return self.obs

    def write(self, cmd: Command) -> None:
        self.cmd = cmd

    def emit(self, channel: str, value: float | int | bool | str) -> None:
        self.channels[channel] = value

    def note(self, text: str, tags: list[str] | None = None) -> None:
        self.notes.append({"text": text, "tags": list(tags or [])})


def _send(out: TextIO, msg: Json) -> None:
    out.write(json.dumps(msg, separators=(",", ":"), allow_nan=False) + "\n")
    out.flush()


def serve(inp: TextIO, out: TextIO, controller_path: Path, params_path: Path | None = None) -> int:
    """Run the host protocol until ``shutdown`` or end of stream. Returns the exit code."""
    controller = load_controller(controller_path, params_path)
    host: ControllerHost | None = None
    io: RealIO | None = None
    for line in inp:
        msg: Json = json.loads(line)
        kind = msg.get("type")
        if kind == "hello":
            io = RealIO(robot_info_from_json(msg["info"]))
            host = ControllerHost(
                controller, io, deadline_s=1.0 / io.info.control_rate_hz, on_exception="raise"
            )
            host.start()
            _send(out, {"type": "ready"})
        elif kind == "obs":
            if host is None or io is None:
                _send(out, {"type": "error", "seq": msg.get("seq"), "detail": "obs before hello"})
                continue
            seq = int(msg["seq"])
            io.obs = observation_from_json(msg["obs"])
            io.channels, io.notes = {}, []
            try:
                host.step()
            except Exception:
                _send(out, {"type": "error", "seq": seq, "detail": traceback.format_exc()})
                continue
            cmd = {"steering_rad": io.cmd.steering_rad, "speed_m_s": io.cmd.speed_m_s}
            try:
                reply = {"type": "cmd", "seq": seq, "cmd": cmd, "channels": io.channels}
                if io.notes:
                    reply["notes"] = io.notes
                _send(out, reply)
            except ValueError:  # NaN/inf in the command or a channel: the core treats it as stop
                _send(out, {"type": "error", "seq": seq, "detail": "non-finite command/channel"})
        elif kind == "shutdown":
            break
    if host is not None:
        host.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m raceforge.car.host")
    ap.add_argument("--socket", required=True, help="Unix socket path of the Rust core")
    ap.add_argument("--controller", required=True, type=Path)
    ap.add_argument("--params", type=Path, default=None)
    args = ap.parse_args(argv)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)  # type: ignore[attr-defined,unused-ignore]
    sock.connect(args.socket)
    with sock.makefile("r", encoding="utf-8") as inp, sock.makefile("w", encoding="utf-8") as out:
        return serve(inp, out, args.controller, args.params)


if __name__ == "__main__":
    sys.exit(main())
