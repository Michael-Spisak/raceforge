"""SimIO: RobotIO backed by the simulator, plus a simulated race runner (spec 0004)."""

import math
from collections.abc import Callable
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


class RaceSession:
    """One race, advanced step by step (used by ``run_race`` and the UI engine)."""

    def __init__(
        self,
        sim: Simulation,
        controller: Controller[ControllerParams],
        car: str = "ego",
        max_time_s: float = 600.0,
        opponent_speed_m_s: float = 0.25,
        record: Path | None = None,
        deadline_s: float = 0.010,
    ) -> None:
        self.sim, self.controller, self.car = sim, controller, car
        self.max_time_s = max_time_s
        self.opponent_speed_m_s = opponent_speed_m_s
        self.io = SimIO(sim, car)
        self.host = ControllerHost(controller, self.io, deadline_s=deadline_s)
        self.host.start()
        self.recorder = Recorder(record) if record else None
        self.seq = 0
        self.last_cmd = Command()
        self.last_state = "run"
        # Teleop (spec 0010): (command, state) replacing the controller for this step, or None.
        self.override: Callable[[], tuple[Command, str] | None] | None = None
        # Deterministic lateral offsets for opponents (alternating sides by start order).
        others = [c for c in sorted(sim.cars) if c != car]
        self.offsets = {name: (0.25 if i % 2 else -0.25) for i, name in enumerate(others)}
        self.closed = False

    @property
    def done(self) -> bool:
        return self.closed or self.sim.t >= self.max_time_s or self.sim.progress(self.car).finished

    def step(self) -> None:
        sim = self.sim
        manual = self.override() if self.override is not None else None
        if manual is None:
            self.last_cmd = cmd = self.host.step()
            self.last_state = self.controller.state
        else:
            cmd, self.last_state = manual
            info = self.io.info  # operator input gets the car's limits, like the car runtime
            cmd = Command(
                steering_rad=max(-info.max_steer_rad, min(info.max_steer_rad, cmd.steering_rad)),
                speed_m_s=max(-info.max_speed_m_s, min(info.max_speed_m_s, cmd.speed_m_s)),
            )
            self.io.write(cmd)
            self.last_cmd = self.io.last_cmd
        for other, offset in self.offsets.items():
            sim.command(other, centreline_follower(sim, other, self.opponent_speed_m_s, offset))
        sim.step()
        if self.recorder is not None:
            frame = frame_from(
                self.seq,
                sim.readings(self.car),
                CarCommand(self.last_cmd.steering_rad, self.last_cmd.speed_m_s),
                self.last_state,
                1 / sim.control_dt,
                dict(self.io.channels),
            )
            self.recorder.add(frame, sim.truth(self.car))
            self.seq += 1

    def finish(self) -> RaceResult:
        if not self.closed:
            self.host.stop()
            if self.recorder is not None:
                self.recorder.close()
            self.closed = True
        return RaceResult(
            progress=self.sim.progress(self.car),
            events=list(self.sim.events),
            sim_time_s=self.sim.t,
            problems=[f"step {p.step}: {p.kind}: {p.detail}" for p in self.host.problems],
            max_step_ms=self.host.max_step_s * 1000,
        )


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
    session = RaceSession(sim, controller, car, max_time_s, opponent_speed_m_s, record, deadline_s)
    while not session.done:
        session.step()
    return session.finish()
