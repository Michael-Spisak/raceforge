//! `rf-runtime` end to end: bundle built by `raceforge.car.bundle` (Python) -> runtime -> mock EV3
//! over UDP + real controller host -> MCAP readable by `read_frames`. Needs `RF_PYTHON`; the
//! refusal tests run without it.

#![allow(clippy::expect_used)] // test helpers: a failed setup should abort the test

use rf_proto::ev3::{CommandFrame, SensorFrame};
use rf_runtime::{run, AppError, Options};
use std::net::{SocketAddr, UdpSocket};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// Timing-sensitive tests in this file run one at a time: parallel control loops on a small CI
/// runner would otherwise measure each other instead of the code under test.
static SERIAL: std::sync::Mutex<()> = std::sync::Mutex::new(());

fn serial() -> std::sync::MutexGuard<'static, ()> {
    SERIAL
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn tmp(name: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("rf-runtime-test-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).expect("tmp");
    d
}

/// Mock EV3: a receiver thread records commands and the board's address; a separate sender
/// thread sends 100 Hz sensor frames with a corridor (a busy receiver cannot starve them).
fn mock_ev3(done: Arc<AtomicBool>) -> (String, Arc<Mutex<Vec<CommandFrame>>>) {
    let sock = UdpSocket::bind("127.0.0.1:0").expect("bind");
    let addr = sock.local_addr().expect("addr").to_string();
    sock.set_read_timeout(Some(Duration::from_millis(5)))
        .expect("timeout");
    let tx = sock.try_clone().expect("clone");
    let cmds = Arc::new(Mutex::new(Vec::new()));
    let peer: Arc<Mutex<Option<(SocketAddr, u32)>>> = Arc::new(Mutex::new(None));
    {
        let (rec, peer, done) = (cmds.clone(), peer.clone(), done.clone());
        std::thread::spawn(move || {
            let mut buf = [0u8; 256];
            while !done.load(Ordering::Acquire) {
                if let Ok((n, from)) = sock.recv_from(&mut buf) {
                    if let Ok(c) = CommandFrame::decode(&buf[..n]) {
                        *peer.lock().expect("lock") = Some((from, c.seq));
                        rec.lock().expect("lock").push(c);
                    }
                }
            }
        });
    }
    std::thread::spawn(move || {
        let mut seq = 0u32;
        while !done.load(Ordering::Acquire) {
            let target = *peer.lock().expect("lock");
            if let Some((p, ack)) = target {
                seq += 1;
                let f = SensorFrame {
                    seq,
                    ack_seq: ack,
                    ultrasonic_mm: [2000, 400, 800, 0xFFFF],
                    battery_mv: 7900,
                    ..Default::default()
                };
                let _ = tx.send_to(&f.encode(), p);
            }
            std::thread::sleep(Duration::from_millis(10));
        }
    });
    (addr, cmds)
}

fn build_bundle(py: &str, dir: &Path, ev3_addr: &str, mode: &str) {
    let script = format!(
        "from pathlib import Path\n\
         from raceforge.car.bundle import *\n\
         build_bundle(Path(r'{dir}'), Path(r'{ctrl}'),\n\
           RobotSpec(car_name='car', sensors=['front','left','right','gyro'], max_steer_rad=0.45,\n\
                     max_speed_m_s=1.5, wheelbase_m=0.2, track_m=0.15, control_rate_hz=50),\n\
           Ev3Spec(addr='{ev3_addr}', local='127.0.0.1:0', steer_motor_deg_per_rad=100.0,\n\
                   drive_counts_per_m=1000.0, ultrasonic={{'front':'1','left':'2','right':'3'}}),\n\
           RuntimeSpec(mode='{mode}', test_speed_limit_m_s=0.25))\n",
        dir = dir.display(),
        ctrl = repo()
            .join("controllers/templates/wall_follow.py")
            .display(),
    );
    let out = std::process::Command::new(py)
        .env("PYTHONPATH", repo().join("src"))
        .args(["-c", &script])
        .output()
        .expect("python");
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
}

fn opts(bundle: &Path, py: &str, log_dir: &Path) -> Options {
    let mut o = Options::new(bundle.to_path_buf());
    o.python = py.to_string();
    o.pythonpath = Some(repo().join("src"));
    o.log_dir = log_dir.to_path_buf();
    o.ev3_wait = Duration::from_secs(2);
    o.max_ticks = Some(50);
    o
}

#[test]
fn runs_python_built_bundle_end_to_end() {
    let _serial = serial();
    let Ok(py) = std::env::var("RF_PYTHON") else {
        eprintln!("RF_PYTHON not set: skipping");
        return;
    };
    let done = Arc::new(AtomicBool::new(false));
    let (ev3_addr, cmds) = mock_ev3(done.clone());
    let dir = tmp("e2e");
    build_bundle(&py, &dir.join("bundle"), &ev3_addr, "test");

    let out = run(
        &opts(&dir.join("bundle"), &py, &dir.join("logs")),
        &AtomicBool::new(false),
    )
    .expect("run");
    done.store(true, Ordering::Release);
    assert_eq!(out.report.ticks, 50);
    assert!(out.report.fault.is_none(), "{:?}", out.report.fault);
    assert_eq!(out.manifest.name, "wall_follow");

    // The template drives; the bundle's test speed limit (0.25 m/s -> 250 cps) is enforced.
    let cmds = cmds.lock().expect("lock").clone();
    let driving: Vec<_> = cmds.iter().filter(|c| c.drive_speed_cps > 0).collect();
    assert!(!driving.is_empty());
    assert!(
        driving.iter().all(|c| c.drive_speed_cps <= 250),
        "speed limit"
    );
    // Left wall closer than right: steering right (negative target).
    assert!(driving.iter().any(|c| c.steer_target_cdeg < 0));

    let check = std::process::Command::new(&py)
        .env("PYTHONPATH", repo().join("src"))
        .args([
            "-c",
            "import sys; from pathlib import Path; from raceforge.sim.record import read_frames; \
                      print(len(read_frames(Path(sys.argv[1]))))",
        ])
        .arg(&out.log)
        .output()
        .expect("python");
    assert!(
        check.status.success(),
        "{}",
        String::from_utf8_lossy(&check.stderr)
    );
    assert_eq!(String::from_utf8_lossy(&check.stdout).trim(), "50");
}

fn hand_bundle(name: &str, mode: &str, ev3_addr: &str) -> PathBuf {
    hand_bundle_with(name, mode, ev3_addr, "null")
}

fn hand_bundle_with(name: &str, mode: &str, ev3_addr: &str, lidar: &str) -> PathBuf {
    let dir = tmp(name);
    let code = b"# controller\n";
    std::fs::write(dir.join("controller.py"), code).expect("write");
    let hash = rf_runtime::sha256::sha256_hex(code);
    let manifest = format!(
        r#"{{"schema":"car_bundle","schema_version":1,"name":"x","created_wall_ns":1,"raceforge_version":"0.0.1",
  "controller":{{"file":"controller.py","sha256":"{hash}"}},"params":null,
  "robot":{{"car_name":"car","sensors":[],"max_steer_rad":0.4,"max_speed_m_s":1.0,"wheelbase_m":0.2,"track_m":0.15,"control_rate_hz":50.0}},
  "ev3":{{"addr":"{ev3_addr}","local":"127.0.0.1:0","steer_motor":"A","drive_motor":"B","steer_motor_deg_per_rad":100.0,
         "drive_counts_per_m":1000.0,"ultrasonic":{{}},"gyro":true,"estop_touch_port":null,"link_timeout_ms":100}},
  "lidar":{lidar},
  "runtime":{{"mode":"{mode}","deadline_ms":15.0,"test_speed_limit_m_s":null}}}}"#
    );
    std::fs::write(dir.join("bundle.json"), manifest).expect("manifest");
    dir
}

