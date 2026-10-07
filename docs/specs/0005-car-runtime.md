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
- [ ] AC1: Protocol encode/decode round-trips; property tests; CRC rejects corrupted frames; `cargo fuzz`
      targets for EV3 and LD06 parsers run in CI (short) without crashes.
- [ ] AC2: LD06 driver decodes recorded sample packets into correct angles/ranges.
- [ ] AC3: With a mock EV3 and mock LiDAR (in-process), the loop runs at 50 Hz, builds Observations, calls a
      Python template controller through the controller host process and sends commands; jitter stats
      reported.
- [ ] AC4: Watchdog: a controller that sleeps 100 ms or raises → stop command within 50 ms; fault logged.
- [ ] AC5: Race mode refuses to arm while a (mocked) wireless interface is up or a radio USB id is present;
      in race mode the WebSocket server is not listening and teleop is refused.
- [ ] AC6: Speed limit and dead-man are enforced by Rust regardless of controller output.
- [ ] AC7: MCAP written by the runtime opens with the Python reader from spec 0003 (`read_frames`).
- [ ] AC8: `ev3_side` unit tests (protocol, failsafe timer) run on the dev machine with mocked ev3dev.
- [ ] AC9: Cross-compiled aarch64 build in CI; `clippy -D warnings`, `cargo deny`, `cargo test` green.
- [ ] AC10 (hardware, manual checklist): on the real car — link up, sensors read, template `wall_follow`
      drives 10 m in a corridor in test mode, emergency stop cuts motors, MCAP recorded and replayable.

## Open questions
- Exact board (Pi 5 vs Orange Pi 5) is decided mid-January; v1 targets generic aarch64 Linux.
- DC-motor driver hardware (if an external drive motor is chosen) — v1 supports EV3 motors only; a PWM driver
  backend follows when the car team picks a motor driver.
