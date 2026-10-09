# Spec: Scan floor plan as underlay for quick tracks

- **Status:** approved (owner request 2026-10-09: "schreibe die software weiter"; plan item "tracks from scans")
- **Owner:** Michael Spisak
- **Plan section:** docs/PLAN.md §3 (TrackScout scans → simulator tracks)
- **Depends on:** Spec 0009 (scan passes in the engine), Spec 0014 (quick tracks), Spec 0025 (track editor layer)

## Purpose
Drawing a quick track over an empty grid means guessing the real corridor. With a TrackScout scan as underlay the
team draws the centreline directly over the scanned hallway and takes the corridor width from the scan.

## Scope
- In scope: `raceforge.capture.floorplan` (car-height occupancy grid, PNG export, corridor widths along a polyline),
  engine `GET /api/v1/scans/{sha256}/floorplan` and `POST /api/v1/scans/{sha256}/corridor-width`,
  `QuickTrack.underlay_sha256` (optional, display only), Tracks tab underlay select and "measure width from scan".
- Out of scope (later): walls taken directly from the scan instead of the drawn corridor, automatic centreline,
  scan-to-track alignment other than the scan's own frame.

## Behaviour
- Floor level: median z of floor-classified vertices (fallback: 2nd percentile of all vertices).
- Occupancy: non-floor, non-ceiling triangles sampled (about two points per 5 cm cell, capped at 4 M points) and
  kept within floor + 5 cm … floor + 50 cm (query params `resolution`, `z_min`, `z_max`); grid margin 0.5 m.
- PNG: occupied cells grey, free cells transparent, row 0 = highest y. The response carries origin, resolution,
  size and the scan trajectory (x, y) so the frontend places it in world metres.
- Width: every 0.25 m along the line, rays perpendicular in both directions (max 3 m each); width = sum of both hit
  distances; samples with an open side or a blocked centre are skipped. The response gives median, minimum and
  sample count; the app rounds the median to 0.05 m (clamped to 0.6–4 m) and sets it as corridor width.
- Results are cached per (scan, parameters) in the engine process.

## Acceptance
- Synthetic corridor 1.6 m wide: measured width 1.6 ± 0.1 m; a shelf above the band is ignored.
- Endpoints return the plan and widths for an opened scan pass; unknown sha → 404.
- A quick track saved with an underlay reloads with the same underlay.