/// Fake sysfs: wired Ethernet only (race ready), or additionally a Wi-Fi interface that is up.
fn fake_sys(name: &str, wifi_up: bool) -> PathBuf {
    let r = tmp(&format!("sys-{name}"));
    let eth = r.join("sys/class/net/eth0");
    std::fs::create_dir_all(&eth).expect("eth0");
    std::fs::write(eth.join("flags"), "0x1003\n").expect("flags");
    if wifi_up {
        let wlan = r.join("sys/class/net/wlan0");
        std::fs::create_dir_all(wlan.join("wireless")).expect("wlan0");
        std::fs::write(wlan.join("flags"), "0x1003\n").expect("flags");
    }
    r
}

#[test]
fn race_mode_refuses_to_arm_with_wifi_up_before_touching_the_ev3() {
    let _serial = serial();
    let silent = UdpSocket::bind("127.0.0.1:0").expect("bind");
    silent
        .set_read_timeout(Some(Duration::from_millis(300)))
        .expect("timeout");
    let dir = hand_bundle(
        "race-wifi",
        "race",
        &silent.local_addr().expect("addr").to_string(),
    );
    let mut o = opts(&dir, "python3", &dir.join("logs"));
    o.sys_root = fake_sys("wifi", true);
    let err = run(&o, &AtomicBool::new(false)).err().expect("refused");
    assert!(
        matches!(&err, AppError::RadiosActive(v) if v == &vec!["Wi-Fi interface wlan0 is up".to_string()]),
        "{err}"
    );
    let mut buf = [0u8; 64];
    assert!(
        silent.recv(&mut buf).is_err(),
        "no EV3 frame may be sent when not armed"
    );
}

