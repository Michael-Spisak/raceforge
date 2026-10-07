# Spec 0002: Parametric quick-start (real LEGO parts) → Assembly → MJCF

- **Status:** approved (2026-10-07) — implementing
- **Owner:** @Michael-Spisak
- **Plan section:** docs/PLAN.md §1 (Construct: parametric quick-start), §3 (Simulate)
- **Related ADRs:** ADR-0003 (MuJoCo), ADR-0011, ADR-0012 (new: mujoco + numpy, LDraw data)
- **Depends on:** Spec 0001 (core schemas)

## Purpose
Generate a **buildable LEGO car from a few parameters**, as a versionable `core.Assembly` made of real
LDraw parts, and turn any such assembly into a **MuJoCo model (MJCF)** that the simulator and training use.
This lets simulation, controller SDK and training start in week 2–3, long before the full 3D editor exists,
and gives the 3D modeler a correct baseline to refine later.

## Scope
- In scope:
  1. `raceforge.parts`: LDraw library location/download into a local cache, minimal LDraw reader
     (resolve sub-files, colours ignored, compute part bounding boxes and simple geometry stats), and a
     **curated catalogue** (~50–100 parts used by the quick-start) with masses, prices and hand-defined
     connectors.
  2. `raceforge.construct.quickstart`: parameters → Assembly using **templates per layout** (RWD, AWD, FWD;
     always LEGO Ackermann front steering) with **grid-snapped** dimensions (1 stud = 8 mm).
  3. `raceforge.construct.derive`: derived data from an assembly — mass, centre of gravity, inertia,
     wheelbase, track width, turning radius, top speed, plausibility checks.
  4. `raceforge.sim.mjcf`: Assembly (+ parts + derived data) → MJCF (rigid-group bodies, steering knuckles,
     wheels, drive with differential or locked axle, steering servo, sensor sites, collision proxies).
  5. `.mpd` export of a quick-start car (opens in LDView / BrickLink Studio for visual checking).
- Out of scope: the 3D editor (spec later), LDCad shadow-library snapping for arbitrary parts (editor spec),
  sensor noise models and corridor generation (spec 0003), controllers (spec 0004), BrickLink API (later;
  curated weights now, API added afterwards — owner decision "both").

## Parameters (`QuickStartParams`, Pydantic, in `raceforge.construct`)
| Parameter | Values / default |
|---|---|
| `layout` | `rwd` (default) \| `awd` \| `fwd` |
| `wheelbase_studs` | template-dependent discrete range, e.g. 11–21, default 15 (= 120 mm) |
| `track_studs` | discrete axle-length options, e.g. 9–15, default 11 |
| `wheel` | catalogue choice (rim + tyre combo), default the 43.2 mm "56908 + 55976"-class combo; list in catalogue |
| `differential` | `true` (default, LEGO 62821) \| `false` (locked axle) |
| `drive_motor` | `ev3_large` (default) \| `ev3_medium` \| `dc_motor` (params required) |
| `drive_gear_ratio` | from available gear pairs (e.g. 1:1, 12:20, 12:36, 20:12, 36:12); default 1:1 |
| `steering_motor` | `ev3_medium` (default) \| `servo` (params required) |
| `max_steer_deg` | 15–40, default 30 (limited by template geometry) |
| `ackermann_pct` | 0–100 %, default derived from template geometry (typical LEGO: 40–70 %) |
| `steering_play_deg` | default 3° (calibrated later, spec 0006 wizard) |
| `sensors` | list of `{device, preset, offset?}`; default **ultrasonic front + ultrasonic left + ultrasonic right** |
| `board` | `raspberry_pi_5` (default) \| `orange_pi_5` \| `none` |
| `battery` | for board/motor if external; EV3 internal battery always present |
| `measured_mass_kg`, `measured_cog` | optional overrides (from scale / tilt test) |

Any value outside the template's range is rejected with the nearest valid options listed.

## Template model
- A template is a YAML file in `src/raceforge/construct/templates/<layout>.yaml` describing **slots**:
  chassis rails, cross members, front steering module (knuckles `32069`-class steering arms + tie rod),
  rear/front drive module (axle, optional differential `62821`, gears), motor mounts, EV3 brick mount,
  board/battery mounts, sensor mount points.
- Each slot maps parameters → concrete part numbers + poses (grid arithmetic in LDU, converted with
  `core.frames`), and declares the **connections and joint roles** it creates (so the assembly carries its
  own mechanics: steering pivots, wheel axles, drive motor, steering motor, gear meshes with ratio).
- Templates are authored by AI, checked by a human by **opening the exported `.mpd` in LDView/Studio** and,
  for the default variant, by building it physically. A template change needs a human review.
- Non-LEGO devices (Pi, external battery, servo, DC motor) are placed as `printed`/`device` parts with
  simple box geometry and mount points; the steering mechanism itself is always LEGO (rule from plan).

## Curated catalogue (`src/raceforge/parts/catalogue/*.yaml`)
- Per part: LDraw id, name, mass (g, with source note), connectors (hand-defined for these parts, in the part's
  LDraw frame, converted to core frame on load), price placeholder, category.
