# Spec 0010: Teleop v1 — drive the sim and the real car by hand, record demonstrations

- **Status:** implemented (A + B: PR #15, C: TrackScout drive mode) — AC5 needs the real car (owner request 2026-10-08: "mach mit Teleop weiter")
- **Owner:** @Michael-Spisak · **Author:** Claude
- **Plan section:** docs/PLAN.md §5 (real demonstrations), §5d (safety), §7 (live screen), milestone weeks 4–5
- **Related:** Spec 0005 (car runtime: teleop/stop over WebSocket, dead-man 300 ms, speed limit, race mode),
  Spec 0003/0004 (sim, RobotIO), Spec 0008 (frontend shell, sim WebSocket)

## Purpose
The car team starts at the end of October. They need to drive the car (and the simulated car) by hand with an
Xbox controller, the keyboard or a touch joystick — to test the hardware, to explore the corridor and to record
**demonstrations** for imitation learning. The car runtime already accepts teleop in test mode and enforces the
dead-man, speed limit and race-mode block (spec 0005); this spec adds the operator side.

## Scope
- In scope:
  1. **Teleop input** (frontend, one module for sim and car): Gamepad API (Xbox standard mapping), keyboard and an
     on-screen touch joystick, mapped to `{steer (rad), speed (m/s)}` with dead zone, a **session speed limit**
     slider and the car's steering limit. Commands are sent every 50 ms while the operator holds the dead-man
     (gamepad RB, a held drive key, or a finger on the joystick); letting go sends `teleop_release` once.
     A big **STOP** button and the Space key / gamepad B always stop.
  2. **Sim teleop** (delivery A): the `/api/v1/sim` WebSocket accepts `teleop` / `teleop_release` / `stop_car`
     (additive message types). While engaged, the teleop command replaces the controller output (the controller
     is not stepped); the sim runs in real time; the same 300 ms dead-man as the car applies (wall clock):
     expired → the car stops (`state = deadman_stop`) until the next teleop message. Controller choice `none`
     (built-in: stand still) lets you drive without any controller. Recording (`record_path`) stores the
     teleop commands with `state = teleop`, so the run log is a demonstration.
  3. **Real car live link** (delivery B): the engine connects to a car runtime (`ws://<car>:<port>/?token=`) and
     relays its telemetry/events to the UI and teleop/stop/note to the car (`/api/v1/car/live` WebSocket);
     it measures the round-trip time (WebSocket ping) and warns above 100 ms. A minimal **Live** tab shows
     connection, mode, controller state, speed/steer, battery, latency, events, and hosts the teleop panel.
  4. **TrackScout drive mode** (delivery C, owner request 2026-10-08): drive the car from TrackScout.
     - **iPhone** (landscape, full screen): driving only — steering pad (left thumb), throttle pad (right thumb;
       finger down = dead-man), STOP, one status line (link, state, latency).
     - **iPad**: the same controls plus live stats (mode, state, command, measured speed, battery, loop rate,
       latency with the 100 ms warning, faults) and the event list.
     - Xbox controller via Apple's GameController framework (same mapping as the desktop).
     - The phone talks **directly** to the car runtime over Wi-Fi (spec 0005 protocol, `?token=`), no laptop
       needed; the address + token come from a QR code shown in the desktop Live tab ("Pair phone with car")
       or are typed in. The token is stored in the Keychain.
     - Available on devices **without LiDAR** (e.g. iPad Air); only scanning needs LiDAR.
     - Sending stops at once (release) when the app leaves the foreground, the controller disconnects or the
       finger lifts; the runtime's 300 ms dead-man stays the guarantee.
- Out of scope (later): Wi-Fi deploy of bundles, the full Live tab (plots, map, layouts), Bluetooth/ESP-NOW
  transports, telemetry relay through the backend, imitation learning itself.

## Interfaces (contracts — additive)
- Sim WebSocket client → engine: `{"type":"teleop","steer":rad,"speed":m_s}`, `{"type":"teleop_release"}`,
  `{"type":"stop_car"}` (latched stop of the simulated car, like the operator stop on the real car).
- `SimStart.controller = "none"` → no controller (car stands still unless teleoperated).
- Engine ↔ UI car link: `/api/v1/car/live` WebSocket; first message `{"type":"connect","url":..,"token":..}`;
  then the car's own messages (spec 0005) are forwarded unchanged, plus `{"type":"link","rtt_ms":..,"state":..}`;
  UI → car messages are forwarded unchanged (`teleop`, `teleop_release`, `stop`, `note`).

## Behaviour / safety
- The frontend never sends teleop faster than every 50 ms and stops sending as soon as the dead-man is released,
  the window loses focus or the gamepad disconnects; the runtime's 300 ms dead-man is the real guarantee.
- Session speed limit: default = car maximum (owner decision in the plan); the runtime's limit still applies.
- Race mode: the car refuses teleop (spec 0005); the UI shows the refusal and disables the panel.

## Acceptance criteria (→ tests)
- [x] AC1 (Vitest): input mapping — dead zone, steering/speed scaling, speed limit, reverse, dead-man
      engagement for gamepad/keyboard/touch, STOP has priority.
- [x] AC2 (pytest): sim teleop overrides the controller; without new messages for > 300 ms the car stops with
      state `deadman_stop`; `teleop_release` hands back to the controller; recorded run log contains the teleop
      commands with state `teleop`; controller `none` stands still.
- [x] AC3 (Playwright): Simulate with controller "none" → arm teleop → hold `W` → the car moves
      (distance increases) → release → it stops.
- [x] AC4 (pytest, delivery B): car link against a fake car WebSocket server: telemetry forwarded, teleop/stop
      forwarded, RTT reported, connection errors reported, token passed.
- [x] AC6 (Swift tests): drive mapping (touch pads, gamepad, dead zone, limit, STOP priority), the 50 ms send
      loop (teleop while engaged, one release, release on background), car-link message parsing; the Swift car
      client against the Python fake car in CI (connect with token, teleop reaches the car, stop latches).
- [x] AC7: desktop Live tab shows a "pair phone with car" QR code `raceforge://car?v=1&d=<base64url{url,token}>`;
      TrackScout parses it.
- [ ] AC5 (manual, real car): Xbox controller drives the car in test mode; releasing RB stops it within 300 ms;
      the MCAP on the car contains the teleop drive.

## Decisions (agent defaults)
- Gamepad mapping (Xbox): left stick X = steer, RT = forward, LT = reverse, RB = dead-man (hold to drive),
  B = stop. Keyboard: W/S or ↑/↓ = forward/reverse, A/D or ←/→ = steer, Space = stop; a held drive key is
  the dead-man. Touch: drag the joystick; lifting the finger releases.
- Engine relays the car link (front-ends only talk to `raceforge.api`); on the same laptop this adds < 1 ms.
