# Spec 0003: Simulation runtime — sensor models, procedural corridors, multi-car worlds, recording

- **Status:** approved (2026-10-07)
- **Owner:** @Michael-Spisak
- **Plan section:** docs/PLAN.md §3 (Simulate), §2e (procedural corridors), §4 (benchmark basis)
- **Related ADRs:** ADR-0003, ADR-0012, ADR-0013 (new: `mcap` for logs)
- **Depends on:** Spec 0001, Spec 0002

## Purpose
Turn the quick-start MJCF into a usable simulator: realistic sensor readings (EV3 ultrasonic, EV3 gyro,
2D LiDAR, wheel encoders, steering angle), generated corridor tracks with walls/doors/objects, several cars
in one world (own car + opponents), and recording of every run as telemetry frames. Spec 0004 builds the
controller SDK on top of this.

## Scope
- In scope: `raceforge.sim.world` (compose track + N cars into one MJCF, name prefixing),
  `raceforge.sim.sensors` (sensor models with noise, latency, rate, dropout), `raceforge.track.procedural`
  (seeded corridor generator → `core.Track`), `raceforge.sim.track_mjcf` (Track → MuJoCo geoms),
  `raceforge.sim.engine` (`Simulation`: step, apply commands, produce readings, progress along centreline,
  collision events), `raceforge.sim.record` (RunLog + MCAP file of `TelemetryFrame`s).
- Out of scope: controllers/SDK (0004), camera simulation (Gaussian splat later; MuJoCo camera only as
  optional debug), scanned-track import (capture specs), training (later spec), browser viewer (frontend spec).

## Sensor models (all parameters from `core.devices`, calibratable later)
| Sensor | Model |
|---|---|
| EV3 ultrasonic | 7 rays in a ±15° cone from the sensor site; reading = min hit distance; range 0.03–2.55 m (beyond → `None`); 1 mm quantisation; Gaussian noise; **dropout** (→ `None`) when the incidence angle to the hit surface > 50° or the surface class is "soft"; **crosstalk** option (random short echo) when ≥ 2 ultrasonic sensors fire in the same 50 ms slot; update rate 20 Hz; latency 30 ms |
| EV3 gyro | yaw rate from MuJoCo gyro + bias random walk + white noise; quantised to 1 °/s; rate 100 Hz; latency 10 ms; integrated angle exposed as EV3 does |
| 2D LiDAR (LD06/LD19/RPLidar-class) | 360° scan, configurable angular resolution (default 0.8° = 450 rays) at 10 Hz via `mj_multiRay`; **motion distortion**: each scan is assembled from slices cast over the rotation period; noise ∝ distance; dropouts on surface classes "glass" and "black" with configurable probability; range 0.02–12 m |
| Wheel encoders | EV3 motor tachos: 360 counts/rev on the motor shaft (× gear ratio), read at 100 Hz |
| Steering angle | steering motor tacho (counts → angle, includes play effects) |
| Bumper / touch | contact sensor from MuJoCo contacts on the touch-sensor geom |
| Battery | voltage from the battery model of spec 0002 derive data (sag under load) — constant in this spec, dynamic later |

Every sensor has: `rate_hz`, `latency_s`, `noise`, `dropout`, deterministic RNG seeded per run.

## Procedural corridors (`raceforge.track.procedural`)
- Seeded (`seed: int`) and fully deterministic; parameters with defaults derived later from scans:
  length 20–120 m, width 1.4–3.0 m (varies by segment), straight/bend/90° corner segments, loop or
  point-to-point, niches (depth 0.2–0.8 m), door openings (open/closed/random), pillars, bins/benches as
  objects, glass segments, floor friction regions.
- Output: `core.Track` with floor polygon, wall polylines (height 2.5 m), objects with classes, surfaces,
  one `RaceSetup` (start line, finish line or lap line, direction, 3 laps, start grid for 6 cars), plus a
  **centreline** polyline (stored in the race setup's checkpoints at 0.5 m spacing) for progress measurement.
- Validation: corridor never narrower than a configurable minimum; no self-intersection; start grid inside.

## Worlds and multi-car
- `World(track, cars=[CarSpec(name, assembly, vehicle_spec, start_slot)])` → one MJCF. Each car's
  names get a prefix (`car0/`, `car1/` …). Cars collide with each other and the walls.
- Opponent cars are older versions of our own car or generic opponents with **randomised size 15–40 cm,
  mass 0.5–2.5 kg, speed** (plan §3); generic opponents are generated quick-start variants scaled in the
  allowed parameter ranges plus a mass override.
- Opponents are driven by a built-in simple controller in this spec (wall-follow at a set speed); real
  controllers come with spec 0004.

## Simulation engine
- `Simulation(world, seed)`: fixed physics step 2 ms; control step configurable (default 20 ms = 50 Hz).
- Per car per control step: apply `Command(steering_rad, speed_m_s | throttle)`; a low-level wheel-speed
  PI loop (emulating the EV3 speed regulation) converts target speed to motor duty.
- Outputs per step: sensor readings (with latency), ground-truth pose, **progress** (arc length along
  centreline, lap count, lap times), collision events (car–wall, car–car, car–object), stuck detection.
- Domain-randomisation hooks: friction, masses, sensor noise/latency scale, object presence — explicit
  parameters with seeds (presets light/medium/strong come with the training spec).

## Recording
- Every run can be recorded: `RunLog` (spec 0001) + an **MCAP** file with one `TelemetryFrame` per control
  step (JSON-encoded per frame, schema name `raceforge.TelemetryFrame`) and a ground-truth channel.
- Replay reader yields frames in order; files open in Foxglove Studio.

## Non-functional targets
- One car, LiDAR + 3 ultrasonic + gyro on a generated 60 m corridor: ≥ 10× real time on a MacBook Air M2.
- Four cars in one world: ≥ 3× real time.
- Generating a corridor: < 0.5 s.

## Acceptance criteria (→ tests)
- [ ] AC1: Same seed → identical track, identical sensor readings and trajectories (bit-for-bit for a 10 s run).
- [ ] AC2: Ultrasonic: wall at known distance straight ahead reads within noise; out of range → `None`;
      grazing angle > 50° → dropout; rate and latency respected (reading timestamps).
- [ ] AC3: LiDAR: in a rectangular box room the scan matches analytic distances (median error < 1 cm without
      noise); glass segments produce dropouts at the configured rate; motion distortion visible when rotating
      in place (first vs last slice offset matches yaw rate × period).
- [ ] AC4: Gyro: stationary → bias drift within configured bounds; turning at known yaw rate → correct reading
      (± quantisation).
- [ ] AC5: Generated corridors are valid (min width, no self-intersection, start grid inside) for 200 seeds.
- [ ] AC6: Progress along the centreline increases monotonically for a car following the centreline;
      lap counting and lap times correct on a loop track.
- [ ] AC7: Collisions car–wall and car–car are reported; stuck detection fires when blocked for > 2 s.
- [ ] AC8: A 30 s run records an MCAP with one valid `TelemetryFrame` per control step; replay round-trips.
- [ ] AC9: Performance targets met (skipped on CI).

## Open questions
- None blocking. Sensor default parameters will be calibrated with real logs from the car (from November).