- Masses come from BrickLink catalogue values entered manually (facts, with source note); the BrickLink API
  importer comes later.
- Device parts (EV3 brick `95646c01`, large motor `95658`, medium motor `99455`, ultrasonic `95652`,
  gyro `99380`, touch `95648`) carry `core.devices` blocks with datasheet defaults.
- Data files are licensed **CC BY 4.0** (derived from LDraw part data); attribution in `NOTICE`.

## LDraw library handling
- `raceforge parts fetch` downloads `https://library.ldraw.org/library/updates/complete.zip` (~146 MB) into
  `~/.cache/raceforge/ldraw` (or `$RACEFORGE_LDRAW_DIR`); never into the repo.
- CI caches the library with `actions/cache`. Unit tests use a **small committed fixture subset** of LDraw
  files (with licence headers intact, CC BY) so most tests run without the download.
- LDraw licence/attribution text shown in About and docs.

## Derived data (`raceforge.construct.derive`)
- Mass/CoG/inertia: sum of part masses at world placements (box inertia per part from its bounding box);
  device masses included; measured overrides take precedence and are flagged.
- Geometry: wheelbase, track width, wheel radius, ground clearance, overall size.
- Kinematics: max steer angle (inner/outer), turning radius, top speed = motor no-load speed × gear ratio ×
  wheel radius, max acceleration from stall torque.
- **Plausibility checks (warnings, not errors):**
  - rollover speed at a given radius: `v_roll = sqrt(g · r · track / (2 · h_cog))`; warn if below target speed
    in a 1 m radius curve;
  - minimum turning radius vs a configurable narrowest corridor width;
  - drive torque vs mass (can it accelerate at ≥ 0.5 m/s²?);
  - expected battery runtime vs the 30-min target (plan §9d);
  - steering motor torque vs estimated steering load.

## MJCF generation (`raceforge.sim.mjcf`)
- Rigid groups (parts connected without a joint role) are merged into one body each: chassis, left/right
  steering knuckle, wheels, drive gears.
- Collision: per body a small set of boxes from part bounding boxes (merged), wheels as **cylinders** with
  tyre radius/width; visual: optional mesh references for the frontend (not needed by MuJoCo).
- Steering: two hinge joints (knuckles) **coupled by a joint-equality polynomial** fitted to the Ackermann
  curve at `ackermann_pct`; the steering motor is a position servo with max speed and torque; **play**
  modelled as a limited intermediate joint (± `steering_play_deg`) between servo and knuckles.
- Drive: wheel hinge joints; **differential** = motor drives a fixed tendon averaging both wheel joints;
  **locked** = joint equality between both wheels; motor modelled with torque–speed curve from datasheet.
- Sensors: `site`s at device poses (used by spec 0003 for rangefinder/IMU sensors).
- Physics: timestep 2 ms, `condim=6` for tyres, friction defaults (LEGO rubber on school floor μ ≈ 0.8),
  all calibratable.
- Output is deterministic (same assembly → byte-identical MJCF).

## Non-functional targets
- Quick-start generation + derive + MJCF for the default car: < 1 s on a MacBook Air M2 (library cached).
- Default car steps in MuJoCo at ≥ 20× real time single-threaded on a MacBook Air M2.

## Acceptance criteria (→ tests)
- [ ] AC1: For every layout and the full parameter grid (wheelbase × track × differential × motors), the
      generator produces an `Assembly` that validates (spec 0001) and passes `validate_against_parts`.
- [ ] AC2: Out-of-range parameters are rejected with the nearest valid values in the message.
- [ ] AC3: Derived wheelbase/track match the parameters exactly (grid arithmetic, ±0.1 mm); mass of the default
      car equals the sum of catalogue masses (golden value); measured overrides replace computed values.
- [ ] AC4: Plausibility checks fire for constructed bad cases (high CoG, tiny steer angle, weak motor) and stay
      silent for the default car.
- [ ] AC5: MJCF loads in MuJoCo for all generated variants; deterministic output (byte-identical).
- [ ] AC6: Sim sanity (default car on flat ground): drives straight within 2 cm lateral drift over 3 m with zero
      steering; turning radius at max steer within ±10 % of the derived value; with differential the wheels
      turn at different speeds in a curve, with locked axle they do not.
- [ ] AC7: Steering servo reaches commanded angle within its rate limit; play produces a dead band of
      `steering_play_deg` (±0.5°).
- [ ] AC8: Exported `.mpd` references only official LDraw parts (plus clearly marked RaceForge placeholder
      parts for non-LEGO devices) and parses back to the same part list. **Manual check by the owner:** the
      default car looks correct in LDView/Studio.
- [ ] AC9: Unit tests run without the full LDraw download (fixture subset); an integration test job with the
      cached full library runs in CI.
- [ ] AC10: Performance targets met (timing tests skipped on CI).

## Open questions
- Exact catalogue wheel/tyre list and template ranges are fixed while authoring templates (AI proposes,
  owner confirms by viewing the `.mpd`).
