# First drive on the real car: AC10 checklist (spec 0005)

The manual acceptance test for spec 0005 AC10. On the real car:
- the EV3 link comes up and the sensors read;
- the template `wall_follow` drives 10 m in a corridor in test mode;
- the emergency stop cuts the motors;
- the MCAP log is recorded and replays.

Work through it top to bottom. The steps marked **record** produce the numbers ADR-0016 and the
spec's targets need. Paste the results table at the end into the PR or issue that ticks AC10.

**Safety, always:**
- Steps 1 to 4 run with the wheels off the ground.
- Every drive has one person whose only job is the emergency stop.
- The first drives use a test speed limit of 0.3–0.5 m/s.

## 0. What you need
- **Board:** Raspberry Pi 5 (or another aarch64 board) with **Raspberry Pi OS Trixie Lite 64-bit**,
  or Armbian on Debian 13 / Ubuntu 24.04. raceforge needs Python ≥ 3.12, and Bookworm images are
  refused.
- **EV3:** ev3dev, connected to the board by USB cable. Add a touch sensor as the emergency stop, on
  the port that `estop_touch_port` names.
- **Laptop:** raceforge installed (`uv sync`), with SSH access to the board's admin account.
- **Corridor:** at least 12 m long, a tape measure, and a stand for the car.

## 1. Board
1. Get the runtime binary.
   - Either the CI artifact `rf-runtime-aarch64` from a green run,
   - or build it locally: `cargo build --release --target aarch64-unknown-linux-musl -p rf-runtime`.
2. Build the Python package on the laptop with `uv build`, which creates `dist/raceforge-*.whl`.
   Copy it, the binary and `car_runtime/deploy/` to the board.
3. On the board:
   ```sh
   sudo deploy/setup-board.sh --binary rf-runtime --python-pkg raceforge-*.whl --deploy-key team.pub
   sudo reboot
   ```
4. Check after the reboot:
   - [ ] `grep -o 'isolcpus=[^ ]*' /proc/cmdline` shows `isolcpus=2,3`;
   - [ ] `swapon --show` prints nothing;
   - [ ] `cat /sys/devices/system/cpu/cpu2/cpufreq/scaling_governor` shows `performance`;
   - [ ] `ls -l /opt/raceforge/bin` shows `rf-runtime`, `raceforge-install-bundle` and
     `raceforge-usb-deploy`.

## 2. EV3
1. Install and start the EV3 program (`ev3_side/README.md`).
   - The ports in `~/ev3.json` must match the `ev3:` section of `car.yaml`.
   - Start it with the steering centred: position 0 is taken at start-up.
2. [ ] The LCD shows the program waiting for the board. Without frames it shows `LINK LOST`.

## 3. Car config and first bundle
1. Copy `controllers/car.example.yaml` to `car.yaml`.
   - Replace every PLACEHOLDER with values measured on the car.
   - `drive_counts_per_m`: roll the car exactly 1 m by hand and read the drive motor's tacho
     degrees.
   - `steer_motor_deg_per_rad`: motor degrees from centre to full lock, divided by the lock
     angle in radians.
   - Set `runtime.test_speed_limit_m_s: 0.3`.
2. Build and deploy:
   ```sh
   raceforge bundle controllers/templates/wall_follow.py --car car.yaml --out bundle
   raceforge deploy bundle --ssh <board>
   ```
   - [ ] The deploy prints `installed wall_follow (…)`, ending in `runtime running` or `runtime
     waiting for the EV3/LiDAR link`.
   - [ ] **record** the time from command to `installed` (target: < 60 s).

## 4. On the stand (wheels free)
1. Link and sensors: `journalctl -u rf-runtime -f` on the board (runtime events such as faults
   and resume appear there as `event t=… <kind>: <detail>` lines).
   - [ ] The runtime starts with no fault. The EV3 LCD leaves `LINK LOST`.
   - [ ] Hold a hand in front of each ultrasonic sensor and turn the car slightly by hand, then
     stop the runtime (`sudo systemctl stop rf-runtime`). In the log (step 6), `meas` shows the
     distances and gyro changing.
2. Directions (positive steering = left):
   - [ ] A hand close to the **left** sensor makes the wheels steer **right**, away from the
     wall. If they steer the other way, flip the sign of `steer_motor_deg_per_rad`.
   - [ ] The drive wheels turn **forward**. If not, flip the sign of `drive_counts_per_m`.
   - After any change: rebuild, redeploy, check again.
