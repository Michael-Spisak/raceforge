"""A simulation the UI can watch: scene once, then frames (spec 0008)."""

import math
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from raceforge.api.models import (
    CarScene,
    EgoView,
    EventView,
    FrameMessage,
    ResultMessage,
    SceneMessage,
    SimStart,
    UltrasonicView,
)
from raceforge.api.service import Engine, track_primitives
from raceforge.construct.quickstart import QuickStartParams, generate, vehicle_spec
from raceforge.control.controller import Controller, load_controller
from raceforge.control.params import ControllerParams
from raceforge.control.types import Command, Observation
from raceforge.core.assembly import Assembly
from raceforge.core.io import load_as
from raceforge.sim.engine import Simulation
from raceforge.sim.runner import SIM_SENSORS
from raceforge.sim.simio import RaceSession
from raceforge.sim.world import CarEntry, build_world
from raceforge.track.procedural import generate_corridor


def _r(x: float, nd: int = 4) -> float:
    return round(float(x), nd)


DEADMAN_S = 0.3  # same as the car runtime (spec 0005)


class StandStill(Controller[ControllerParams]):
    """Controller choice "none": the car waits for teleop (spec 0010)."""

    def step(self, obs: Observation) -> Command:
        return Command()


class Teleop:
    """Operator input for the simulated car, with the car runtime's dead-man semantics (spec 0010).

    While engaged, each step uses the last teleop command; if none arrived for ``DEADMAN_S``
    (wall clock), the car stops (``deadman_stop``) until the next message. ``release`` hands back to
    the controller; ``stop`` latches a stop like the operator stop on the real car.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.cmd: Command | None = None
        self.last = 0.0
        self.stopped = False

    @property
    def engaged(self) -> bool:
        return self.cmd is not None

    def drive(self, steer: float, speed: float) -> None:
        if not (math.isfinite(steer) and math.isfinite(speed)):
            steer, speed = 0.0, 0.0
        self.cmd = Command(steering_rad=steer, speed_m_s=speed)
        self.last = self.clock()

    def release(self) -> None:
        self.cmd = None

    def stop(self) -> None:
        self.stopped = True
        self.cmd = None

    def override(self) -> tuple[Command, str] | None:
        if self.stopped:
            return Command(), "stop"
        if self.cmd is None:
            return None
        if self.clock() - self.last > DEADMAN_S:
            return Command(), "deadman_stop"
        return self.cmd, "teleop"


class SimSession:
    def __init__(self, engine: Engine, start: SimStart) -> None:
        self.engine = engine
        self.start = start
        cat = engine.cat
        params = start.quickstart or QuickStartParams(drive_gears="20-28", sensors=SIM_SENSORS)
        car = generate(params, cat)
        spec = vehicle_spec(car, cat)
        if start.quick_track:
            from raceforge.api.tracks import QuickTracks

            cor = QuickTracks().corridor(start.quick_track, laps=start.laps)
        else:
            cor = generate_corridor(start.corridor)
        track = cor.track
        setup = track.race_setups[0]
        if setup.start_line == setup.finish_line and setup.laps != start.laps:
            track = track.model_copy(
                update={"race_setups": [setup.model_copy(update={"laps": start.laps})]}
            )
        ego = car.assembly
        if start.assembly is not None:
            # The edited car; drives and steering motor still come from the quick-start fields.
            ego = load_as(Assembly, start.assembly)
            ego.validate_against_parts(cat.parts_by_hash())
        entries = [CarEntry("ego", ego, spec, 0)]
        entries += [
            CarEntry(f"opp{i + 1}", car.assembly, spec, i + 1, True) for i in range(start.opponents)
        ]
        self.world = build_world(track, entries, cat, seed=start.seed)
        self.sim = Simulation(self.world, seed=start.seed)
        controller: Controller[ControllerParams] = (
            StandStill()
            if start.controller == "none"
            else load_controller(
                Path(start.controller), Path(start.params_path) if start.params_path else None
            )
        )
        record = Path(start.record_path) if start.record_path else None
        if record is not None:
            record.parent.mkdir(parents=True, exist_ok=True)
        length = float(self.sim.length)
        self.race = RaceSession(
            self.sim, controller, max_time_s=start.laps * length / 0.12 + 120, record=record
        )
        self.teleop = Teleop()
        self.race.override = self.teleop.override
        self.assemblies = {e.name: e.assembly for e in entries}
        self._events_sent = 0

    def scene(self) -> SceneMessage:
        cars: list[CarScene] = []
        for name, handle in sorted(self.world.cars.items()):
            frames: dict[str, tuple[np.ndarray, np.ndarray]] = {}
            for body in set(handle.info.part_bodies.values()):
                b = self.sim.data.body(handle.prefix + body)
                frames[body] = (np.array(b.xpos), np.array(b.xmat).reshape(3, 3))
            # Parts are placed in the car (assembly) frame; the chassis body sits 2 mm above it.
            chassis_pos, chassis_rot = frames["chassis"]
            chassis_pos = chassis_pos - chassis_rot @ np.array([0.0, 0.0, 0.002])
            car_frames = {
                k: (chassis_rot.T @ (p - chassis_pos), chassis_rot.T @ r)
                for k, (p, r) in frames.items()
            }
            cars.append(
                self.engine.car_scene(
                    name, self.assemblies[name], handle.info.part_bodies, car_frames
                )
            )
        return SceneMessage(
            primitives=track_primitives(self.sim.model, self.world.surface_of_geom),
            cars=cars,
            centreline=[(_r(x), _r(y)) for x, y in self.world.centreline],
        )

    def advance(self, steps: int) -> None:
        for _ in range(steps):
            if self.race.done:
                break
            self.race.step()

    @property
    def done(self) -> bool:
        return self.race.done

    def frame(self) -> FrameMessage:
        sim = self.sim
        bodies: dict[
            str, dict[str, tuple[tuple[float, float, float], tuple[float, float, float, float]]]
        ] = {}
        for name, handle in self.world.cars.items():
            per: dict[
                str, tuple[tuple[float, float, float], tuple[float, float, float, float]]
            ] = {}
            for body in sorted(set(handle.info.part_bodies.values())):
                b = sim.data.body(handle.prefix + body)
                per[body] = (
                    (_r(b.xpos[0]), _r(b.xpos[1]), _r(b.xpos[2])),
                    (_r(b.xquat[0], 5), _r(b.xquat[1], 5), _r(b.xquat[2], 5), _r(b.xquat[3], 5)),
                )
            bodies[name] = per
        ego = self.world.cars["ego"]
        readings = sim.readings("ego")
        us_views: dict[str, UltrasonicView] = {}
        lidar_points: list[tuple[float, float]] = []
        up = np.array(sim.data.body(ego.prefix + "chassis").xmat).reshape(3, 3)[:, 2]
        for mount in ego.sensors:
            site = sim.data.site(mount.site)
            pos = np.array(site.xpos)
            fwd = np.array(site.xmat).reshape(3, 3)[:, 2]
            if mount.name in readings.ultrasonic_m:
                value = readings.ultrasonic_m[mount.name]
                us_views[mount.name] = UltrasonicView(
                    value=None if value is None or value.value is None else _r(value.value),
                    origin=(_r(pos[0]), _r(pos[1]), _r(pos[2])),
                    direction=(_r(fwd[0]), _r(fwd[1]), _r(fwd[2])),
                )
            elif mount.name == "lidar" and readings.lidar is not None:
                f = fwd - up * float(fwd @ up)
                f /= np.linalg.norm(f)
                left = np.cross(up, f)
                for i, (a, d) in enumerate(
                    zip(readings.lidar.angles_rad, readings.lidar.ranges_m, strict=True)
                ):
                    if d is None or i % 2:
                        continue
                    pt = pos + d * (math.cos(a) * f + math.sin(a) * left)
                    lidar_points.append((_r(pt[0], 3), _r(pt[1], 3)))
        pr = sim.progress("ego")
        cmd = self.race.last_cmd
        channels = {k: v for k, v in self.race.io.channels.items() if k != "state"}
        new_events = sim.events[self._events_sent :]
        self._events_sent = len(sim.events)
        return FrameMessage(
            t=_r(sim.t, 3),
            bodies=bodies,
            ego=EgoView(
                ultrasonic=us_views,
                lidar_points=lidar_points,
                steering_cmd=_r(cmd.steering_rad),
                speed_cmd=_r(cmd.speed_m_s),
                state=self.race.last_state,
                channels=channels,
                distance_m=_r(pr.distance_m, 3),
                laps=pr.laps,
                lap_times_s=[_r(x, 2) for x in pr.lap_times_s],
                finished=pr.finished,
            ),
            events=[
                EventView(t=_r(e.t, 2), car=e.car, kind=e.kind, other=e.other) for e in new_events
            ],
        )

    def result(self) -> ResultMessage:
        r = self.race.finish()
        return ResultMessage(
            finished=r.progress.finished,
            laps=r.progress.laps,
            lap_times_s=[_r(x, 2) for x in r.progress.lap_times_s],
            sim_time_s=_r(r.sim_time_s, 2),
            wall_contacts=r.wall_contacts,
            problems=r.problems[:20],
            record_path=self.start.record_path,
        )
