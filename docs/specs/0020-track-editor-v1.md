# Spec: Track editor v1 — race setup, objects, surfaces, check distances, validation

- **Status:** approved (owner, 2026-10-09: car width = track setting, separate finish line, validation advisory)
- **Owner:** Michael Spisak
- **Plan section:** docs/PLAN.md milestone weeks 8–9 ("Track editor v1"), §2c (Track editor), §2e0 (quick tracks)
- **Depends on:** Spec 0001 (core `Track`, `RaceSetup`, `TrackObject`, `SurfaceRegion`, `CheckMeasurement`),
  Spec 0014 (quick tracks), Spec 0003 (sim, `Corridor`)

## Purpose
Spec 0014 draws a corridor and the engine decides everything else (start line, grid, checkpoints).
The team needs to correct that by hand and to describe the *real* school corridor precisely: where the
start/finish are, how the cars line up, which objects stand where and how much they vary, where the floor is
slippery or reflective, and whether the model matches tape-measure distances. The editor works on a
`Track` **edit layer** (non-destructive: the quick-track drawing stays the base, edits are stored on top),
so the same layer can later sit on a scan-processed track (weeks 8–9+, not part of this spec).

## Scope
- In scope:
  - `TrackEdit` layer + `apply_edit(Corridor, TrackEdit) -> Track` in `raceforge.track.edit`.
  - Race setup editing: start line, finish line (same line = laps), driving direction, laps, start grid
    for N cars (auto-fill + drag each slot), checkpoints (auto + add/move/delete), no-go zones.
  - Objects: library (box, cone, bin, bench, pillar, opponent-car marker), place/move/rotate/delete,
    static/movable, randomisation ranges (position, yaw, size, presence probability).
  - Surface regions (polygon, friction, material, LiDAR reflectivity).
  - Check distances (two points + tape-measured length) and the error against the model.
  - Track validator with a readable report (see Behaviour).
  - Tracks tab: edit mode next to draw mode; 2D plan with layers toggle; undo/redo; save as new track version.
  - `SimStart.quick_track` / `TrainRace.quick_track` use the edited track unchanged (edits are applied when
    the track is built).
- Out of scope (later specs): label review / segment→class mapping table, brush/lasso selection, 3D view and
  scan layers, geometry editing of scanned walls, door variants, team sync (workspace object), publishing
  drafts to the backend, coverage heatmap.

## Interfaces (additive, no change to `raceforge.core` schemas)
- `TrackEdit {race_setup: RaceSetupEdit?, objects: [TrackObject] (replaces the quick-track obstacles when
  set), surfaces: [SurfaceRegion], check_measurements: [CheckMeasurement], classes: [ClassDef]}` — uses
  plain (x, y) tuples (EditObject/EditSurface/EditCheck) that are converted to the core models; core schemas unchanged.
- `RaceSetupEdit {start_line?: Segment2D, finish_line?: Segment2D, direction?: Vec2, laps?: int,
  grid_cars: int (1–8), grid_poses?: [Pose2D], checkpoints?: [Segment2D], no_go_zones: [Polygon2D]}`.
  Missing fields fall back to the automatic values of 0014.
- `QuickTrack.edit: TrackEdit | null` (default `null`; old files load unchanged).
- `POST /api/v1/tracks/quick/preview` → `QuickTrackPreview` additionally carries `finish_line`, `start_grid`,
  `checkpoints`, `no_go_zones`, `surfaces`, `checks: [CheckResult {a, b, measured_m, model_m, error_m}]`
  and `validation: ValidationReport`.
- `POST /api/v1/tracks/quick/validate` → `ValidationReport {ok, items: [{severity: error|warning|info,
  code, message, where?: Vec2}]}`.
- Frontend only talks to these endpoints (AGENTS.md architecture rule).

## Behaviour
- Edits are applied after the base corridor is built; invalid edits never produce a 4xx for the preview —
  they show up as validation errors (`ok: false`), as in 0014.
- Start grid auto-fill: N slots in two columns behind the start line, 0.5 m lateral / 0.7 m longitudinal spacing,
  inside the free space; slots can be dragged and are validated individually.
- Validator checks (errors block "race-ready", warnings do not):
  1. start line and finish line exist and lie inside the corridor (error);
  2. start grid slots inside free space, not overlapping walls, objects or each other (error);
  3. track connected along the centreline; loop closes (error);
  4. corridor wider than the widest configured car + 0.1 m everywhere; narrow spots listed with position
     (warning below 1.5× car width, error below 1.1×);
  5. no no-go zone covers the start grid or the whole corridor width (error);
  6. object overlaps walls or blocks the full corridor width (error); blocks > 70 % of the width (warning);
  7. check distances: model length vs tape measure; warning > 2 cm, error > 5 cm (plan target ±1–2 cm);
  8. surface regions outside the corridor (warning).
- Randomisation (`RandomRange`) is applied by the sim per run seed (training seeds from 0, held-out from 1000, as
  in 0013); a presence probability < 1 means the object may be absent in a run.
- Files: the edit is part of `<engine data>/tracks/<slug>.quicktrack.json` (field `edit`).
- Undo/redo covers every edit operation in the frontend (50 steps, as in the draw mode).

## Non-functional targets
- Preview incl. validation < 300 ms for a 200 m corridor (it is debounced while editing).
- Editing never mutates the base drawing; removing `edit` restores the 0014 behaviour exactly (golden test).

## Acceptance criteria (→ tests, critical paths)
- [ ] AC1: A quick track without `edit` builds the same `Track` as before this spec (hash equal to the 0014 result).
- [ ] AC2: Moving the start/finish line and the grid changes `race_setups[0]`; the sim spawns cars on the edited
  grid and lap timing triggers on the edited finish line (centering template finishes 1 lap).
- [ ] AC3: Validator reports: finish line outside the corridor, grid slot in a wall, object blocking the corridor, a too narrow
  spot for a 0.3 m wide car, and a check distance off by 6 cm — each with its code and position.
- [ ] AC4: Object randomisation: two run seeds give different object poses within the ranges; presence
  probability 0 removes the object in every run.
- [ ] AC5: Save → list → load round-trips the edit; old quick-track files without `edit` still load.
- [ ] AC6: Tracks tab (manual): edit start/finish, drag a grid slot, add a surface region and a check distance,
  validation list updates, undo/redo works.

## Open questions
1. Car size for the corridor-width check: use the car currently selected in Simulate, or a track setting
   ("max car width", default 0.35 m)? (Proposal: track setting, default 0.35 m.)
2. Is one finish line per `RaceSetup` enough for v1 (start = finish for laps), or do point-to-point races need a
   different finish? (Proposal: both supported, as `RaceSetup` already has two segments.)
3. Should "race-ready" (validator `ok`) be required to start a race-mode run, or only advisory? (Proposal:
   advisory in v1; race-mode rules need their own, human-approved spec.)
