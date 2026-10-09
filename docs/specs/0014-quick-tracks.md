# Spec: Quick tracks — draw a 2D corridor and race on it

- **Status:** approved (owner request 2026-10-09: "continue working off the list of features")
- **Owner:** Michael Spisak
- **Plan section:** docs/PLAN.md milestone weeks 4–5 ("2D-drawn quick tracks"), §2 (Track)
- **Depends on:** Spec 0001 (core `Track`), Spec 0003 (procedural corridors, sim)

## Purpose
Before scans are processed into tracks, the team needs the real school corridor in the simulator. A quick
track is drawn as a centreline polyline with one width; the engine builds walls, checkpoints, start grid and
race setup with the same rules as the procedural corridors, so every sim/benchmark feature works on it.

## Scope
- In scope: `raceforge.track.quick` (`QuickTrack` → `Corridor`), local storage in the engine data folder,
  2D preview, a Tracks tab (draw, drag, delete, obstacles: bin/pillar/bench, loop/laps/width/corner radius),
  `SimStart.quick_track` to race on a saved quick track.
- Out of scope: per-segment widths, doors/niches, team sync of quick tracks (workspace object later),
  track editor on scans (weeks 8–9), using quick tracks in the Train tab (later).

## Interfaces (additive)
- `QuickTrack {name, points: [(x, y)] (m, driving order), width_m 0.6–4, loop, laps, corner_radius_m?,
  obstacles: [{kind: bin|pillar|bench, x, y, yaw_deg}], wall_height_m, friction}`; neighbouring points ≥ 0.3 m.
- `POST /api/v1/tracks/quick/preview` → `QuickTrackPreview {ok, error?, length_m, centreline, walls, start_line,
  direction, objects}` (never 4xx for an undrivable drawing: `ok: false` + reason).
- `GET /api/v1/tracks/quick` → `[QuickTrackInfo]`; `GET|PUT|DELETE /api/v1/tracks/quick/{name}`.
- `SimStart.quick_track: str | null` (name) replaces the procedural corridor.
- `raceforge.track.procedural.assemble_corridor(...)`: the shared wall/checkpoint/start-grid builder
  (the procedural generator's output is unchanged; verified against a 80-corridor golden hash).

## Behaviour
- Corners are rounded with radius `corner_radius_m` (default width/2 + 0.4) and sampled every 0.25 m.
- Rejected with a reason: walls crossing, corridor folded onto itself (parts far apart along the track closer
  than the width), shorter than 5 m, no place for the start grid.
- The start line is on the first straight with 2.5 m before/after it, else the straightest place.
- Files: `<engine data>/tracks/<slug>.quicktrack.json`.

## Acceptance criteria (→ tests, critical paths)
- [ ] AC1: A drawn rectangle previews with two walls, a start line and ~28 m length; a folded drawing is
  reported as overlapping.
- [ ] AC2: Save → list → load → race (sim WebSocket with `quick_track`) finishes with the centering template;
  delete removes it.
- [ ] AC3: Laps and obstacles end up in the track.
