# Spec 0009: Scan viewer v1 — TrackScout passes in the desktop app

- **Status:** implemented (PR) (owner request 2026-10-08: "damit man die scans im frontend sieht")
- **Owner:** @Michael-Spisak · **Author:** Claude
- **Plan section:** docs/PLAN.md §2 (capture → process → edit), §2f (TrackScout)
- **Related:** Spec 0007 (`.tscan`, importer, inbox), Spec 0006 (workspace, blob cache), Spec 0008 (frontend shell)

## Purpose
Make recorded corridor scans visible right after recording: a **Scans** tab lists every TrackScout pass the
laptop can reach (team workspace, passes received from a phone, local `.tscan` files) and shows a pass in 3D —
Apple's classified scene mesh, the camera path, discarded ranges — with its key facts. Several passes of one
track share a coordinate frame and can be overlaid, so the team sees coverage and alignment before the scan
pipeline (registration, fusion, track editor) exists.

## Scope
- In scope:
  1. Engine endpoints (additive) under `/api/v1/scans`:
     - `GET /scans` → tracks: workspace `capture` objects (latest version, one entry per pass file) plus inbox
       passes and opened files; each pass is identified by its SHA-256.
     - `POST /scans/open {path}` → registers a local `.tscan` file (validated) for this engine session.
     - `GET /scans/{sha256}` → summary (as `raceforge capture info`), segments with discarded ranges, the camera
       trajectory (≤ 2000 points, RaceForge Z-up frame, kept flag per point) and the bounding box.
     - `GET /scans/{sha256}/mesh?max_faces=N` → the pass's meshes (all segments) as base64 little-endian arrays:
       positions `float32[3·V]`, indices `uint32[3·F]`, per-face ARKit class `uint8[F]`. More than `max_faces`
       (default 300 000) → uniform face subsampling (preview only, never written back).
     - Workspace passes are downloaded on demand into the blob cache; parsed results are cached in memory (LRU).
  2. Frontend **Scans** tab: list grouped by track; select one or more passes (overlay); summary panel; 3D view
     with mesh coloured by class or by pass, trajectory (kept vs discarded), start marker, auto-fit camera; toggles
     for mesh / trajectory / classes; class legend; "open .tscan file" field. DE + EN.
- Out of scope (later specs): RGB video and depth point clouds, registration/fusion of passes, editing, RoomPlan
  geometry display, coverage heatmap, the track editor.

## Interfaces (contracts)
Pydantic models in `raceforge.api.models`: `ScanPassRef`, `ScanTrack`, `ScanDetail`, `ScanSegment`, `ScanMesh`.
All coordinates in the RaceForge frame (Z up, metres) as produced by `raceforge.capture.tscan`.

## Behaviour
- A pass that fails validation (checksum, schema) is listed with its error; opening it returns 422 with the reason.
- Offline: workspace passes already in the blob cache open; others report "not downloaded (offline)".
- Mesh colours: ARKit classes none/wall/floor/ceiling/table/seat/window/door with a fixed colour-blind-safe palette.

## Non-functional targets
- A 5-minute High pass (~300 k faces) opens in < 3 s on a MacBook Air M2 (second open: cached, < 0.5 s).
- Frontend keeps ≥ 30 fps while orbiting a 300 k-face mesh.

## Acceptance criteria (→ tests)
- [x] AC1: `GET /scans` lists workspace capture passes and inbox passes with track, pass type, size and source.
- [x] AC2: `GET /scans/{sha}` returns summary, segments, a trajectory in the RaceForge frame (golden pose from the
      synthetic pass) with discarded points flagged, and bounds containing mesh and trajectory.
- [x] AC3: `GET /scans/{sha}/mesh` round-trips the synthetic pass mesh exactly; `max_faces` subsamples.
- [x] AC4: Damaged pass → listed with error / 422; unknown sha → 404; `POST /scans/open` rejects non-`.tscan`.
- [x] AC5 (Playwright): open a synthetic `.tscan` by path in the Scans tab → summary visible, 3D canvas rendered,
      toggles work; DE/EN strings present.

## Decisions (agent defaults)
- Transport as base64 JSON (no new dependency, typed in OpenAPI); binary endpoints can come later if needed.
- Face subsampling instead of real decimation for the preview (no mesh library needed).
- "Hide ceiling" is on by default and a height cut is available, so the corridor and the camera path are visible
  from above (owner test with real passes, 2026-10-08).
- TrackScout stores the accumulated ARKit mesh once, on the last segment, at the end of a pass; it covers all
  segments, so the viewer shows every mesh of a pass rather than one per segment.
