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
| `rf-telemetry` | Live telemetry + teleop over WebSocket (test mode only) |
| `rf-runtime` | The `rf-runtime` binary: runs a deploy bundle |

The EV3 side lives in [`../ev3_side`](../ev3_side/README.md).

## Run a bundle
1. Build a bundle on the dev machine: a controller plus the car config (copy
   [`controllers/car.example.yaml`](../controllers/car.example.yaml) and fill in your car):
   ```sh
   raceforge bundle controllers/templates/wall_follow.py --car car.yaml --out bundle
   ```
   The controller is loaded once (a broken file fails here, not on the car), its parameters
   (`<controller>.yaml` or `--params`) are copied, and every file's hash goes into `bundle.json`.
   `--race` builds a race-mode bundle. A telemetry token comes from `RACEFORGE_TELEMETRY_TOKEN`,
   so it never has to be written into `car.yaml`. From Python, use
   `raceforge.car.bundle.build_bundle`.
2. Copy `bundle/` to the board (or `raceforge deploy`, below), start the EV3 program, then on the board:
   ```sh
   rf-runtime --bundle bundle --log-dir logs
   ```
   The runtime checks every bundle file's SHA-256, waits for the EV3 link, starts the controller host
   and logs to `logs/run-<time>-<name>.mcap`. `--ticks N` stops after N control steps.
   The log has `/telemetry` (one `TelemetryFrame` per tick, same as the simulator), `/events`,
   `/lidar_raw` (one record per revolution, at most 360 points) and `/ev3_raw` (every frame on the
   EV3 link in wire units, `dir` = `tx` / `rx` / `rx_bad`, about 250 records/s, roughly 4 MB per
   minute). Link latency comes from `ack_seq` and frame loss from gaps in `seq`.

After a driving fault the car stays stopped and the runtime keeps running. Holding the EV3 centre
button for 1 s (`Ev3Spec(resume_button=...)`, default `"enter"`) restarts the controller host from the
same bundle; driving resumes once it answers (event `resumed`, or `resume_failed` and the car stays
stopped). The resume is refused while the e-stop is pressed or a link is lost. A new deploy is the
only other way out of a fault.

With a `lidar` section in the bundle (`LidarSpec`: device, mount offset, policy `critical` or
`optional`), the runtime also waits for the first LiDAR scan. If the scan angle stops advancing
(e.g. a stalled scan motor), revolutions are dropped at 2,000 points instead of growing in memory;
no new scan arrives, so the LiDAR goes stale and its policy applies.

On the board, `rf-runtime` runs as the systemd service in [`deploy/rf-runtime.service`](deploy/rf-runtime.service),
installed with `sudo deploy/setup-board.sh --binary rf-runtime --python-pkg <wheel>` (Raspberry Pi OS
Trixie or Armbian on Debian 13 / Ubuntu 24.04: raceforge needs Python 3.12 or newer, so Bookworm-based
images are refused; also turns swap off, sets the `performance` governor and reserves cores 2-3, then asks for a reboot): SCHED_FIFO on isolated cores per ADR-0016, restarted only when
start-up failed (exit 1), never after a driving fault (3), an unusable bundle (4) or a race run that
refused to arm (5).

### Deploying a bundle to the car
```sh
raceforge deploy bundle/ --ssh car-1.local      # now, over Wi-Fi/Tailscale/Ethernet (test mode)
raceforge deploy bundle/ --usb /media/STICK     # USB stick: plug it into the board, it installs itself
raceforge deploy --usb-result /media/STICK      # afterwards: what the car reported
```
Both run the board's installer (`raceforge-install-bundle`, root). It checks every hash before
anything changes, keeps the current and two previous bundles in `/opt/raceforge/bundles/` behind
the `/opt/raceforge/bundle` symlink, restarts the runtime and rolls back automatically if the
runtime refuses the bundle. A runtime still waiting for the EV3 counts as installed.

- **SSH:** `sudo deploy/setup-board.sh --deploy-key team.pub` (one public key per line) creates
  `raceforge-deploy`. Its keys can only run the installer: no shell, no forwarding, one sudo rule.
  Nobody needs an admin account on the car to deploy. `--ssh` uses the system `ssh` and
  `~/.ssh/config`; `user@host` overrides the default user.
- **USB:** FAT/exFAT sticks only. A stick without `raceforge/bundle/` is ignored. Works with all
  radios off. The board logs to `journalctl -t raceforge-usb-deploy`.

Live telemetry and teleop (test mode only): add `telemetry=TelemetrySpec(token=...)` to the bundle and
connect to `ws://<car>:8765/?token=<token>`. Clients get `TelemetryFrame` JSON at 20 Hz (same as the
logs) plus events, and can send `stop`, `teleop {steer, speed}` (dead-man 300 ms, speed limits apply),
`teleop_release` and `note`. A token is required unless the server binds to loopback.

Race mode (`RuntimeSpec(mode="race")`) arms only when no radio can be active: every Wi-Fi/Bluetooth
radio rfkill-blocked, no Wi-Fi interface up, no USB Wi-Fi/Bluetooth dongle (plus `radio_usb_ids` from
the bundle). Otherwise it exits with code 5 before opening the EV3 link. Prepare a race board with
`sudo deploy/setup-board.sh --race` (radios off for good; `--no-race` undoes it). In race mode the test speed
limit is lifted and teleop is refused. Stopping the process (Ctrl-C/kill) is safe:
the EV3 brakes after 150 ms without frames and the log stays readable up to the last record.

## Develop
The Rust version is pinned in `rust-toolchain.toml` (rustup installs it automatically).

```sh
cargo fmt --all --check && cargo clippy --all-targets -- -D warnings
RF_PYTHON=../.venv/bin/python cargo test   # without RF_PYTHON the Python end-to-end tests are skipped
```

Board binary (ADR-0021): a static `aarch64-unknown-linux-musl` build that runs on any aarch64
Linux regardless of its glibc. rustup installs the target with the pinned toolchain and
`.cargo/config.toml` links with `rust-lld`, so no cross compiler is needed:
```sh
cargo build --release --target aarch64-unknown-linux-musl -p rf-runtime
# -> target/aarch64-unknown-linux-musl/release/rf-runtime, for setup-board.sh --binary
```
CI builds the same binary on every run (artifact `rf-runtime-aarch64`, kept 14 days) and runs the
whole test suite natively on an arm64 runner.

Dependency checks (ADR-0020): `cargo install --locked cargo-deny@0.20.2`, then from `car_runtime/`
`cargo deny check` and `cargo deny --manifest-path fuzz/Cargo.toml check` (rules in `deny.toml`:
licence allow-list, RustSec advisories, no wildcard or git dependencies).

Fuzzing (ADR-0019, Linux or macOS): the targets in `fuzz/` cover the EV3 frame decoders
(`ev3_frames`) and the LD06 stream parser up to the revolution in the car frame (`ld06_stream`).
```sh
rustup toolchain install nightly-2026-09-24 --profile minimal
cargo install --locked cargo-fuzz@0.13.2
cargo +nightly-2026-09-24 fuzz run ev3_frames -- -max_total_time=60
```
CI fuzzes every target for 60 s per run and 30 min in the Sunday scheduled run. A crashing input
lands in `fuzz/artifacts/<target>/`; fix it together with a unit test that reproduces it.
