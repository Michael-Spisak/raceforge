# Spec 0033: Fleet + calibration v1

- **Status:** approved (owner: "no preference" on 2026-10-09 → agent picked the recommended item; decisions
  below are defaults documented here)
- **Owner:** Daniel Hodeib
- **Plan section:** docs/PLAN.md §5 (guided calibration wizards), §5f (fleet)
- **Depends on:** Spec 0005 (car config, bundle, EV3 link), Spec 0010 (teleop), Spec 0012 (deploy panel)

## Purpose
Every physical car differs a little: wheel slip, steering play, a crooked steering zero. v1 keeps one car config
per car (the fleet) and fills its numbers from short guided drives instead of guesses.

## Scope
- In scope:
  1. **Fleet:** car configs in the engine data folder (`cars/<name>.yaml`, created from
     `controllers/car.example.yaml`); `GET /api/v1/cars`, `POST /api/v1/cars/{name}`; the Deploy panel picks a
     fleet car's config.
  2. **Steering trim** (contract change, additive): `ev3.steer_trim_rad` (−0.2…0.2) in car.yaml and the bundle
     manifest; `rf-ev3` adds it to every steering command and removes it from the measured angle.
  3. **Calibration wizards** in the Live tab (test mode, driven with teleop), computed in
     `raceforge.api.calibration` from recorded telemetry:
     - straight drive over a measured distance → `ev3.drive_counts_per_m` (scaled by encoder/true distance) and
       `ev3.steer_trim_rad` (from the heading drift);
     - full-lock circles left and right → `ev3.steer_motor_deg_per_rad` (commanded vs achieved wheel angle,
       atan(L / R) with R = speed / yaw rate) plus the left/right asymmetry.
  4. **Apply:** `POST /api/v1/cars/{name}/apply` writes the values into the car's config, keeping comments; the
     previous file is kept as `.bak`; an invalid result is refused.
- Out of scope (later): sensor offsets and LiDAR yaw wizard, gyro bias, sim calibration from logs (Optuna),
  per-car calibrated sim models, syncing car configs to the team backend.

## Acceptance criteria (→ tests)
- [ ] AC1 (Rust): the trim shifts the steering command and is removed from the measurement.
- [ ] AC2: straight-drive maths: 10 % encoder over-count → counts scaled by 1/1.1; a left drift gives a negative
  trim change.
- [ ] AC3: circle maths: a car that only reaches half the commanded angle doubles `steer_motor_deg_per_rad`.
- [ ] AC4: applying changes keeps comments, writes a `.bak` and refuses invalid values.
- [ ] AC5 (manual, owner): calibrate a real car, rebuild the bundle, it drives straight.
