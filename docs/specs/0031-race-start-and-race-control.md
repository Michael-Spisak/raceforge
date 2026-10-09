# Spec 0031: Race start v1 + Race Control

- **Status:** approved — owner gate for race-mode/safety rules passed in chat 2026-10-09 (Daniel Hodeib:
  start by button + countdown and by pull-away start cable; Race Control with start sequence, timing and laps)
- **Owner:** Daniel Hodeib
- **Plan section:** docs/PLAN.md §5c (race start and "Race Control")
- **Depends on:** Spec 0005 (runtime, EV3 link, bundle), Spec 0030 (race bundles from the app)
- **Open (teacher):** whether optical/acoustic start signals count as "wireless"; the wired start cable is the
  safe default (PLAN "Still open"). Light/tone start and false-start detection are left for later.

## Purpose
A race car must not drive off when its runtime boots, and all cars must be able to start at the same moment.
The car gets a READY state that only a start signal ends; our team gets a Race Control screen that shows
the start lights and times the race.

## Scope
- In scope:
  1. **Start gate** (`rf-core::start`): states READY (`ready` / `ready_wire` when the start cable is plugged
     in), COUNTDOWN, GO. Until GO the controller is not called and the motors stay stopped; emergency stop and
     link checks stay active. A `start` event (method) is logged.
     - `button`: press and release the start button (default: EV3 centre button), then `countdown_s`.
     - `wire`: pull-away cable on `ev3.start_touch_port` (dry contact, closed = plugged in). It must have
       been seen plugged in once; opening it starts at once (no countdown).
  2. **Bundle**: `runtime.start {methods, countdown_s, button}` and `ev3.start_touch_port` (car.yaml); a race
     bundle without a start config gets `button`, 3 s — a race car never drives off at boot. Test bundles
     without `start` drive at once as before. `wire` without a start port is refused.
  3. **Race Control tab** (desktop/browser, tablet-friendly, for our team): full-screen five red lights at a
     configurable step, lights out after a random hold = GO (with tones); race clock; a big LAP button per car;
     undo lap; incidents; DNF; standings (finished by time, racing by laps, DNFs); "Save result" stores a
     `run` object with `race.json` in the team workspace (`POST /api/v1/race-control/results`).
- Out of scope: light/tone start detection, false-start detection, start box hardware, simulated starts,
  EV3 LCD countdown display, lap counting by camera.

## Acceptance criteria (→ tests)
- [ ] AC1 (Rust unit): button starts after release + countdown; the wire starts only after being plugged in;
  methods that are not configured are ignored.
- [ ] AC2: a race bundle without start config gets `button`; `wire` without `start_touch_port` is refused.
- [ ] AC3 (Vitest): laps only between start and finish; standings order; time format.
- [ ] AC4: saving a race result creates a `run` object with `race.json`.
- [ ] AC5 (manual, owner): real car in READY; button → 3 s → drives; cable plugged + pulled → drives at once;
  Race Control lights on the iPad, laps tapped, result saved.
