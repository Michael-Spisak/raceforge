# Spec 0011: EV3RT 1.1 bridge — the EV3 brick runs EV3RT instead of ev3dev

- **Status:** implemented; AC5 (owner's brick) open. Revised 2026-10-23 against the real EV3RT 1.1 SDK (see below)
- **Owner:** @Michael-Spisak · **Author:** Claude
- **Plan section:** docs/PLAN.md §5 (deploy, car), §10 (onboard computer options)
- **Related:** Spec 0005 (car runtime, EV3 link), ADR-0014 (EV3 link), new ADR-0024 (EV3RT toolchain)
- **Depends on:** Spec 0005

## Purpose
The owner's EV3 brick runs **EV3RT 1.1** (TOPPERS real-time OS, C apps) instead of ev3dev (Linux + Python). EV3RT
boots in seconds, gives hard real-time motor control and needs no Linux on the brick. In **bridge mode** the
EV3 does exactly what the ev3dev bridge does today — drive/steer motors, read sensors, watchdog — and the onboard
board (Raspberry Pi etc.) keeps running the Rust car runtime, the Python controllers, LiDAR, telemetry and teleop.
ev3dev stays selectable. (spec 0035 adds the standalone mode: controller in C on the EV3, no board.)

## Scope
- In scope:
  1. **EV3RT bridge app** (`ev3rt/apps/raceforge_bridge`, C, EV3RT API only): 100 Hz control task; parses
     `CommandFrame`s and sends `SensorFrame`s (the **unchanged** rf-proto v1 frames, spec 0005); drive speed loop
     (PI on tacho counts → motor power), steering position loop (P on counts → power, saturated); sensors:
     tachos, ultrasonic (EV3RT gives cm → frame mm), gyro rate/angle, touch, buttons, battery mV; **watchdog**:
     no valid command for 150 ms → motors brake + `FAILSAFE` flag; `STOP`/`ESTOP` flags honoured at once;
     status on the brick's LCD and LED.
  2. **Serial link**: frames over a byte stream with **COBS** framing (`0x00` delimiter) + the existing CRC-16.
     Transports: **UART on sensor port 1** (115 200 baud, wired, race legal) and Bluetooth SPP (bench tests from a
     laptop, never in race mode). EV3RT 1.1 has **no USB serial port** (USB is SD-card mass storage, and plugging
     it in ends the running app). ev3dev keeps UDP.
  3. **Car runtime** (`rf-ev3`): `SerialTransport` (COBS over a tty, configured with `stty` like the LiDAR);
     bundle `ev3.os = ev3dev | ev3rt`, `ev3.link = udp | uart | bt_spp`, `ev3.device`, `ev3.baud`.
     Race mode refuses `bt_spp` (radio check, spec 0005).
  4. **Build & install** (`raceforge car ev3rt …`): `setup-sdk` unpacks the EV3RT 1.1 release (HRP3) to
     `~/ev3rt` and patches it for current toolchains; `build` (from a car config or bundle) generates
     `rf_config.h` (ports, sensors, gains, link) and runs EV3RT's `make app=raceforge_bridge`; `install` copies the
     app to the brick's SD card over USB (mass storage); `upload` POSTs it to the EV3RT loader over Bluetooth PAN
     (`http://10.0.10.1/upload`, as EV3RT's `make upload`); `config` prints the required
     `/ev3rt/etc/rc.conf.ini` settings (`[Sensors] DisablePort1=1` for UART, `[Debug] DefaultPort=LCD`).
  4b. **Bench check from a laptop** (`raceforge car ev3rt check --port …`): the laptop takes the board's place
     (Python rf-proto + COBS, `raceforge.car.ev3_serial`): link rate, latency, battery, sensors, buttons,
     steering, and with `--drive` a short drive and the link-loss failsafe.
  5. **Wiring guide** for the UART cable (EV3 sensor port 1 ↔ board UART, 3.3 V logic, GND; pin 1 carries up
     to 9 V and must stay unconnected) — verified by a person with a multimeter before first use.
- Out of scope: standalone mode (spec 0035), EV3RT firmware installation, Bluetooth teleop from phones (iOS
  has no SPP).

## Interfaces (contracts)
- rf-proto v1 frames unchanged (golden vectors shared by Rust and C tests).
- Serial framing: `COBS(frame) ‖ 0x00`; a decoder drops anything that is not a valid frame (bad COBS, CRC, magic).
- Bundle additions (additive, defaults keep ev3dev/UDP): `ev3.os`, `ev3.link`, `ev3.device`, `ev3.baud`,
  `ev3.gains {drive_kp, drive_ki, steer_kp, steer_max_power}`.

## Behaviour
- The bridge starts its motors only after the first valid command; link loss → brake within 150 ms; a CRC/COBS
  error is counted and shown on the LCD, never acted upon.
- EV3RT writes emergency log lines to port 1 even when it is used as UART; COBS + CRC resynchronise.
- Ultrasonic resolution is 1 cm under EV3RT (ev3dev: 1 mm); the sim's sensor model gets a `quantize_m` option.

## Non-functional targets
- Command → motor < 15 ms; 100 Hz both ways at 115 200 baud (≈ 6.4 KB/s up, 2.4 KB/s down: < 60 % of the line).

## Acceptance criteria (→ tests)
- [x] AC1 (C, host build with a mock EV3 API): frame encode/decode matches the Rust golden vectors; COBS round
      trip incl. resync after garbage; watchdog brakes after 150 ms; STOP/ESTOP; speed and steer loops converge
      on a simulated motor; ultrasonic cm → mm, "none" → 0xFFFF.
- [x] AC2 (Rust): `SerialTransport` round trip over a socket pair, resync after garbage, timeout; manifest
      parses the new fields; `bt_spp` refused in race mode.
- [x] AC3 (Python): bundle schema, `raceforge car ev3rt config|build|upload` (upload against a fake loader
      HTTP server; build generates `rf_config.h` and calls make with the right arguments).
- [x] AC4: the bridge app builds with the real EV3RT 1.1 SDK (HRP3) and Arm GNU 15.3 (`raceforge car ev3rt
      build`, verified on macOS 2026-10-23; no CI by owner decision 2026-10-08).
- [x] AC6 (laptop side): Python frames match the C/Rust golden bytes; `check --drive` passes end to end against
      the C bridge logic with simulated motors over a pseudo terminal (`ev3rt/tests/host_bridge.c`); the Rust
      runtime + `wall_follow` drives the same host bridge over a serial pty (manual run).
- [ ] AC5 (manual, owner's brick): app on the SD card, `check --drive` over Bluetooth passes; later UART cable +
      `rf-runtime` on the board drives the motors, link loss brakes, ultrasonic/gyro values appear in telemetry.

## Decisions (owner, 2026-10-08)
- EV3RT is used in **both** roles: bridge (this spec) and standalone (spec 0035).
- Link: **UART cable on sensor port 1**. (BT SPP for tests only; USB CDC was planned but EV3RT has none.)
- ev3dev **stays selectable**.
- Apps are loaded with the **EV3RT loader over Bluetooth** (PAN + HTTP upload); copying to the SD card over USB
  works too (added 2026-10-23).

## Revision 2026-10-23 (real SDK, owner decisions)
- EV3RT 1.1 is TOPPERS/**HRP3**: times are microseconds (`tslp_tsk`, `get_tim`, `CRE_CYC`), cyclic handlers are
  `CRE_CYC(…, { TA_NULL, { TNFY_ACTTSK, task }, period_us, 0 })` and started with `sta_cyc`. The app was ported.
- **USB CDC removed**: it does not exist in EV3RT 1.1 (serial ports: UART, BT SPP, SPP master).
- Sending moved to its own task: the 10 ms control task (failsafe, e-stop, motor loops) never blocks on the link,
  so a Bluetooth stall cannot keep the car driving.
- The start-cable touch sensor (`ev3.start_touch_port`, spec 0031) is configured as a touch sensor and reported.
- SDK patch: EV3RT links `cfg1_out.c` into an executable and links that again; binutils >= 2.40 refuse it. The
  rule compiles it to an object instead (`patch_sdk`, idempotent).
- Owner: Bluetooth now for bench tests with the Mac (no board yet), UART cable for the race; standalone mode
  (spec 0035) after the bridge.
- With `link: uart` only sensor ports 2-4 are free (e.g. two ultrasonic + gyro; the LiDAR on the board covers
  the rest).

## Decisions (agent defaults)
- COBS framing (simple, bounded overhead, self-synchronising) instead of SLIP.
- Own PI/P loops on tacho counts (EV3RT's API offers power and blocking/non-blocking rotate, not a speed setpoint
  with feedback we control); gains in the bundle with safe defaults.
