# Spec 0011: EV3RT 1.1 bridge — the EV3 brick runs EV3RT instead of ev3dev

- **Status:** implemented except AC4 (CI cross-build needs the EV3RT 1.1 SDK) and AC5 (owner's brick)
- **Owner:** @Michael-Spisak · **Author:** Claude
- **Plan section:** docs/PLAN.md §5 (deploy, car), §10 (onboard computer options)
- **Related:** Spec 0005 (car runtime, EV3 link), ADR-0014 (EV3 link), new ADR-0024 (EV3RT toolchain)
- **Depends on:** Spec 0005

## Purpose
The owner's EV3 brick runs **EV3RT 1.1** (TOPPERS real-time OS, C apps) instead of ev3dev (Linux + Python). EV3RT
boots in seconds, gives hard real-time motor control and needs no Linux on the brick. In **bridge mode** the
EV3 does exactly what the ev3dev bridge does today — drive/steer motors, read sensors, watchdog — and the onboard
board (Raspberry Pi etc.) keeps running the Rust car runtime, the Python controllers, LiDAR, telemetry and teleop.
ev3dev stays selectable. (Spec 0012 adds the standalone mode: controller in C on the EV3, no board.)

## Scope
- In scope:
  1. **EV3RT bridge app** (`ev3rt/apps/raceforge_bridge`, C, EV3RT API only): 100 Hz control task; parses
     `CommandFrame`s and sends `SensorFrame`s (the **unchanged** rf-proto v1 frames, spec 0005); drive speed loop
     (PI on tacho counts → motor power), steering position loop (P on counts → power, saturated); sensors:
     tachos, ultrasonic (EV3RT gives cm → frame mm), gyro rate/angle, touch, buttons, battery mV; **watchdog**:
     no valid command for 150 ms → motors brake + `FAILSAFE` flag; `STOP`/`ESTOP` flags honoured at once;
     status on the brick's LCD and LED.
  2. **Serial link**: frames over a byte stream with **COBS** framing (`0x00` delimiter) + the existing CRC-16.
     Transports: **UART on sensor port 1** (default, 115 200 baud, wired — race legal), USB CDC (EV3RT USB mode
     `CDC`, keeps port 1 free) and Bluetooth SPP (testing only, never in race mode). ev3dev keeps UDP.
  3. **Car runtime** (`rf-ev3`): `SerialTransport` (COBS over a tty, configured with `stty` like the LiDAR);
     bundle `ev3.os = ev3dev | ev3rt`, `ev3.link = udp | uart | usb_cdc | bt_spp`, `ev3.device`, `ev3.baud`.
     Race mode refuses `bt_spp` (radio check, spec 0005).
  4. **Build & upload** (`raceforge car ev3rt …`): `build` generates `rf_config.h` from the bundle (ports, gear
     ratios, gains, link) and runs EV3RT's `make app=raceforge_bridge` in the SDK given by `RF_EV3RT_SDK`;
     `upload` POSTs the app to the EV3RT loader over **Bluetooth PAN** (`http://10.0.10.1/upload`, as EV3RT's
     `make upload`); `config` prints the required `/ev3rt/etc/rc.conf.ini` settings
     (`[Sensors] DisablePort1=1` for UART, `[Debug] DefaultPort=LCD`, `[USB] Mode=CDC` for USB).
  5. **Wiring guide** for the UART cable (EV3 sensor port 1 ↔ board UART, 3.3 V logic, GND; pin 1 carries up
     to 9 V and must stay unconnected) — verified by a person with a multimeter before first use.
- Out of scope: standalone mode (spec 0012), EV3RT firmware installation, Bluetooth teleop from phones (iOS
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
- [ ] AC4 (CI): the bridge app cross-compiles with the EV3RT 1.1 SDK and arm-none-eabi-gcc.
- [ ] AC5 (manual, owner's brick): upload over Bluetooth PAN, UART cable, `rf-runtime` drives the motors, link
      loss brakes, ultrasonic/gyro values appear in telemetry.

## Decisions (owner, 2026-10-08)
- EV3RT is used in **both** roles: bridge (this spec) and standalone (spec 0012).
- Link: **UART cable on sensor port 1**. (USB CDC and BT SPP are supported as alternatives; BT is test-only.)
- ev3dev **stays selectable**.
- Apps are loaded with the **EV3RT loader over Bluetooth** (PAN + HTTP upload).

## Decisions (agent defaults)
- COBS framing (simple, bounded overhead, self-synchronising) instead of SLIP.
- Own PI/P loops on tacho counts (EV3RT's API offers power and blocking/non-blocking rotate, not a speed setpoint
  with feedback we control); gains in the bundle with safe defaults.
