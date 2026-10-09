# Spec: Quick-track walls from the scan

- **Status:** approved (owner request 2026-10-09: "start with the next goal", follow-up of spec 0024)
- **Owner:** Michael Spisak
- **Plan section:** docs/PLAN.md §3 b6–b7 (sim geometry from scans, 2D map and width profile), e0 (quick scan)
- **Depends on:** Spec 0014 (quick tracks), Spec 0024 (scan floor plan), Spec 0025 (track editor layer)

## Purpose
With spec 0024 the team draws over the scanned corridor, but the simulated walls still sit at one fixed width. Here
the walls follow the real corridor: niches, door frames, pillars at the wall and width changes end up in the sim.

## Scope
- In scope: `raceforge.track.scan_walls` (`ScanGrid`, `fit_to_scan`, `unfold`), `QuickTrack.scan_walls`
  (optional; the grid is embedded so workers build the track without the scan file), engine
  `GET /api/v1/scans/{sha256}/grid`, `assemble_corridor(walls_lr=…)`, Tracks tab checkbox "Walls from the scan".
- Out of scope (later): free-standing scanned objects as track objects (place them with the track editor),
  wall polylines independent of the drawn line (junctions, rooms), scan alignment tools.

## Behaviour
- Grid: the spec 0024 floor plan at 5 cm, floor + 5–50 cm, packed bits, zlib, base64; max 16 M cells.
- Rays every 0.25 m perpendicular to the drawn (filleted) line, up to 3 m per side. First pass: the line moves to
  the corridor middle, smoothed over 2 m. Second pass from the moved line: wall distances, median of 3 samples
  (features shorter than about 0.5 m along the corridor are smoothed away), gaps (open doors, glass, unscanned
  parts, junctions) continue the neighbouring wall, a side without any wall gets one at `width_m / 2`, inner walls of bends stay within 0.9 times the bend radius, small folds of the wall polylines are
  removed (points moved onto the crossing).
- Errors (track not built, reason shown in the Tracks tab): the moved line runs through a scanned obstacle for two
  or more samples; the free width (left + right) drops below 0.3 m.
- Checkpoints, start grid and validation use the symmetric part of the width around the moved line.

## Acceptance
- Ring corridor 1.8 m wide drawn 0.3 m off-centre with `width_m = 1.0`: line within 5 cm of the middle, median
  width 1.8 ± 0.06 m.
- A block across the line and a 0.2 m gap are reported with their reasons.
- `GET /api/v1/scans/{sha}/grid` returns the floor plan grid of an opened pass.
