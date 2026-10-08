# Spec 0005: Car runtime (Rust core + Python controllers) and EV3 bridge

- **Status:** approved (2026-10-07)
- **Owner:** @Michael-Spisak
- **Plan section:** docs/PLAN.md §5 (Deploy & sim-to-real), §5b–5f (driving stack, safety, battery, fleet), §7 (live)
- **Related ADRs:** ADR-0007 (Rust core), ADR-0013 (MCAP); new ADR-0014 (EV3 link transport)
- **Depends on:** Spec 0001 (TelemetryFrame), Spec 0004 (RobotIO contract)
- **Deadline:** first drive on the real car at the end of October (critical path)

## Purpose
Run the team's Python controllers on the real car with the same `RobotIO` contract as in the simulator,
reliably at 50 Hz, with safety features that cannot be bypassed by controller code, on-car MCAP logging
and a live telemetry/teleop link in test mode.

## Scope
- In scope (v1, needed for the first real drives):
  1. `car_runtime/` Rust workspace: `rf-core` (timing loop, watchdog, safety, modes), `rf-ev3` (EV3 link client),
     `rf-lidar` (LD06/LD19 UART driver), `rf-log` (MCAP writer, same schema as spec 0003),
     `rf-telemetry` (WebSocket server: telemetry out, teleop/stop/mode in), and the controller bridge
     (**RealIO**, see "Python bridge").
  2. `ev3_side/`: small ev3dev Python program on the EV3 (motors, ultrasonic, gyro, touch, buttons, LEDs,
     LCD status) speaking a compact binary protocol to the board.
  3. Safety: hardware emergency stop input, watchdog stop on controller crash/hang, test-mode speed limit
     (default: car maximum, configurable), teleop dead-man, **race-mode radio check** (refuse to arm if any
     Wi-Fi/Bluetooth interface is up or a radio USB device is present; no sockets in race mode).
  4. Deploy: bundle format (controller file + params + car config + versions), `raceforge deploy --usb | --ssh`,
     hash check at runtime start, automatic log upload hook (upload itself comes with the backend spec).
  5. Board setup: install script for Raspberry Pi OS / Armbian (aarch64); SD image build follows later.
- Out of scope (later specs): camera pipeline, localisation/racing line, calibration wizards, ESP-NOW,
  RL policy (ONNX) runner, HIL/SIL coupling with the simulator, SD image CI, backend upload.

## EV3 link (ADR-0014)
- Physical: **USB cable** between board and EV3 (wired, allowed during the race).
- Transport: ev3dev's default USB-Ethernet gadget (CDC-ECM/RNDIS, static link-local IPs) with **UDP** frames
  (no TCP head-of-line blocking); USB serial (CDC-ACM) as a fallback if the gadget is unavailable.
  ADR-0007 said "USB serial": same cable, transport refined here.
- Protocol: little-endian binary frames with sequence number and CRC-16. Board → EV3 at 100 Hz:
  steering target (deg), drive duty/speed, LED/LCD state, keep-alive. EV3 → board at 100 Hz: tacho counts and
  speeds for all motors, ultrasonic (cm ×10), gyro rate/angle, touch/buttons, battery voltage, timestamps.
- EV3 failsafe: if no valid frame for 150 ms → motors stop (brake), LCD shows "LINK LOST".
- Loop on the EV3 measured in HIL; if ev3dev Python cannot reach 100 Hz, rate drops to 50 Hz (configurable).

## Runtime loop (rf-core)
- Fixed 50 Hz control loop (configurable 20–100 Hz) on a real-time-prioritised thread; sensor threads for
  EV3 link and LiDAR fill a latest-value store with timestamps (monotonic ns).
- Each tick: build `Observation` → send it to the controller host process, wait for its `Command`
  (up to the deadline)
  → validate `Command` (finite, clamped) → apply speed limit → send to EV3 / DC-motor driver.
- **Watchdog** (separate thread): if a step exceeds its deadline (default 15 ms) or raises → motors stop
  immediately, fault logged + shown on the EV3 LCD; controller restarted only by the resume button or a new
  deploy (plan: "stop immediately").
- Sensor policy per sensor: `critical` (stop) or `optional` (degrade, speed × 0.5) — from bundle config.
- Modes: `test` (sockets on, teleop allowed), `race` (armed only after radio check passes; sockets closed,
  teleop refused, speed limit lifted), `hil` (later).
- Start: button / countdown; light/tone and wired-trigger start come with the Race Control spec.

