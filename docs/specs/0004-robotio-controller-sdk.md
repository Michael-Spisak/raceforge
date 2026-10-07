# Spec 0004: RobotIO + Python controller SDK (for Java/C#/JS developers)

- **Status:** approved (2026-10-07)
- **Owner:** @Michael-Spisak
- **Plan section:** docs/PLAN.md "Development approach §7" (SDK), §5a (controller code), §5b (driving stack)
- **Related ADRs:** ADR-0002, ADR-0007
- **Depends on:** Spec 0001, Spec 0003 (SimIO); Spec 0005 implements RealIO on the car

## Purpose
Give the car team one small, strongly typed Python API to write controllers that run **unchanged** in the
simulator and on the real car. The team knows Java/C#/JS; the SDK must feel familiar (classes, interfaces,
enums, explicit types) and be hard to misuse.

## Scope
- In scope: `raceforge.control` — the `RobotIO` protocol (contract), `Observation`/`Command` types (contract),
  `Controller` base class, `StateMachine`, `PID`, filters, geometry helpers, `ControllerParams` (tunable
  parameters with ranges for the tuner), `SimIO` (adapter to spec 0003), a runner with fixed-rate loop and
  deadline measurement, hot-reload, CLI `raceforge sim`, three templates, docs.
- Out of scope: `RealIO` and the Rust car runtime (spec 0005), localisation/racing line (later spec; the SDK
  exposes `pose_estimate` as optional), RL policy runner (training spec).

## Contracts (human-owned; changes need spec approval)
```text
Observation (frozen dataclass, SI units):
  t_s: float                         # monotonic time of this control step
  dt_s: float                        # time since previous step
  ultrasonic_m: dict[str, float | None]   # by sensor name ("front", "left", "right"); None = no echo
  lidar: LidarScan | None            # angles_rad, ranges_m (None = dropout), timestamp
  yaw_rate_rad_s: float | None       # gyro
  heading_rad: float | None          # integrated gyro heading (EV3 style)
  speed_m_s: float | None            # from wheel encoders
  steering_rad: float | None         # measured steering angle
  bumper: dict[str, bool]
  battery_v: float | None
  pose_estimate: PoseEstimate | None # later (localisation spec); None in this spec
  mode: Mode                          # test | race | sim | hil
Command (frozen dataclass):
  steering_rad: float                # + = left; clamped to the car's limit by the runtime
  speed_m_s: float                   # target speed; runtime regulates wheel speed
RobotIO (Protocol):
  info -> RobotInfo                  # car name, sensors present, limits (max steer, max speed), rate
  read() -> Observation
  write(cmd: Command) -> None
  emit(channel: str, value: float | int | bool | str) -> None   # free telemetry channels (spec 0001)
  note(text: str, tags: list[str] = []) -> None
```

## SDK
- `class Controller` (abstract): `params: ControllerParams` (optional), `setup(info)`, `step(obs) -> Command`,
  `teardown()`. Called at a fixed rate (default 50 Hz) by the runner. Exceptions or a step longer than the
  deadline are reported (sim: test failure/warning; car: watchdog → stop, spec 0005).
- `ControllerParams`: pydantic model; fields declared with `Tunable(default, min, max, step?)` so the tuner
  (training spec) can optimise them; loaded from YAML next to the controller.
- `StateMachine[S: Enum]`: declarative states, `on_enter/on_exit`, guarded transitions, current state emitted
  as telemetry `state` automatically (feeds the live state-diagram later).
- `PID` (anti-windup, output limits, derivative on measurement), `Ema`, `Median`, `RateLimiter`.
- Helpers: `lidar.sector_min(scan, from_deg, to_deg)`, `wall_angle(scan, side)`, `centering_error(left, right)`,
  `clamp`, `deg/rad` conversions.
- `SimIO`: wraps `Simulation` (spec 0003) for one car; opponents use built-in controllers.
- Runner: `run(controller, io, duration_s | until_laps)` with fixed-rate scheduling, deadline stats, telemetry
  (`TelemetryFrame` per step incl. emitted channels), notes.
- CLI: `raceforge sim --controller my_ctrl.py [--params p.yaml] [--track seed:42 | track.json] [--laps 3]
  [--opponents 3] [--record out/] [--watch]` — headless by default, prints lap times/collisions; `--watch`
  hot-reloads the controller file on save and restarts the run.
- Templates (in `controllers/templates/`): `wall_follow.py`, `centering.py`, `state_machine.py`
  (straight / curve / obstacle / stuck-recovery), each with a params YAML and a unit test.

## Docs
- `docs/user/controller-guide.de.md` and `.en.md`: "Python für Java/C#-Entwickler" cheat sheet
  (types, dataclasses vs records, Protocol vs interface, Enum, f-strings, list/dict, None vs null, pytest),
  how to write/run/test a controller, and the AGENTS.md rules for Copilot/Claude when writing controllers.

## Non-functional targets
- SDK overhead per step (excluding controller logic) < 0.2 ms in sim.
- A new teammate gets from installed tool to a running template controller in < 15 min (plan §9d).

## Acceptance criteria (→ tests)
- [ ] AC1: `Observation`, `Command`, `RobotIO` are fully typed; pyright strict passes on `raceforge.control`;
      import-linter: `control` imports neither `sim` (except the `SimIO` adapter module living in `sim`) nor GUI/backend.
- [ ] AC2: The three templates complete 3 laps on 20 generated corridors (seeds fixed) without wall contact
      for `centering.py` and `state_machine.py` (≥ 90 % for `wall_follow.py`).
- [ ] AC3: A controller that raises or exceeds the deadline is reported with the step index; the run continues
      (sim) according to policy.
- [ ] AC4: `ControllerParams` loads from YAML, rejects unknown keys and out-of-range values, and exposes the
      tunable ranges.
- [ ] AC5: `StateMachine` transitions, guards and enter/exit hooks; state appears in telemetry.
- [ ] AC6: PID/filters unit-tested against reference values.
- [ ] AC7: `raceforge sim` CLI prints lap times; `--record` writes a RunLog + MCAP; `--watch` reloads on change.
- [ ] AC8: The German and English guides exist and their code snippets are executed as doctests.

## Open questions
- None blocking. RealIO details (serial protocol to EV3, timing) are specified in 0005.
