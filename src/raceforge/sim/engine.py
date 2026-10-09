"""Simulation engine: commands, low-level control, sensors, progress, collisions (spec 0003)."""

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from raceforge.core.devices import BatteryParams, GyroParams, Lidar2DParams, RangeSensorParams
from raceforge.sim.battery import LOW_SOC, BatteryModel, BatteryState
from raceforge.sim.mj import mujoco
from raceforge.sim.mjcf import TIMESTEP, RateLimiter
from raceforge.sim.sensors import Encoders, Gyro, Lidar, LidarScan, Stamped, Ultrasonic
from raceforge.sim.world import CAR_SURFACE, CarHandle, World

CHECKPOINT_M = 0.5
STUCK_AFTER_S = 2.0


@dataclass(frozen=True)
class CarCommand:
    steering_rad: float = 0.0
    speed_m_s: float = 0.0


@dataclass(frozen=True)
class Truth:
    x: float
    y: float
    yaw: float
    speed_m_s: float
    yaw_rate_rad_s: float


@dataclass(frozen=True)
class Readings:
    t: float
    ultrasonic_m: dict[str, Stamped[float | None] | None]
    lidar: LidarScan | None
    yaw_rate_rad_s: Stamped[float] | None
    heading_rad: Stamped[float] | None
    speed_m_s: Stamped[float] | None
    steering_rad: float
    bumper: bool
    battery_v: float


@dataclass
class Progress:
    distance_m: float = 0.0  # unwrapped progress along the centreline since the start line
    laps: int = 0
    lap_times_s: list[float] = field(default_factory=list[float])
    finished: bool = False
    index: int = 0


@dataclass(frozen=True)
class Event:
    t: float
    car: str
    kind: str  # "wall" | "object" | "car" | "stuck"
    other: str = ""


@dataclass
class _CarState:
    handle: CarHandle
    steer: RateLimiter
    cmd: CarCommand = CarCommand()
    integral: float = 0.0
    ultrasonics: dict[str, Ultrasonic] = field(default_factory=dict[str, Ultrasonic])
    gyro: Gyro | None = None
    lidar: Lidar | None = None
    encoders: Encoders | None = None
    progress: Progress = field(default_factory=Progress)
    lap_start_t: float = 0.0
    slow_since: float | None = None
    stuck: bool = False
    contacts: set[str] = field(default_factory=set[str])
    bumper: bool = False
    driven_joints: list[str] = field(default_factory=list[str])
    top_speed: float = 1.0
    last_arc: float | None = None
    anchor: tuple[float, float] | None = None
    battery: BatteryModel | None = None


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


