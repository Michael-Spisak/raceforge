# RaceForge on EV3RT 1.1

The EV3 brick can run **EV3RT 1.1** (TOPPERS/HRP3, C apps) instead of ev3dev (spec 0011, ADR-0024). In
**bridge mode** the brick runs the small C app `raceforge_bridge` (motors, sensors, 150 ms failsafe). The
car runtime, controllers, LiDAR and telemetry run on the onboard board (Raspberry Pi), or for bench tests on a
laptop. Spec 0035 adds a standalone mode (C controller on the EV3).

```
ev3rt/common/                 frames, COBS, bridge logic (plain C99, host-tested)
ev3rt/apps/raceforge_bridge/  the EV3RT app (app.c/app.cfg/Makefile.inc, default rf_config.h)
ev3rt/tests/                  host tests, stub EV3RT headers, host_bridge.c (bridge with simulated motors)
```

**Links.** EV3RT 1.1 has two serial links and **no USB serial port**: its USB port only shows the SD card, and
plugging it in ends the running app.

| link | where | use |
|---|---|---|
| `bt_spp` (Bluetooth) | brick paired with a laptop or the Pi | bench tests; refused in race mode |
| `uart` (cable on sensor port 1) | brick ↔ Pi UART | the car / race; port 1 is then not a sensor port |

## 0. What you need
- The EV3 with EV3RT 1.1 on a microSD card (≤ 32 GB, FAT32): copy the release's `sdcard/` contents (`uImage`,
  `ev3rt/`) to the card, insert it, switch the brick on. The EV3RT loader menu appears.
- The EV3RT 1.1 release folder (`ev3rt-1.1-release`, from the TOPPERS/EV3RT site), and Python via `uv` as
  for the rest of RaceForge.
- The Arm GNU toolchain `arm-none-eabi` (see 1).
- **For the UART cable later (shopping list):**
  - an EV3 breadboard/breakout adapter (EV3 socket with pins), or a spare EV3 cable you may cut open;
  - female-female jumper wires;
  - a multimeter;
  - for testing the cable on a laptop: a **3.3 V** USB-to-serial adapter (CP2102 or FTDI FT232 set to 3.3 V,
    not 5 V).

## 1. Toolchain and SDK (once per computer)
macOS (no admin rights needed): download the Arm GNU toolchain package and unpack it to
`~/.local/arm-gnu-toolchain`, where RaceForge finds it on its own:

```bash
brew fetch --cask gcc-arm-embedded
```
```bash
pkgutil --expand-full "$(find ~/Library/Caches/Homebrew -name '*arm-none-eabi*.pkg' | head -1)" /tmp/armpkg && mv /tmp/armpkg/Payload ~/.local/arm-gnu-toolchain
```

(Or `brew install --cask gcc-arm-embedded` with an admin password. On Linux: the distribution's
`gcc-arm-none-eabi` with newlib, or the Arm download. The tool can also be given with `RF_ARM_TOOLCHAIN`.)

Unpack and prepare the SDK (to `~/ev3rt/hrp3/sdk`; it patches one build rule for current linkers):

```bash
uv run raceforge car ev3rt setup-sdk --release ~/Downloads/ev3rt-1.1-release
```

## 2. Car config
Copy `controllers/car.ev3rt.example.yaml` to `car.yaml` and set the ports. The relevant `ev3` keys are
`os: ev3rt`, `link: bt_spp` (bench) or `uart` (car), `gyro_port`, the sensor ports, and optional `gains`.
`raceforge car ev3rt config --car car.yaml` prints the generated `rf_config.h` and the settings the brick's
`/ev3rt/etc/rc.conf.ini` needs (e.g. `DisablePort1=1` for the UART link).

## 3. Build and put the app on the brick
```bash
uv run raceforge car ev3rt build --car car.yaml
```
This builds `~/ev3rt/hrp3/sdk/workspace/app`. To install it:
- **USB (simplest):** connect the brick by USB while the EV3RT loader runs. The SD card appears as a drive.
  Run `uv run raceforge car ev3rt install ~/ev3rt/hrp3/sdk/workspace/app --volume /Volumes/<drive>`,
  or `build … --volume /Volumes/<drive>`. Eject the drive, then unplug.
