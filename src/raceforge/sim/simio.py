"""SimIO: RobotIO backed by the simulator, plus a simulated race runner (spec 0004)."""

import math
from dataclasses import dataclass, field
from pathlib import Path

from raceforge.control.controller import Controller, ControllerHost
from raceforge.control.params import ControllerParams
from raceforge.control.types import Command, LidarScan, Mode, Observation, RobotInfo
from raceforge.sim.engine import CarCommand, Event, Progress, Simulation, centreline_follower
from raceforge.sim.record import Recorder, frame_from


class SimIO:
    """RobotIO for one car in a :class:`Simulation`."""

    def __init__(self, sim: Simulation, car: str) -> None:
        self.sim, self.car = sim, car
        st = sim.cars[car]
        h = st.handle
        names = tuple(s.name for s in h.sensors)
        self._info = RobotInfo(
            car_name=car,
            sensors=names,
            max_steer_rad=h.info.max_steer_rad,
            max_speed_m_s=st.top_speed,
            wheelbase_m=h.derived.wheelbase_m,
            track_m=h.derived.track_m,
            control_rate_hz=1.0 / sim.control_dt,
        )
        self.channels: dict[str, float | int | bool | str] = {}
        self.notes: list[tuple[float, str, list[str]]] = []
        self.last_cmd = Command()
        self._last_t = sim.t

    @property
    def info(self) -> RobotInfo:
        return self._info

    def read(self) -> Observation:
        r = self.sim.readings(self.car)
        dt = r.t - self._last_t if r.t > self._last_t else self.sim.control_dt
        self._last_t = r.t
        lidar = None
        if r.lidar is not None:
            lidar = LidarScan(
                tuple(float(a) for a in r.lidar.angles_rad), tuple(r.lidar.ranges_m), r.lidar.t_end
            )
        self.channels = {}
        return Observation(
            t_s=r.t,
            dt_s=dt,
            ultrasonic_m={
                k: (v.value if v is not None else None) for k, v in r.ultrasonic_m.items()
            },
            lidar=lidar,
            yaw_rate_rad_s=None if r.yaw_rate_rad_s is None else r.yaw_rate_rad_s.value,
            heading_rad=None if r.heading_rad is None else r.heading_rad.value,
            speed_m_s=None if r.speed_m_s is None else r.speed_m_s.value,
            steering_rad=r.steering_rad,
            bumper={"any": r.bumper},
            battery_v=r.battery_v,
            mode=Mode.SIM,
        )

    def write(self, cmd: Command) -> None:
        if not (math.isfinite(cmd.steering_rad) and math.isfinite(cmd.speed_m_s)):
            cmd = Command()
        self.last_cmd = cmd
        self.sim.command(self.car, CarCommand(cmd.steering_rad, cmd.speed_m_s))

    def emit(self, channel: str, value: float | int | bool | str) -> None:
        self.channels[channel] = value

    def note(self, text: str, tags: list[str] | None = None) -> None:
        self.notes.append((self.sim.t, text, list(tags or [])))


@dataclass
class RaceResult:
    progress: Progress
    events: list[Event]
    sim_time_s: float
    problems: list[str] = field(default_factory=list[str])
    max_step_ms: float = 0.0

    @property
    def wall_contacts(self) -> int:
        return sum(1 for e in self.events if e.car == "ego" and e.kind == "wall")


def run_race(
    sim: Simulation,
    controller: Controller[ControllerParams],
    car: str = "ego",
    max_time_s: float = 600.0,
    opponent_speed_m_s: float = 0.25,
    record: Path | None = None,
    deadline_s: float = 0.010,
) -> RaceResult:
    """Drive ``car`` with ``controller`` until finished or ``max_time_s``.

    Other cars use the built-in centreline driver.
    """
    io = SimIO(sim, car)
    host = ControllerHost(controller, io, deadline_s=deadline_s)
    host.start()
    recorder = Recorder(record) if record else None
    seq = 0
    while sim.t < max_time_s and not sim.progress(car).finished:
        cmd = host.step()
        for other in sim.cars:
            if other != car:
                offset = 0.25 if hash(other) % 2 else -0.25
                sim.command(other, centreline_follower(sim, other, opponent_speed_m_s, offset))
        sim.step()
        if recorder is not None:
            frame = frame_from(
                seq,
                sim.readings(car),
                CarCommand(cmd.steering_rad, cmd.speed_m_s),
                controller.state,
                1 / sim.control_dt,
                dict(io.channels),
            )
            recorder.add(frame, sim.truth(car))
            seq += 1
    host.stop()
    if recorder is not None:
        recorder.close()
    return RaceResult(
        progress=sim.progress(car),
        events=list(sim.events),
        sim_time_s=sim.t,
        problems=[f"step {p.step}: {p.kind}: {p.detail}" for p in host.problems],
        max_step_ms=host.max_step_s * 1000,
    )
