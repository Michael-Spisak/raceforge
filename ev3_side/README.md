# EV3-side program (`raceforge_ev3`)

Runs on the EV3 brick (ev3dev-stretch, **Python 3.5**, python-ev3dev2) and bridges the EV3's
motors and sensors to the board running the Rust car runtime (spec 0005, ADR-0014).

- Board → EV3: steering target + drive speed at 100 Hz (UDP, port 47100, CRC-checked frames).
- EV3 → board: tachos, ultrasonic, gyro, touch, buttons, battery at the same rate.
- **Failsafe:** no valid frame for 150 ms → drive motor brakes, LCD shows `LINK LOST`.
- **Local e-stop** (optional touch sensor): brakes immediately, independent of the board.

## Install on the brick
1. Connect the EV3 to the board with the USB cable (ev3dev's USB-Ethernet gadget).
2. Copy the package: `scp -r ev3_side/raceforge_ev3 robot@ev3dev.local:~/`
3. Optional config (`~/ev3.json`, keys and defaults in `raceforge_ev3/hw.py` `DEFAULT_CONFIG`):
   ```json
   {"steer_motor": "A", "drive_motors": ["B"], "estop_touch_port": "4",
    "sensors": {"1": "ultrasonic", "2": "ultrasonic", "3": "ultrasonic", "4": "touch"}}
   ```
4. Start with the steering centred (position 0 is taken at start-up):
   `python3 -m raceforge_ev3 --config ~/ev3.json`

If the brick cannot keep 100 Hz (sysfs reads are slow), set `"rate_hz": 50`.

## Code rules
Python 3.5 subset only (no f-strings, variable annotations, dataclasses) — enforced by
`tests/ev3_side/test_py35_syntax.py`. Tests run on the dev machine with fakes for ev3dev2.
