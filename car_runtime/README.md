# Car runtime (spec 0005)

Rust runtime that drives the real car: fixed-rate control loop, safety, EV3 link, on-car logging.
The team's Python controllers run unchanged in a separate host process (ADR-0014).

| Crate | Role |
|---|---|
| `rf-proto` | Wire protocols: EV3 frames, LD06 LiDAR packets, controller IPC |
| `rf-core` | Control loop, per-step deadline + watchdog, safety limits, controller link |
| `rf-ev3` | EV3 link over UDP (USB-Ethernet gadget) |
| `rf-lidar` | LD06/LD19 LiDAR UART driver |
| `rf-log` | MCAP logging (same `/telemetry` schema as the simulator) |
| `rf-runtime` | The `rf-runtime` binary: runs a deploy bundle |

The EV3 side lives in [`../ev3_side`](../ev3_side/README.md).

## Run a bundle
1. Build a bundle on the dev machine (Python):
   ```python
   from pathlib import Path
   from raceforge.car.bundle import build_bundle, RobotSpec, Ev3Spec

   build_bundle(
       Path("bundle"),
       Path("controllers/templates/wall_follow.py"),
       RobotSpec(
           car_name="car",
           sensors=["front", "left", "right", "gyro"],
           max_steer_rad=0.45,
           max_speed_m_s=1.5,
           wheelbase_m=0.2,
           track_m=0.15,
           control_rate_hz=50,
       ),
       Ev3Spec(
           steer_motor_deg_per_rad=171.9,
           drive_counts_per_m=2046.0,
           ultrasonic={"front": "1", "left": "2", "right": "3"},
       ),
   )
   ```
2. Copy `bundle/` to the board, start the EV3 program, then on the board:
   ```sh
   rf-runtime --bundle bundle --log-dir logs
   ```
   The runtime checks every bundle file's SHA-256, waits for the EV3 link, starts the controller host
   and logs to `logs/run-<time>-<name>.mcap`. `--ticks N` stops after N control steps.

With a `lidar` section in the bundle (`LidarSpec`: device, mount offset, policy `critical` or
`optional`), the runtime also waits for the first LiDAR scan.

On the board, `rf-runtime` runs as the systemd service in [`deploy/rf-runtime.service`](deploy/rf-runtime.service),
installed with `sudo deploy/setup-board.sh --binary rf-runtime --python-pkg <wheel>` (Raspberry Pi OS or
Armbian; also turns swap off, sets the `performance` governor and reserves cores 2-3, then asks for a reboot): SCHED_FIFO on isolated cores per ADR-0016, restarted only when
start-up failed (exit 1), never after a driving fault (3) or an unusable bundle (4).

Race mode is refused until the radio check (AC5) exists. Stopping the process (Ctrl-C/kill) is safe:
the EV3 brakes after 150 ms without frames and the log stays readable up to the last record.

## Develop
The Rust version is pinned in `rust-toolchain.toml` (rustup installs it automatically).

```sh
cargo fmt --all --check && cargo clippy --all-targets -- -D warnings
RF_PYTHON=../.venv/bin/python cargo test   # without RF_PYTHON the Python end-to-end tests are skipped
```