#[test]
fn race_mode_arms_when_radios_are_off() {
    let _serial = serial();
    // Check passes, start-up continues to the EV3 link (which is silent here).
    let silent = UdpSocket::bind("127.0.0.1:0").expect("bind");
    let dir = hand_bundle(
        "race-ok",
        "race",
        &silent.local_addr().expect("addr").to_string(),
    );
    let mut o = opts(&dir, "python3", &dir.join("logs"));
    o.sys_root = fake_sys("clean", false);
    o.ev3_wait = Duration::from_millis(200);
    assert!(matches!(
        run(&o, &AtomicBool::new(false)),
        Err(AppError::Ev3NotConnected(_))
    ));
}

#[test]
fn race_bundle_drives_in_race_mode_end_to_end() {
    let _serial = serial();
    let Ok(py) = std::env::var("RF_PYTHON") else {
        eprintln!("RF_PYTHON not set: skipping");
        return;
    };
    let done = Arc::new(AtomicBool::new(false));
    let (ev3_addr, cmds) = mock_ev3(done.clone());
    let dir = tmp("race-e2e");
    build_bundle(&py, &dir.join("bundle"), &ev3_addr, "race");
    let mut o = opts(&dir.join("bundle"), &py, &dir.join("logs"));
    o.sys_root = fake_sys("race-e2e", false);
    let out = run(&o, &AtomicBool::new(false)).expect("run");
    done.store(true, Ordering::Release);
    assert!(out.report.fault.is_none(), "{:?}", out.report.fault);
    // The bundle's test speed limit (0.25 m/s = 250 cps) does not apply in race mode:
    // wall_follow cruises at 0.3 m/s = 300 cps.
    let cmds = cmds.lock().expect("lock").clone();
    assert!(
        cmds.iter().any(|c| c.drive_speed_cps == 300),
        "race mode keeps the test limit"
    );
    let check = std::process::Command::new(&py)
        .env("PYTHONPATH", repo().join("src"))
        .args([
            "-c",
            "import sys; from pathlib import Path; from raceforge.sim.record import read_frames; \
             print({f.mode.value for f in read_frames(Path(sys.argv[1]))})",
        ])
        .arg(&out.log)
        .output()
        .expect("python");
    assert_eq!(String::from_utf8_lossy(&check.stdout).trim(), "{'race'}");
}

#[test]
fn missing_ev3_is_an_error_and_sends_stop_frames() {
    let _serial = serial();
    // An EV3 that receives but never answers.
    let silent = UdpSocket::bind("127.0.0.1:0").expect("bind");
    silent
        .set_read_timeout(Some(Duration::from_millis(500)))
        .expect("timeout");
    let dir = hand_bundle(
        "noev3",
        "test",
        &silent.local_addr().expect("addr").to_string(),
    );
    let mut o = opts(&dir, "python3", &dir.join("logs"));
    o.ev3_wait = Duration::from_millis(300);
    let t = Instant::now();
    assert!(matches!(
        run(&o, &AtomicBool::new(false)),
        Err(AppError::Ev3NotConnected(_))
    ));
    assert!(t.elapsed() < Duration::from_secs(2));
    let mut buf = [0u8; 64];
    let n = silent.recv(&mut buf).expect("the link sent frames");
    let f = CommandFrame::decode(&buf[..n]).expect("command frame");
    assert_eq!(f.drive_speed_cps, 0); // only stop frames before the loop runs
    assert!(
        !dir.join("logs").exists(),
        "no log before the controller is up"
    );
}

#[test]
fn tampered_bundle_is_refused() {
    let dir = hand_bundle("tampered", "test", "127.0.0.1:9");
    std::fs::write(dir.join("controller.py"), b"# changed\n").expect("write");
    let o = opts(&dir, "python3", &dir.join("logs"));
    let err = run(&o, &AtomicBool::new(false)).err().expect("refused");
    assert!(err.to_string().contains("hash mismatch"), "{err}");
}

#[test]
fn missing_lidar_device_is_an_error() {
    let _serial = serial();
    let done = Arc::new(AtomicBool::new(false));
    let (ev3_addr, _) = mock_ev3(done.clone());
    let lidar = r#"{"device":"/nonexistent/ttyLIDAR","mount_offset_rad":0.0,"policy":"critical","timeout_ms":300}"#;
    let dir = hand_bundle_with("nolidar", "test", &ev3_addr, lidar);
    let o = opts(&dir, "python3", &dir.join("logs"));
    let err = run(&o, &AtomicBool::new(false)).err().expect("refused");
    done.store(true, Ordering::Release);
    assert!(
        matches!(err, AppError::Lidar(ref dev, _) if dev == "/nonexistent/ttyLIDAR"),
        "{err}"
    );
    assert!(!dir.join("logs").exists());
}
