# Spec 0030: Race day v1 — race bundles from the app, radio pre-check, race-day checklist

- **Status:** approved — owner gate for race-mode rules passed in chat 2026-10-09 (Daniel Hodeib: race
  bundle from the app for every member, radio pre-check, race-day checklist; agent may merge)
- **Owner:** Daniel Hodeib
- **Plan section:** docs/PLAN.md §5c (race-day kit and flow), §7 (race-mode radio check), §6 (deploy policy)
- **Depends on:** Spec 0005 (race mode, radio check AC5), Spec 0012 (deploy panel), Spec 0027 (dashboard)

## Purpose
Race mode exists on the car (spec 0005): it arms only when no radio can be active and then closes every
socket. But a race bundle can only be built on the command line, nobody can see *before* switching to race
mode why the car would refuse, and the race-day preparation lives in people's heads. v1 closes these gaps.

## Scope
- In scope:
  1. **Race bundle from the app:** the Deploy panel gets "Race bundle"; building one needs a confirmation that
     lists what changes (radios must be off, no live view or teleop, test speed limit lifted). Every member may
     build one (PLAN §6: no deploy gates); the runtime safety (radio check, emergency stop) stays active.
     `BundleRequest.race: bool = false` (additive).
  2. **Radio pre-check:** new car-runtime WebSocket command `{"type":"radio_check"}` (test mode) runs the same
     check as arming race mode and answers `{"type":"radio_check","ok":bool,"violations":[str]}` without arming
     anything. The engine forwards it; the Live tab shows every violation.
  3. **Race-day checklist** in the Live tab: automatic items (connected; battery ≥ threshold; no faults and
     sensors reporting; radio pre-check ok; last deploy installed and verified, and it was a race bundle) and
     manual items (batteries charged and measured, emergency stop tested, start mode armed, spare car ready,
     calibration done). "Save" sends the result as a note to the car log (and so into the run log, spec 0027).
     A warning if the installed bundle is not a race bundle.
- Out of scope: start methods / Race Control (own spec), bundle tagging in the backend, SD image, fleet.

## Interfaces
- Car runtime (contract change, owner-approved here): `radio_check` command as above; `rf-runtime` injects
  `radio::check(sys_root, radio_usb_ids)` into the telemetry server (`TelemetryServer::set_radio_check`).
  Older runtimes answer `ack {cmd: "radio_check", ok: false}`; the UI shows "update the runtime".
- Engine: `FORWARD_TO_CAR` gains `radio_check`; `BundleRequest.race`; `BundleInfo.mode` already reports it.

## Acceptance criteria (→ tests)
- [ ] AC1 (Rust): `radio_check` answers with the injected check's violations; without one, an ack `ok: false`.
- [ ] AC2: `POST /api/v1/car/bundle` with `race: true` builds a bundle whose manifest has mode `race`.
- [ ] AC3: the engine forwards `radio_check` to the car and the answer back (fake car).
- [ ] AC4 (manual, owner): Wi-Fi on → pre-check lists it; Wi-Fi off + dongle out → ok; checklist saved as a note.