## Logging & telemetry
- MCAP on the SD card: `/telemetry` (TelemetryFrame JSON, identical to the simulator), `/ev3_raw`, `/lidar_raw`
  (reduced: sectors + decimated points per plan), `/events`. Logs kept until upload is confirmed.
- Time: monotonic clock + wall offset (RTC if present, corrected on next sync) — spec 0001 `Timestamp`.
- WebSocket (test mode): telemetry at configurable rate (default 20 Hz), commands: `stop`, `teleop{steer,speed}`
  with dead-man (expires after 300 ms), `mode`, `note`. Same JSON as the Live tab will use.

## Deploy (`raceforge deploy`)
*Added 2026-10-08, pending approval: details for scope item 4, decided with the owner (USB stick,
system OpenSSH, dedicated deploy user).*

- **Bundle identity:** the SHA-256 of `bundle.json`. It lists every file's hash, so it identifies
  the whole bundle. `raceforge deploy` compares it before and after the install.
- **Board layout:** each bundle lives in `/opt/raceforge/bundles/<digest[:12]>-<name>/`, owned by
  root and read-only for the runtime. `/opt/raceforge/bundle` is a symlink to the current one and is
  swapped atomically. The current bundle and the two before it are kept; older ones are deleted.
- **Installer** `raceforge-install-bundle` (root, `python -m raceforge.car.install`), shared by
  both paths:
  1. It takes the bundle as a tar stream on stdin (SSH) or as a directory (USB stick). Only
     regular files are accepted; there are no links, devices or absolute or `..` paths, and the
     size is limited (64 MiB, 64 files).
  2. It validates the manifest and every file hash before anything changes. A bad bundle is
     refused and the running one stays.
  3. It swaps the symlink, restarts `rf-runtime.service` and waits up to 10 s. If the runtime
     rejects the bundle (exit 4), the installer swaps back to the previous bundle and restarts it.
     A runtime still waiting for the EV3 or LiDAR (exit 1, retried) counts as installed.
  4. It prints one JSON result: `ok`, name, digest, previous digest, service state, rolled back,
     detail.
  5. One install at a time (lock file).
