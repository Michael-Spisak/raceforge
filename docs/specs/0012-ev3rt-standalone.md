# Spec 0012: EV3RT standalone — the controller runs in C on the EV3 (no onboard board)

- **Status:** approved (owner decision 2026-10-08: EV3RT in both roles); implementation after spec 0011
- **Owner:** @Michael-Spisak · **Author:** Claude
- **Plan section:** docs/PLAN.md §4/§5 (controllers, deploy), §5a (controller code)
- **Related:** Spec 0004 (controller SDK), Spec 0011 (EV3RT bridge, shared C library)

## Purpose
Without an onboard board the EV3 alone drives the car: a controller written in **C** runs under EV3RT with the
EV3's motors and sensors (no LiDAR). The same C controller must also run **in the RaceForge simulator** so the team
can develop and test it before deploying — the core idea of the tool (same code in sim and on the car).

## Scope
- In scope:
  1. **C controller SDK** (`ev3rt/sdk/raceforge_controller.h`): `rf_observation` (time, ultrasonic distances,
     gyro rate/heading, speed, steering, bumper, battery), `rf_command` (steering rad, speed m/s), a controller
     struct with `setup/step/teardown` and named channels (`rf_emit`) — the C mirror of the Python API (spec 0004).
  2. **EV3RT runtime for C controllers** (`ev3rt/apps/raceforge_standalone`): 100 Hz loop, sensors → observation,
     controller step with deadline measurement, command → the same drive/steer loops as the bridge (shared code),
     safety: speed limit, touch e-stop, button stop, exceptions → stop; run log to the SD card (CSV, imported by
     RaceForge as a RunLog).
  3. **Simulator support**: RaceForge compiles a C controller for the host (clang/gcc) into a shared library and
     runs it in the sim through a thin Python wrapper (`CController`, ctypes) — selectable like Python controllers.
  4. **Templates in C**: `wall_follow` and `centering`, ported from the Python templates, with the same tests.
  5. **Build/upload**: `raceforge car ev3rt build --standalone <controller.c>` and `upload` (spec 0011 tooling).
- Out of scope (later): teleop in standalone mode (EV3RT Bluetooth SPP from the laptop), LiDAR on the EV3.

## Acceptance criteria (→ tests)
- [ ] AC1: the C templates complete the corridor test in the sim like their Python counterparts (spec 0004 AC).
- [ ] AC2: a C controller that crashes (returns error) or overruns its deadline stops the car in the sim and on
      the EV3 runtime (host build with a mock EV3 API).
- [ ] AC3: CI cross-compiles the standalone app with a template controller (EV3RT 1.1 SDK).
- [ ] AC4: the SD-card run log imports as a RunLog and replays in the Replay tab.
- [ ] AC5 (manual): a template drives the real car standalone in the corridor.
