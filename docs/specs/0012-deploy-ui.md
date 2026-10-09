# Spec: Deploy from the app (bundle + install on the car)

- **Status:** approved (owner request 2026-10-08: "arbeite an den weiteren Features weiter, welche ich in der App brauche")
- **Owner:** Michael Spisak
- **Plan section:** docs/PLAN.md §5 (Deploy & sim-to-real), milestone weeks 4–5 ("Wi-Fi deploy")
- **Related ADRs:** ADR-0007 (Rust core); uses spec 0005 "Deploy" unchanged
- **Depends on:** Spec 0005 (bundle format, `deploy_ssh`, `deploy_usb`, `usb_result`)

## Purpose
The car team must get a controller onto the real car without the command line: pick a controller and the
car config, build a bundle, install it over Wi-Fi/SSH or write it to a USB stick, and see the board's result.
The app only wraps the existing spec 0005 functions; the bundle format and the board side do not change.

## Scope
- In scope: engine endpoints to build a bundle, deploy it (SSH or USB stick) and read the USB result;
  a "Deploy" panel on the Live tab (DE + EN).
- Out of scope: **race-mode bundles** (race mode is an owner gate; use `raceforge bundle --race`),
  deploy over Bluetooth/radio, fleet deploy to several cars, SD image build, bundle history.

## Interfaces (contracts) — additive REST endpoints
- `POST /api/v1/car/bundle` — `BundleRequest {controller: str, params: str | null, car_config: str,
  name: str | null}` → `BundleInfo {path, name, digest, controller, params, car_name, mode, speed_limit_m_s,
  warnings: list[str]}`. The bundle is written to `<engine data>/bundles/<name>` (replaced if it is a bundle).
  Always a **test-mode** bundle. Errors (bad car config, controller does not load) → 422 with the message.
- `POST /api/v1/car/deploy` — `DeployRequest {bundle: str, target: "ssh" | "usb", host: str | null,
  stick: str | null}` → `DeployResponse {result: InstallResult | null, usb_path: str | null}`.
  SSH returns the board's `InstallResult`; USB returns where the bundle was written. Errors → 422 (bad
  request) or 502 (`DeployError`: ssh failed, no answer, digest mismatch, stick not mounted).
- `GET /api/v1/car/deploy/usb-result?stick=…` → `InstallResult | null` (null: stick not in a car yet).

## Behaviour
- The engine data folder is the parent of the workspace cache (`RACEFORGE_WORKSPACE_DIR`/..), so tests and
  E2E runs never touch the user's real bundles.
- Bundle names are reduced to `[A-Za-z0-9._-]` (default: controller file name); no path traversal.
- The telemetry-token warning of `raceforge bundle` is returned in `warnings`.
- The UI remembers controller, params, car config, SSH host and stick path (localStorage; never the token).
- An install that reports `ok: false` is shown as an error with `rolled_back` ("previous bundle restored").

## Acceptance criteria (→ tests, critical paths only)
- [ ] AC1: Building a bundle from a template controller + `controllers/car.example.yaml` returns its digest,
  test mode and the car name; a broken car config returns 422 with the field message.
- [ ] AC2: Deploy to a USB folder writes `raceforge/bundle`; the USB result is null until the car writes
  `result.json`, then it is returned.
- [ ] AC3: The Live tab builds and deploys a bundle (manual check by the owner).