- **Bluetooth PAN:** `build … --upload` sends it to the loader at `10.0.10.1`. The computer must be connected to
  the brick's Bluetooth network.

On the brick: loader menu → *Load App* → SD card → `raceforge_bridge`. The LCD shows `RaceForge EV3RT`, the
link state and counters. Hold **BACK** for 1 s to end the app. The LED is red while there is no link, orange
when stopped, and green when driving.

## 4. Bench test from a laptop over Bluetooth (no Pi needed)
1. With `link: bt_spp` in the car config, build and install as in 3, then start the app.
2. Pair the laptop with the brick (macOS: System Settings → Bluetooth → "Mindstorms EV3", PIN `0000`, both set
   in `rc.conf.ini` `[Bluetooth]`).
3. Find the serial port: `ls /dev/cu.*`, then look for an entry with the brick's name.
4. Run the check. Without `--drive` nothing moves except the steering motor (±30°):

```bash
uv run raceforge car ev3rt check --port /dev/cu.<brick>
```

5. Put the car on a box so the **wheels are off the ground**, then test driving and the failsafe:

```bash
uv run raceforge car ev3rt check --port /dev/cu.<brick> --drive
```

The check reports:
- link rate (about 100 frames/s), latency, battery, ultrasonic, gyro and touch;
- buttons (press ENTER when asked);
- steering ±30° and back;
- driving 360°/s;
- **failsafe**: the laptop stops sending, and the brick must brake on its own within 150 ms.

### Optional: the real car runtime on the laptop
The Rust runtime that later runs on the Pi also runs on macOS and Linux:

```bash
cargo build --release -p rf-runtime --manifest-path car_runtime/Cargo.toml
```

Make a test bundle whose `ev3.device` is the brick's `/dev/cu.*` port (`raceforge bundle
controllers/templates/wall_follow.py --car car.yaml --out bundle`), put the wheels up and run
`car_runtime/target/release/rf-runtime --bundle bundle --pythonpath src`. Test mode only: race bundles refuse
Bluetooth.

## 5. The UART cable (car / race)
EV3 sensor port pins:

| pin | function | connect? |
|---|---|---|
| 1 | analog, **up to 9 V** | **never** |
| 2 | analog / detect | no |
| 3 | GND | to board GND |
| 4 | 4.3 V supply | **no** |
| 5 | UART (TX of the EV3) | to board **RX** |
| 6 | UART (RX of the EV3) | to board **TX** |

- **Levels:** both sides use **3.3 V logic**. Never use a 5 V adapter.
- **Check before power-on:** check every wire with the multimeter (continuity, no short to pin 1 or 4) before
  the first power-on. Wrong pins can damage the brick or the board.
- **No frames?** If no frames arrive, swap 5 and 6; that is harmless at 3.3 V.
- **Raspberry Pi pins:** GPIO14 TXD (pin 8), GPIO15 RXD (pin 10), GND (pin 6). Enable the UART and disable the
  serial console (`raspi-config` → Interface → Serial: login shell *no*, hardware *yes*). The device is
  `/dev/serial0`.
- **Car config and brick:** set `link: uart`, then rebuild and reinstall the app. Check that `rc.conf.ini` has
  `[Sensors] DisablePort1=1`.
- **Test the cable on a laptop first:** with the 3.3 V USB-serial adapter, run
  `raceforge car ev3rt check --port /dev/cu.usbserial-…`.

## 6. Tests without hardware
- `make -C ev3rt/tests` runs:
  - the host tests: frames identical to Rust and Python, COBS, failsafe, e-stop, motor loops;
  - a syntax check of the app against the stub headers;
  - the build of `host_bridge`.
- `tests/car/test_ev3rt_link.py` runs `check --drive` against `host_bridge` over a pseudo terminal.
