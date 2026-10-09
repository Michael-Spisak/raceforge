# Spec 0029: Localisation v1 — map, particle filter, racing line, `localised` controller

- **Status:** approved (owner decisions 2026-10-09, Daniel Hodeib: map + particle filter, racing line, new
  controller template; map and line travel inside the controller's params YAML)
- **Owner:** Daniel Hodeib
- **Plan section:** docs/PLAN.md §5b (driving stack: localisation, planning, control, fallback), critical path
- **Depends on:** Spec 0004 (controller SDK), Spec 0014/0025 (quick tracks + track editor), Spec 0027 (dashboard)
- **Open (teacher):** map-based localisation must be allowed by the race rules (PLAN "Still open"); the
  reactive fallback keeps the car driving on live sensor data either way.

## Purpose
The classic controllers react to what the LiDAR sees right now; they cannot plan a fast line or know where
on the track they are. v1 gives the car a map of the track, lets it find itself on that map with a particle
filter, and drives a precomputed racing line with a speed profile — falling back to reactive driving whenever
it is not sure where it is.

## Scope
- In scope:
  1. **Map** (`raceforge.track.localisation`): occupancy grid of a track (walls + static objects) at 5 cm and its
     distance field (distance to the nearest wall, capped at 1 m).
  2. **Racing line** (`raceforge.track.racing_line`): minimum-curvature-style line inside the corridor with a
     safety margin (iterative smoothing of the centreline, clamped to the corridor), plus a speed profile from a
     lateral-acceleration limit and forward/backward acceleration passes. Loops and point-to-point tracks.
  3. **Embedding**: `raceforge track localise TRACK [--controller localised] [--out FILE]` and
     `POST /api/v1/tracks/quick/{name}/localisation` write a params YAML for the `localised` template with
     `map_b64` (zlib + base64 of the uint8 distance field in cm), map geometry, `line` (x, y, v) and the start
     pose; the deploy bundle format stays unchanged. Tracks tab: button "Export for the car (localisation)".
  4. **Particle filter** (`raceforge.control.localisation`, numpy only — runs on the car): motion model from
     measured speed + gyro yaw rate with noise; LiDAR likelihood-field sensor model on ≈ 30 subsampled beams;
     low-variance resampling when the effective sample size drops; confidence from the mean beam likelihood;
     re-initialisation: around the start pose at start, globally (random particles over free cells) after
     being picked up or when the confidence stays low.
  5. **Template `controllers/templates/localised.py`**: layers as in PLAN §5b — safety (slow down/avoid when
     something is close in front), localisation, pure pursuit on the racing line with the speed profile,
     reactive fallback (centering on the LiDAR) while the confidence is below the threshold, stuck recovery.
     Publishes `pose.x`, `pose.y`, `pose.yaw`, `pose.conf` and `loc.mode` channels; the Live dashboard draws
     the pose from these channels when the frame has no `pose_est`.
- Out of scope (later specs): local re-planning around opponents, Stanley controller, LiDAR mounting offset
  from the assembly, maps from scans (needs scan merge), car-driven SLAM, MCP tools, sim kidnapping tests.

## Interfaces
- Params of `localised` (ControllerParams): `map_b64: str`, `map_origin: (x, y)`, `map_resolution_m`,
  `map_width`, `map_height`, `line: list[(x, y, v)]`, `loop: bool`, `start: (x, y, yaw)`, plus Tunables:
  `particles` (100–1000, default 300), `beams` (10–60, 30), `confidence_drive` (0.3–0.9, 0.55),
  `lookahead_m` (0.2–1.5, 0.6), `speed_scale` (0.2–1.5, 0.8), `fallback_speed_m_s`, `obstacle_front_m`.
- No change to core schemas, the telemetry frame, RobotIO or the bundle format.

## Non-functional targets
- One filter update (300 particles × 30 beams) < 5 ms on a Raspberry Pi 5 (numpy, vectorised).
- Params YAML of a 30 × 30 m track < 200 kB.

## Acceptance criteria (→ tests, critical paths only)
- [ ] AC1: Map: cells on a wall have distance 0; a cell 0.3 m from a wall has ≈ 0.3 m; encode/decode round-trip.
- [ ] AC2: Racing line stays ≥ margin inside the corridor everywhere; its total curvature is lower than the
  centreline's; speed profile respects the lateral-acceleration limit and is lower in curves.
- [ ] AC3: Particle filter on a synthetic map with ray-cast scans: starting near the true pose it tracks a
  driven path with error < 10 cm / 5°; after a kidnap the global re-initialisation recovers within 50 updates.
- [ ] AC4: `localised` with an embedded quick track finishes a lap in the simulator.
- [ ] AC5 (manual, owner): on the real car the dashboard shows the pose on the track map; covering the LiDAR
  switches to the fallback (`loc.mode` = reactive) and back.
