# RaceForge on EV3RT 1.1

The EV3 brick can run **EV3RT** instead of ev3dev (spec 0011, ADR-0024). In **bridge mode** the brick runs the
small C app `raceforge_bridge` (motors, sensors, watchdog) and the onboard board (Raspberry Pi etc.) runs the car
runtime, controllers, LiDAR and telemetry as before. Spec 0012 adds a standalone mode (C controller on the EV3).

```
ev3rt/common/                 frames, COBS, bridge logic (plain C99, host-tested)
ev3rt/apps/raceforge_bridge/  the EV3RT app (app.c/app.cfg/Makefile.inc, default rf_config.h)
ev3rt/tests/                  host tests (`make -C ev3rt/tests`) and stub EV3RT headers
```

## 1. Car bundle
In the bundle's `ev3` section: `"os": "ev3rt"`, `"link": "uart"` (or `"usb_cdc"`, or `"bt_spp"` for tests only),
`"gyro_port": "4"`, sensors on ports 2–4 (port 1 is the UART link). Optional `"gains"` for the motor loops.

## 2. Brick settings
`raceforge car ev3rt config --bundle <dir>` prints what `/ev3rt/etc/rc.conf.ini` on the SD card needs, e.g.

```ini
[Debug]
DefaultPort=LCD
[Sensors]
DisablePort1=1
```

## 3. Build and upload
Needs the EV3RT 1.1 SDK (from the EV3RT release, folder `sdk`) and `arm-none-eabi-gcc` on the PATH.

```bash
export RF_EV3RT_SDK=~/ev3rt-1.1-release/hrp2/sdk   # adjust to your unpacked release
uv run raceforge car ev3rt build --bundle path/to/bundle --upload
```

`--upload` sends the app over **Bluetooth PAN** to the EV3RT loader (`http://10.0.10.1/upload`): pair the
computer with the brick, connect to its Bluetooth network, and keep the loader open on the brick. Then start
`raceforge_bridge` from the loader's menu. Hold **BACK** for 1 s to end the app.

## 4. UART cable (sensor port 1 ↔ board)
EV3 sensor port pins: 1 = analog (**up to 9 V — never connect**), 2 = analog/ID, 3 = GND, 4 = 4.3 V supply,
5 = UART TX/digital 0, 6 = UART RX/digital 1. Connect EV3 pin 5 → board RX, EV3 pin 6 → board TX, pin 3 → GND.
Both sides use 3.3 V logic. **Check every wire with a multimeter before the first power-on** — wrong pins can
damage the brick or the board. On a Raspberry Pi use `/dev/serial0` (enable the UART, disable the serial console).

## 5. Test without hardware
`make -C ev3rt/tests` builds and runs the host tests (frames identical to the Rust and Python implementations,
COBS, failsafe, e-stop, motor loops) and syntax-checks the app against stub EV3RT headers.