class Simulation:
    """Steps a :class:`World` with a fixed control period; deterministic for a given seed."""

    def __init__(
        self,
        world: World,
        seed: int = 0,
        control_dt: float = 0.02,
        crosstalk_prob: float = 0.02,
        battery: BatteryParams | None = None,
        battery_soc: float = 1.0,
    ) -> None:
        self.world = world
        self._battery = (battery, battery_soc)
        self.model: Any = world.model
        self.data: Any = mujoco.MjData(self.model)
        self.surface_of_geom = world.surface_of_geom
        self.reflectivity_of_geom = world.reflectivity_of_geom
        self.control_dt = control_dt
        self.substeps = max(1, round(control_dt / TIMESTEP))
        self.t = 0.0
        self.events: list[Event] = []
        mujoco.mj_forward(self.model, self.data)
        setup = next(r for r in world.track.race_setups if r.id == world.race_setup_id)
        self.loop = setup.start_line == setup.finish_line
        self.laps_target = setup.laps
        self.centre = world.centreline
        seg = np.linalg.norm(np.diff(self.centre, axis=0), axis=1)
        self.s = np.concatenate([[0.0], np.cumsum(seg)])
        start_mid = np.array(
            [
                (setup.start_line.a.x + setup.start_line.b.x) / 2,
                (setup.start_line.a.y + setup.start_line.b.y) / 2,
            ]
        )
        finish_mid = np.array(
            [
                (setup.finish_line.a.x + setup.finish_line.b.x) / 2,
                (setup.finish_line.a.y + setup.finish_line.b.y) / 2,
            ]
        )
        self.start_idx = int(np.argmin(np.linalg.norm(self.centre - start_mid, axis=1)))
        self.finish_s = float(
            self.s[int(np.argmin(np.linalg.norm(self.centre - finish_mid, axis=1)))]
            - self.s[self.start_idx]
        )
        self.length = float(self.s[-1])
        self.cars: dict[str, _CarState] = {}
        ss = np.random.SeedSequence(seed)
        for n, (name, h) in enumerate(sorted(world.cars.items())):
            self.cars[name] = self._make_state(h, ss.spawn(1)[0], crosstalk_prob)
            st = self.cars[name]
            tr = self.truth(name)
            st.progress.index = self._nearest(tr, None)
            st.last_arc = self._arc(tr, st.progress.index)
            offset = st.last_arc - float(self.s[self.start_idx])
            if self.loop and offset > self.length / 2:
                offset -= self.length
            st.progress.distance_m = offset
            _ = n

    # ---- setup
    def _make_state(self, h: CarHandle, seq: np.random.SeedSequence, crosstalk: float) -> _CarState:
        st = _CarState(handle=h, steer=RateLimiter(h.info.steer_rate_rad_s))
        rngs = [np.random.default_rng(s) for s in seq.spawn(len(h.sensors) + 1)]
        for k, mount in enumerate(h.sensors):
            dev, rng = mount.device, rngs[k]
            if dev.type in ("ev3_ultrasonic", "tof") and isinstance(dev.params, RangeSensorParams):
                st.ultrasonics[mount.name] = Ultrasonic(
                    mount.site, dev.params, rng, h.chassis_body, crosstalk_prob=crosstalk
                )
            elif dev.type == "ev3_gyro" and isinstance(dev.params, GyroParams):
                st.gyro = Gyro(dev.params, rng, h.prefix + "imu_gyro")
            elif dev.type == "lidar_2d" and isinstance(dev.params, Lidar2DParams):
                st.lidar = Lidar(mount.site, dev.params, rng, h.chassis_body)
        spec = h.entry.spec
        if spec.drives:
            d = spec.drives[0]
            st.top_speed = d.motor.no_load_speed_rad_s / d.gear_ratio * h.derived.wheel_radius_m
            st.driven_joints = self._driven_joints(h)
            st.encoders = Encoders(
                [h.prefix + j for j in st.driven_joints], d.gear_ratio, h.derived.wheel_radius_m
            )
            pack, soc = self._battery
            if pack is not None:
                st.battery = BatteryModel(pack, d.motor, motors=len(spec.drives), soc=soc)
        return st

    def _driven_joints(self, h: CarHandle) -> list[str]:
        """Wheel joints on the first drive axle (closest wheels to the axle x in the car frame)."""
        chassis = self.data.body(h.prefix + "chassis")
        rot = np.array(chassis.xmat).reshape(3, 3)
        axle_x = h.entry.spec.drives[0].axle_x_m
        out: list[str] = []
        for wheel, joint in h.info.wheel_joints.items():
            local = rot.T @ (
                np.array(self.data.body(h.prefix + wheel).xpos) - np.array(chassis.xpos)
            )
            if abs(local[0] - axle_x) < 0.01:
                out.append(joint)
        return sorted(out)

    # ---- public API
    def command(self, car: str, cmd: CarCommand) -> None:
        self.cars[car].cmd = cmd

    def truth(self, car: str) -> Truth:
        h = self.cars[car].handle if car in self.cars else self.world.cars[car]
        body = self.data.body(h.prefix + "chassis")
        rot = np.array(body.xmat).reshape(3, 3)
        vel = np.zeros(6)
        mujoco.mj_objectVelocity(self.model, self.data, mujoco.mjtObj.mjOBJ_BODY, body.id, vel, 0)
        return Truth(
            x=float(body.xpos[0]),
            y=float(body.xpos[1]),
            yaw=math.atan2(rot[1, 0], rot[0, 0]),
            speed_m_s=float(vel[3] * rot[0, 0] + vel[4] * rot[1, 0]),
            yaw_rate_rad_s=float(vel[2]),
        )

    def readings(self, car: str) -> Readings:
        st = self.cars[car]
        t = self.t
        gyro = st.gyro.read(t) if st.gyro else None
        return Readings(
            t=t,
            ultrasonic_m={name: us.read(t) for name, us in st.ultrasonics.items()},
            lidar=st.lidar.read(t) if st.lidar else None,
            yaw_rate_rad_s=None if gyro is None else Stamped(gyro.value[0], gyro.t_sample),
            heading_rad=None if gyro is None else Stamped(gyro.value[1], gyro.t_sample),
            speed_m_s=st.encoders.read(t) if st.encoders else None,
            steering_rad=math.radians(
                round(
                    math.degrees(
                        float(
                            self.data.joint(
                                st.handle.prefix + st.handle.info.steer_joint_left
                            ).qpos[0]
                        )
                    )
                )
            ),
            bumper=st.bumper,
            battery_v=7.4 if st.battery is None else st.battery.volts,
        )

    def battery_state(self, car: str) -> BatteryState | None:
        b = self.cars[car].battery
        return None if b is None else b.state()

    def progress(self, car: str) -> Progress:
        return self.cars[car].progress

    def step(self) -> None:
        """Advance one control period."""
        for _ in range(self.substeps):
            for st in self.cars.values():
                self._low_level(st)
            mujoco.mj_step(self.model, self.data)
            self.t += TIMESTEP
        self._contacts()
        for name, st in self.cars.items():
            self._sense(st)
            self._update_progress(name, st)
            self._stuck(name, st)

    # ---- internals
    def _low_level(self, st: _CarState) -> None:
        h = st.handle
        lim = h.info.max_steer_rad
        target = max(-lim, min(lim, st.cmd.steering_rad))
        self.data.actuator(h.prefix + h.info.steer_actuator).ctrl = st.steer.step(target, TIMESTEP)
        if not st.driven_joints:
            return
        speed = (
            float(np.mean([self.data.joint(h.prefix + j).qvel[0] for j in st.driven_joints]))
            * h.derived.wheel_radius_m
        )
        err = st.cmd.speed_m_s - speed
        st.integral = max(-0.5, min(0.5, st.integral + err * TIMESTEP))
        duty = (
            st.cmd.speed_m_s / st.top_speed
            + 1.5 * err / st.top_speed
            + 4.0 * st.integral / st.top_speed
        )
        duty = max(-1.0, min(1.0, duty))
        if st.battery is not None:
            self._power(st, duty)
            duty *= st.battery.drive_scale()
        for a in h.info.drive_actuators:
            self.data.actuator(h.prefix + a).ctrl = duty

    def _power(self, st: _CarState, duty: float) -> None:
        """Battery step for one physics step; events for low charge and brownout (spec 0024)."""
        assert st.battery is not None
        h = st.handle
        wheel = float(np.mean([self.data.joint(h.prefix + j).qvel[0] for j in st.driven_joints]))
        motor_speed = wheel * h.entry.spec.drives[0].gear_ratio
        name = next(n for n, c in self.cars.items() if c is st)
        if st.battery.step(duty, motor_speed, TIMESTEP):
            self.events.append(Event(self.t, name, "brownout"))
        if st.battery.soc < LOW_SOC and not st.battery.low_reported:
            st.battery.low_reported = True
            self.events.append(Event(self.t, name, "low_battery"))

    def _sense(self, st: _CarState) -> None:
        firing = len(st.ultrasonics)
        for us in st.ultrasonics.values():
            us.sample(self, self.t, others_firing=max(0, firing - 1))
        if st.gyro:
            st.gyro.sample(self, self.t)
        if st.lidar:
            st.lidar.sample(self, self.t)
        if st.encoders:
            st.encoders.sample(self, self.t)

    def _car_of_body(self, body: int) -> str | None:
        root = int(self.model.body_rootid[body])
        for name, st in self.cars.items():
            if st.handle.chassis_body == root:
                return name
        return None

    def _contacts(self) -> None:
        current: dict[str, set[str]] = {n: set() for n in self.cars}
        for k in range(self.data.ncon):
            c = self.data.contact[k]
            g1, g2 = int(c.geom1), int(c.geom2)
            s1, s2 = self.surface_of_geom[g1], self.surface_of_geom[g2]
            for ga, gb, sa, sb in ((g1, g2, s1, s2), (g2, g1, s2, s1)):
                if sa != CAR_SURFACE or sb == "floor":
                    continue
                car = self._car_of_body(int(self.model.geom_bodyid[ga]))
                if car is None:
                    continue
                if sb == CAR_SURFACE:
                    other = self._car_of_body(int(self.model.geom_bodyid[gb]))
                    if other and other != car:
                        current[car].add(f"car:{other}")
                else:
                    current[car].add(("wall:" if sb in ("wall", "glass") else "object:") + sb)
        for name, st in self.cars.items():
            for key in sorted(current[name] - st.contacts):
                kind, other = key.split(":", 1)
                self.events.append(Event(self.t, name, kind, other))
            st.contacts = current[name]
            st.bumper = bool(current[name])

    def _nearest(self, tr: Truth, around: int | None) -> int:
        p = np.array([tr.x, tr.y])
        if around is None:
            return int(np.argmin(np.linalg.norm(self.centre - p, axis=1)))
        n = len(self.centre)
        window = np.arange(around - 3, around + 4)  # a car moves < 0.1 m per control step
        window = window % n if self.loop else np.clip(window, 0, n - 1)
        return int(window[np.argmin(np.linalg.norm(self.centre[window] - p, axis=1))])

    def _arc(self, tr: Truth, idx: int) -> float:
        """Continuous arc length of the car's projection onto the centreline near ``idx``."""
        n = len(self.centre)
        best_s, best_d = float(self.s[idx]), math.inf
        p = np.array([tr.x, tr.y])
        for i in (idx - 1, idx):
            if not self.loop and (i < 0 or i >= n - 1):
                continue
            i %= n - 1 if self.loop else n
            a, b = self.centre[i], self.centre[i + 1]
            ab = b - a
            length = float(np.linalg.norm(ab))
            if length == 0:
                continue
            u = max(0.0, min(1.0, float((p - a) @ ab) / length**2))
            d = float(np.linalg.norm(a + u * ab - p))
            if d < best_d:
                best_d, best_s = d, float(self.s[i]) + u * length
        return best_s

    def _update_progress(self, name: str, st: _CarState) -> None:
        pr = st.progress
        if pr.finished:
            return
        tr = self.truth(name)
        prev_arc = self._arc(tr, pr.index) if pr.index == pr.index else 0.0
        prev_arc = st.last_arc if st.last_arc is not None else prev_arc
        pr.index = self._nearest(tr, pr.index)
        arc = self._arc(tr, pr.index)
        ds = arc - prev_arc
        if self.loop and ds < -self.length / 2:
            ds += self.length
        elif self.loop and ds > self.length / 2:
            ds -= self.length
        pr.distance_m += ds
        st.last_arc = arc
        if self.loop:
            laps = math.floor(pr.distance_m / self.length) if pr.distance_m > 0 else 0
            while pr.laps < laps:
                pr.laps += 1
                pr.lap_times_s.append(self.t - st.lap_start_t)
                st.lap_start_t = self.t
            pr.finished = pr.laps >= self.laps_target
        elif pr.distance_m >= self.finish_s:
            pr.laps, pr.finished = 1, True
            pr.lap_times_s.append(self.t - st.lap_start_t)

    def _stuck(self, name: str, st: _CarState) -> None:
        """Stuck = commanded to move but displaced < 5 cm within STUCK_AFTER_S."""
        tr = self.truth(name)
        here = (tr.x, tr.y)
        if abs(st.cmd.speed_m_s) <= 0.05:
            st.slow_since, st.anchor, st.stuck = None, None, False
            return
        if st.anchor is None or math.dist(st.anchor, here) > 0.05:
            st.anchor, st.slow_since, st.stuck = here, self.t, False
            return
        assert st.slow_since is not None
        if not st.stuck and self.t - st.slow_since > STUCK_AFTER_S:
            st.stuck = True
            self.events.append(Event(self.t, name, "stuck"))


def centreline_follower(
    sim: Simulation,
    car: str,
    speed_m_s: float,
    lateral_offset_m: float = 0.0,
    lookahead_m: float = 0.6,
) -> CarCommand:
    """Built-in opponent driver: pure pursuit on the (offset) centreline using ground truth."""
    st = sim.cars[car]
    tr = sim.truth(car)
    n = len(sim.centre)
    steps = max(1, round(lookahead_m / CHECKPOINT_M))
    i = st.progress.index
    j = (i + steps) % n if sim.loop else min(n - 1, i + steps)
    a, b = sim.centre[j], sim.centre[(j + 1) % n if sim.loop else min(n - 1, j + 1)]
    tangent = b - a
    norm = np.linalg.norm(tangent)
    if norm > 0:
        tangent /= norm
    target = a + np.array([-tangent[1], tangent[0]]) * lateral_offset_m
    alpha = _wrap(math.atan2(target[1] - tr.y, target[0] - tr.x) - tr.yaw)
    wheelbase = st.handle.derived.wheelbase_m
    steer = math.atan2(2 * wheelbase * math.sin(alpha), lookahead_m)
    return CarCommand(steering_rad=steer, speed_m_s=speed_m_s)
