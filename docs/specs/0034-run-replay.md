# Spec 0034: Run replay v1 — play back team drives, compare two drives

- **Status:** approved (owner: "no preference" 2026-10-11 → agent picked the recommended item; defaults below)
- **Owner:** Daniel Hodeib
- **Plan section:** docs/PLAN.md §7 (replay mode, run comparison in replay)
- **Depends on:** Spec 0027 (run logs, dashboard), Spec 0028 (run summaries)

## Purpose
Every shared test drive is saved as a run log (spec 0027). v1 lets the team watch a drive again with the same
dashboard as live, jump to notes and events, and compare two drives.

## Scope
- In scope:
  - `GET /api/v1/workspace/runs` (drives and Race Control results), `GET …/runs/{run}/timeline?max_hz=10`
    (frames thinned to `max_hz`, all events, notes, `moved_s` = first movement),
    `GET …/runs/compare?a=&b=` (spec 0028 summaries + differences).
  - Replay tab (logged in): list of team drives; player with scrubber, play/pause, 1×/2×/5×; the Live dashboard
    fed with the frame at the cursor (silent alarms); notes and events as marks on the timeline (click = jump).
  - Comparison: speed curves of two drives aligned at the moment each car first moved, plus summary differences.
- Out of scope: ghost car on the map, per-sector deltas (need lap/sector detection on a track), video, MCAP
  logs from the car's SD card (upload comes with the backend upload spec).

## Acceptance criteria (→ tests)
- [ ] AC1: the timeline thins frames to `max_hz`, keeps every event and puts notes and `moved_s` on its time axis.
- [ ] AC2 (Vitest): frame at a time (binary search); speed curves aligned at first movement.
- [ ] AC3 (manual, owner): open a recorded drive, play, jump to a note, compare with a second drive.