3. Safety (each check ends with the motors stopped):
   - [ ] **Emergency stop:** press the e-stop touch sensor. The motors stop at once, and the
     journal shows `event … fault: EStop`.
   - [ ] **Resume:** release the e-stop, then hold the EV3 centre button for 1 s. The journal
     shows `resuming`, then `resumed`, and the wheels turn again.
   - [ ] **Watchdog:** `sudo pkill -f raceforge.car.host` kills the controller. The motors stop,
     and the journal shows `fault: LinkClosed …`. A resume brings the controller back.
   - [ ] **Link loss:** unplug the USB cable. The EV3 brakes within 150 ms and shows `LINK LOST`.

## 5. Corridor drive (test mode)
1. Raise the speed limit to 0.5 m/s in `car.yaml`, rebuild and redeploy. Place the car at the
   start of a straight corridor section, with one person at the e-stop.
2. Start the runtime (`sudo systemctl restart rf-runtime`) and let it drive.
   - [ ] `wall_follow` drives at least 10 m without touching a wall. **record** the distance and
     any incidents.
   - [ ] The e-stop during driving stops the car. **record** the stopping distance at 0.5 m/s.

## 6. Logs and replay
1. Copy the newest log, using the admin account (the deploy user cannot read logs):
   `ssh <admin>@<board> 'sudo cat /var/lib/raceforge/logs/$(sudo ls -t /var/lib/raceforge/logs | head -1)' > run.mcap`
2. [ ] Replay: `raceforge ui`, then **Replay** and open `run.mcap`. It shows commands, speed and
   states over time, but no ground-truth path, which only simulator runs have.
3. [ ] **record** loop timing and EV3 link quality. The runtime's own summary is not printed when
   systemd stops it, so read the numbers from the log:
   ```python
   # uv run python stats.py run.mcap
   import json, statistics, sys
   from pathlib import Path
   from mcap.reader import make_reader
   from raceforge.sim.record import read_frames

   log = Path(sys.argv[1])
   frames = read_frames(log)
   jit = sorted(f.loop.jitter_ms for f in frames)
   p99 = jit[min(len(jit) - 1, int(0.99 * len(jit)))]
   dur = (frames[-1].t.mono_ns - frames[0].t.mono_ns) / 1e9
   print(
       f"{len(frames)} ticks in {dur:.1f} s, jitter p99 {p99:.2f} ms (target < 2 ms), "
       f"deadline misses {frames[-1].loop.deadline_misses}, "
       f"faults {sorted({x for f in frames for x in f.faults})}"
   )
   sent, rtt, seqs, bad = {}, [], [], 0
   with log.open("rb") as fh:
       for _, _, m in make_reader(fh).iter_messages(topics=["/ev3_raw"]):
           r = json.loads(m.data)
           if r["dir"] == "tx":
               sent[r["seq"]] = m.log_time
           elif r["dir"] == "rx":
               seqs.append(r["seq"])
               if r["ack_seq"] in sent:
                   rtt.append((m.log_time - sent[r["ack_seq"]]) / 1e6)
           else:
               bad += 1
   lost = sum(b - a - 1 for a, b in zip(seqs, seqs[1:]) if b > a)
   print(
       f"EV3 link: {len(seqs)} frames in, loss {100 * lost / max(1, len(seqs) + lost):.2f} % "
       f"(target < 0.1 %), {bad} bad, round trip median {statistics.median(rtt):.1f} ms "
       f"(one way ~ half; target < 10 ms)"
   )
   ```

## 7. Results (paste into the PR or issue)
| Item | Result |
|---|---|
| Date, board, OS image, raceforge commit | |
| Deploy time (step 3) | s |
| Directions correct after sign fixes (step 4.2) | yes / changes made |
| E-stop, resume, watchdog, link loss (step 4.3) | pass / fail each |
| Corridor drive: distance, incidents (step 5) | m |
| Stopping distance at 0.5 m/s | cm |
| Jitter p99, deadline misses (step 6) | ms, n |
| EV3 loss, round trip median (step 6) | %, ms |

AC10 is done when every box is ticked and the numbers are recorded. Jitter p99 ≥ 2 ms or deadline
misses mean the ADR-0016 settings need another look, not the deadline.