- **`raceforge deploy BUNDLE --ssh [USER@]HOST`** (test mode, Wi-Fi/Tailscale/Ethernet):
  - It verifies the bundle locally, streams it as tar through the system `ssh` (Windows 10+,
    macOS, Linux; the team's keys and `~/.ssh/config`), prints the result and fails unless the
    board reports the same digest.
  - The default user is `raceforge-deploy`. Target: < 60 s from command to running (PLAN §9).
- **`raceforge deploy BUNDLE --usb STICK`:** it copies the bundle into `STICK/raceforge/bundle/`,
  verifies the copy and removes an old `raceforge/result.json`. When the stick is plugged into the
  car's board, a udev rule starts the installer on it and writes `raceforge/result.json` back to the
  stick. Works with radios off (race preparation). A stick without that folder is ignored.
- **Deploy rights:** `setup-board.sh --deploy-key KEY.pub` creates the user `raceforge-deploy`.
  - It has no password. Its key entry is `restrict` with a forced command, so it can only run
    `sudo -n /opt/raceforge/bin/raceforge-install-bundle --stdin`, the one command its sudo rule
    allows.
  - Deploying team members need no admin account on the car. Anyone with physical access can
    deploy by USB (plan: any bundle, any car, any time).

## Python bridge (RealIO)
- **Implementation note (2026-10-07):** instead of embedding CPython with PyO3, the controller runs in a
  **separate Python process** (`python -m raceforge.car.host`) connected to the Rust core over a Unix domain
  socket (one JSON message per tick: Observation → Command). Reasons: the Rust watchdog can stop the motors
  *and* kill/restart a hung Python process (stronger isolation than an embedded interpreter), and the Rust
  binary stays pure Rust, so cross-compiling for aarch64 needs no Python headers. Contracts are unchanged.
- The host loads the controller exactly like `raceforge sim` does (`raceforge.control.load_controller`).
  `RealIO` implements the `RobotIO` protocol of spec 0004, so the same controller file and params YAML run
  unchanged.
- Observation mapping: ultrasonic names from the bundle's port map (`front`/`left`/`right`), LiDAR scan from
  rf-lidar (angles CCW from forward after mounting calibration), heading from EV3 gyro, speed from tachos.

## Non-functional targets
- Control loop jitter p99 < 2 ms at 50 Hz on a Raspberry Pi 5 with LiDAR + 3 ultrasonic + gyro.
- Watchdog stop latency < 50 ms from deadline miss to motor stop command at the EV3.
- EV3 link: < 10 ms one-way latency, < 0.1 % frame loss.
- Runtime start to "ready" < 30 s after boot (no hard requirement per plan; tracked).

## Acceptance criteria (→ tests)
- [x] AC1: Protocol encode/decode round-trips; property tests; CRC rejects corrupted frames; `cargo fuzz`
      targets for EV3 and LD06 parsers run in CI (short) without crashes.
      → `rf-proto` proptests (`ev3.rs`, `ld06.rs`, `ipc.rs`) and CRC tests; CI `fuzz` job (`car_runtime/fuzz/`, ADR-0025).
- [x] AC2: LD06 driver decodes recorded sample packets into correct angles/ranges.
      → `rf-proto` `ld06::decodes_sample_packet` (vendor sample), `rf-lidar/tests/stream.rs`.
- [x] AC3: With a mock EV3 and mock LiDAR (in-process), the loop runs at 50 Hz, builds Observations, calls a
      Python template controller through the controller host process and sends commands; jitter stats
      reported.
      → Covered by several tests, not one test with every part together: `rf-core/tests/mock_loop.rs` `ac3_loop_runs_at_50hz_and_forwards_commands` (LiDAR in the observation: `lidar_scan_time_is_converted_to_the_runtime_clock`); `rf-core/tests/python_host.rs` `ac3_wall_follow_template_through_python_host`; `rf-ev3/tests/loop_with_ev3.rs` (mock EV3 over UDP); `tests/car/test_host.py` (LiDAR mapping in the host).
- [x] AC4: Watchdog: a controller that sleeps 100 ms or raises → stop command within 50 ms; fault logged.
      → `mock_loop.rs` `ac4_hanging_controller_stops_within_50ms`, `ac4_raising_controller_stops_and_logs`; `python_host.rs` `ac4_sleeping_python_controller_is_stopped`, `ac4_raising_python_controller_is_stopped`.
- [x] AC5: Race mode refuses to arm while a (mocked) wireless interface is up or a radio USB id is present;
      in race mode the WebSocket server is not listening and teleop is refused.
      → `rf-runtime/tests/radio_check.rs`; `run_bundle.rs` `race_mode_refuses_to_arm_with_wifi_up_before_touching_the_ev3`, `race_mode_never_listens_for_telemetry`; `rf-telemetry` refuses to start in race mode.
- [x] AC6: Speed limit and dead-man are enforced by Rust regardless of controller output.
      → `mock_loop.rs` `ac6_speed_limit_enforced_regardless_of_controller`, `ac6_teleop_dead_man_stops_when_not_refreshed`.
- [x] AC7: MCAP written by the runtime opens with the Python reader from spec 0003 (`read_frames`).
      → `rf-log/tests/runtime_log.rs` `ac7_runtime_log_readable_by_python`, `ac7_edge_case_values_stay_valid_for_the_python_model`.
- [x] AC8: `ev3_side` unit tests (protocol, failsafe timer) run on the dev machine with mocked ev3dev.
      → `tests/ev3_side/` (protocol, failsafe, bridge, Python 3.5 syntax).
- [x] AC9: Cross-compiled aarch64 build in CI; `clippy -D warnings`, `cargo deny`, `cargo test` green.
      → CI `rust` (fmt, clippy, tests, cargo-deny, aarch64 musl build) and `rust-arm64` (tests on arm64); ADR-0026, ADR-0027.
- [ ] AC10 (hardware, manual checklist: `car_runtime/FIRST_DRIVE.md`): on the real car — link up, sensors read, template `wall_follow`
      drives 10 m in a corridor in test mode, emergency stop cuts motors, MCAP recorded and replayable.
- [x] AC11: The installer refuses a bundle with a wrong hash, an unsafe tar member or a missing file
      without touching the running bundle; a good bundle is swapped in atomically, two previous ones are
      kept, and a bundle the runtime rejects (exit 4) is rolled back automatically.
      → `tests/car/test_install.py`.
- [x] AC12: `raceforge deploy --ssh` round trip (tests: fake `ssh` running the real installer) installs the
      bundle and checks the reported digest; `--usb` writes a verified copy that the installer accepts.
      → `tests/car/test_deploy.py`.
- [x] AC13: `setup-board.sh --deploy-key` creates the deploy user whose key and sudo rule allow only the
      installer, plus the USB auto-install rule; the bundle directory becomes the symlink layout.
      → `tests/car/test_setup_board.py`.

## Open questions
- Exact board (Pi 5 vs Orange Pi 5) is decided mid-January; v1 targets generic aarch64 Linux.
- DC-motor driver hardware (if an external drive motor is chosen) — v1 supports EV3 motors only; a PWM driver
  backend follows when the car team picks a motor driver.
