//! `rf-runtime` end to end: bundle built by `raceforge.car.bundle` (Python) -> runtime -> mock EV3
//! over UDP + real controller host -> MCAP readable by `read_frames`. Needs `RF_PYTHON`; the
//! refusal tests run without it.

#![allow(clippy::expect_used)] // test helpers: a failed setup should abort the test

use rf_proto::ev3::{CommandFrame, SensorFrame};
use rf_runtime::{run, AppError, Options};
use std::net::{SocketAddr, UdpSocket};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};
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
    mock_ev3_with(done, Arc::new(AtomicU8::new(0)))
}

/// Mock EV3 whose button bits (`SensorFrame.buttons`) the test can change while it runs.
fn mock_ev3_with(
    done: Arc<AtomicBool>,
    buttons: Arc<AtomicU8>,
) -> (String, Arc<Mutex<Vec<CommandFrame>>>) {
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
                    buttons: buttons.load(Ordering::Acquire),
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
    build_bundle_with(py, dir, ev3_addr, mode, "None");
}

/// `telemetry` is a Python expression, e.g. `TelemetrySpec(bind='127.0.0.1:9000')` or `None`.
fn build_bundle_with(py: &str, dir: &Path, ev3_addr: &str, mode: &str, telemetry: &str) {
    let ctrl = repo().join("controllers/templates/wall_follow.py");
    build_bundle_ctrl(py, dir, &ctrl, ev3_addr, mode, telemetry);
}

fn build_bundle_ctrl(
    py: &str,
    dir: &Path,
    ctrl: &Path,
    ev3_addr: &str,
    mode: &str,
    telemetry: &str,
) {
    let script = format!(
        "from pathlib import Path\n\
         from raceforge.car.bundle import *\n\
         build_bundle(Path(r'{dir}'), Path(r'{ctrl}'),\n\
           RobotSpec(car_name='car', sensors=['front','left','right','gyro'], max_steer_rad=0.45,\n\
                     max_speed_m_s=1.5, wheelbase_m=0.2, track_m=0.15, control_rate_hz=50),\n\
           Ev3Spec(addr='{ev3_addr}', local='127.0.0.1:0', steer_motor_deg_per_rad=100.0,\n\
                   drive_counts_per_m=1000.0, ultrasonic={{'front':'1','left':'2','right':'3'}}),\n\
           RuntimeSpec(mode='{mode}', test_speed_limit_m_s=0.25), telemetry={telemetry})\n",
        dir = dir.display(),
        ctrl = ctrl.display(),
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

    // /ev3_raw: every frame on the EV3 link, both directions, on the telemetry clock.
    let bytes = std::fs::read(&out.log).expect("log");
    let (recs, _) = rf_log::mcap::parse(&bytes).expect("mcap");
    let ev3_channel = recs
        .iter()
        .find(|r| r.opcode == 0x04 && r.body.windows(8).any(|w| w == b"/ev3_raw"))
        .map(|r| u16::from_le_bytes([r.body[0], r.body[1]]))
        .expect("/ev3_raw channel");
    let raw: Vec<serde_json::Value> = recs
        .iter()
        .filter_map(rf_log::mcap::message_parts)
        .filter(|(ch, ..)| *ch == ev3_channel)
        .map(|(.., data)| serde_json::from_slice(data).expect("json"))
        .collect();
    let count = |dir: &str| raw.iter().filter(|m| m["dir"] == dir).count();
    // 50 ticks = 1 s: ~100 keep-alives + the new outputs out, ~100 sensor frames in.
    assert!(count("tx") >= 100, "{} tx", count("tx"));
    assert!(count("rx") >= 50, "{} rx", count("rx"));
    assert!(raw
        .iter()
        .any(|m| m["dir"] == "rx" && m["battery_mv"] == 7900));
}

#[test]
fn resume_button_restarts_a_crashed_controller_end_to_end() {
    let _serial = serial();
    let Ok(py) = std::env::var("RF_PYTHON") else {
        eprintln!("RF_PYTHON not set: skipping");
        return;
    };
    let dir = tmp("resume");
    // Crashes once (10th step of the first process), then drives normally after the restart.
    let marker = dir.join("crashed");
    let ctrl = dir.join("crash_once.py");
    std::fs::write(
        &ctrl,
        format!(
            "from pathlib import Path\n\
             from raceforge.control import Command, Controller, ControllerParams, Observation\n\n\
             class CrashOnce(Controller[ControllerParams]):\n    \
                 n = 0\n\n    \
                 def step(self, obs: Observation) -> Command:\n        \
                     self.n += 1\n        \
                     marker = Path(r'{}')\n        \
                     if self.n == 10 and not marker.exists():\n            \
                         marker.touch()\n            \
                         raise RuntimeError('boom')\n        \
                     return Command(steering_rad=0.0, speed_m_s=0.2)\n",
            marker.display()
        ),
    )
    .expect("controller");
    let done = Arc::new(AtomicBool::new(false));
    // The EV3 centre button is held for the whole run: the hold only counts while faulted.
    let buttons = Arc::new(AtomicU8::new(1 << 4));
    let (ev3_addr, cmds) = mock_ev3_with(done.clone(), buttons);
    build_bundle_ctrl(&py, &dir.join("bundle"), &ctrl, &ev3_addr, "test", "None");

    let mut o = opts(&dir.join("bundle"), &py, &dir.join("logs"));
    o.max_ticks = Some(250); // 5 s: crash at 0.2 s, 1 s hold, controller restart
    let out = run(&o, &AtomicBool::new(false)).expect("run");
    done.store(true, Ordering::Release);

    assert!(marker.exists(), "the controller crashed once");
    let kinds: Vec<_> = out.report.events.iter().map(|e| e.kind.as_str()).collect();
    let at = |k: &str| kinds.iter().position(|x| *x == k);
    let (fault, resuming, resumed) = (at("fault"), at("resuming"), at("resumed"));
    assert!(
        fault.is_some() && fault < resuming && resuming < resumed,
        "{kinds:?}"
    );
    assert!(out.report.fault.is_none(), "{:?}", out.report.fault);
    // Stopped while faulted, driving again afterwards.
    let cmds = cmds.lock().expect("lock").clone();
    let drove = cmds
        .iter()
        .position(|c| c.drive_speed_cps > 0)
        .expect("drove");
    let stopped = drove
        + cmds[drove..]
            .iter()
            .position(|c| c.drive_speed_cps == 0)
            .expect("stopped");
    assert!(
        cmds[stopped..].iter().any(|c| c.drive_speed_cps > 0),
        "drives again after the resume"
    );
}

#[test]
fn cli_bundle_from_the_example_car_config_is_accepted() {
    // `raceforge bundle` + controllers/car.example.yaml -> a bundle rf-runtime loads and maps.
    let Ok(py) = std::env::var("RF_PYTHON") else {
        eprintln!("RF_PYTHON not set: skipping");
        return;
    };
    let dir = tmp("cli-bundle");
    let out = dir.join("bundle");
    let status = std::process::Command::new(&py)
        .env("PYTHONPATH", repo().join("src"))
        .args([
            "-c",
            "import sys; from raceforge.cli import main; sys.exit(main(sys.argv[1:]))",
        ])
        .arg("bundle")
        .arg(repo().join("controllers/templates/wall_follow.py"))
        .arg("--car")
        .arg(repo().join("controllers/car.example.yaml"))
        .arg("--out")
        .arg(&out)
        .status()
        .expect("python");
    assert!(status.success());
    let m = rf_runtime::manifest::Manifest::load_verified(&out).expect("bundle accepted");
    let ev3 = m.ev3_config().expect("ev3 mapping");
    assert_eq!(ev3.estop_touch_port, Some(3)); // EV3 port "4"
    assert_eq!(ev3.resume_buttons, 1 << 4); // "enter"
    assert_eq!(ev3.ultrasonic.len(), 3);
    m.ev3_addrs().expect("addresses");
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

fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .expect("port")
        .port()
}

/// Minimal WebSocket client for the telemetry server; retries until the runtime listens.
fn ws_connect(port: u16) -> std::net::TcpStream {
    use std::io::{Read, Write};
    let t = Instant::now();
    let mut s = loop {
        match std::net::TcpStream::connect(("127.0.0.1", port)) {
            Ok(s) => break s,
            Err(_) if t.elapsed() < Duration::from_secs(20) => {
                std::thread::sleep(Duration::from_millis(20))
            }
            Err(e) => panic!("telemetry server not reachable: {e}"),
        }
    };
    s.set_read_timeout(Some(Duration::from_secs(5)))
        .expect("timeout");
    write!(
        s,
        "GET / HTTP/1.1\r\nHost: car\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\
         Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n"
    )
    .expect("request");
    let mut head = Vec::new();
    let mut b = [0u8; 1];
    while !head.ends_with(b"\r\n\r\n") {
        s.read_exact(&mut b).expect("response");
        head.push(b[0]);
    }
    assert!(String::from_utf8_lossy(&head).starts_with("HTTP/1.1 101"));
    s
}

fn ws_recv(s: &mut std::net::TcpStream) -> serde_json::Value {
    let (_, p) = rf_telemetry::ws::read_server_frame(s).expect("frame");
    serde_json::from_slice(&p).expect("json")
}

fn ws_send(s: &mut std::net::TcpStream, msg: &str) {
    use std::io::Write;
    s.write_all(&rf_telemetry::ws::encode_client(
        1,
        msg.as_bytes(),
        [3, 1, 4, 1],
    ))
    .expect("send");
}

#[test]
fn telemetry_and_teleop_through_the_runtime() {
    let _serial = serial();
    let Ok(py) = std::env::var("RF_PYTHON") else {
        eprintln!("RF_PYTHON not set: skipping");
        return;
    };
    let done = Arc::new(AtomicBool::new(false));
    let (ev3_addr, cmds) = mock_ev3(done.clone());
    let dir = tmp("telemetry");
    let port = free_port();
    let tel = format!("TelemetrySpec(bind='127.0.0.1:{port}')");
    build_bundle_with(&py, &dir.join("bundle"), &ev3_addr, "test", &tel);
    let mut o = opts(&dir.join("bundle"), &py, &dir.join("logs"));
    o.max_ticks = Some(150); // 3 s
    let runtime = std::thread::spawn(move || run(&o, &AtomicBool::new(false)));

    let mut c = ws_connect(port);
    assert_eq!(ws_recv(&mut c)["type"], "hello");
    let first = loop {
        let m = ws_recv(&mut c);
        if m["type"] == "telemetry" {
            break m;
        }
    };
    assert_eq!(first["frame"]["schema"], "telemetry");
    assert_eq!(first["frame"]["mode"], "test");
    // Teleop at 0.2 m/s (wall_follow alone would drive 0.25 m/s = the bundle's test limit).
    for _ in 0..15 {
        ws_send(&mut c, r#"{"type":"teleop","steer":0.0,"speed":0.2}"#);
        std::thread::sleep(Duration::from_millis(40));
    }
    ws_send(&mut c, r#"{"type":"note","text":"teleop check"}"#);
    ws_send(&mut c, r#"{"type":"stop","reason":"end of teleop test"}"#);
    let out = runtime.join().expect("thread").expect("run");
    done.store(true, Ordering::Release);

    let cmds = cmds.lock().expect("lock").clone();
    assert!(
        cmds.iter()
            .any(|c| c.drive_speed_cps == 200 && c.steer_target_cdeg == 0),
        "teleop drove"
    );
    assert!(
        cmds.iter().any(|c| c.drive_speed_cps == 250),
        "controller drove before teleop"
    );
    assert_eq!(
        out.report.fault,
        Some(rf_core::runtime::Fault::OperatorStop(
            "end of teleop test".into()
        ))
    );
    assert!(out
        .report
        .events
        .iter()
        .any(|e| e.kind == "note" && e.detail == "teleop check"));
    assert!(
        std::net::TcpStream::connect(("127.0.0.1", port)).is_err(),
        "server closed after run"
    );
}

#[test]
fn race_mode_never_listens_for_telemetry() {
    let _serial = serial();
    let Ok(py) = std::env::var("RF_PYTHON") else {
        eprintln!("RF_PYTHON not set: skipping");
        return;
    };
    let done = Arc::new(AtomicBool::new(false));
    let (ev3_addr, cmds) = mock_ev3(done.clone());
    let dir = tmp("race-telemetry");
    let port = free_port();
    let tel = format!("TelemetrySpec(bind='127.0.0.1:{port}')");
    build_bundle_with(&py, &dir.join("bundle"), &ev3_addr, "race", &tel);
    let mut o = opts(&dir.join("bundle"), &py, &dir.join("logs"));
    o.sys_root = fake_sys("race-telemetry", false);
    o.max_ticks = Some(100);
    let runtime = std::thread::spawn(move || run(&o, &AtomicBool::new(false)));
    // Wait until the car is driving, then probe the port while the race runs.
    let t = Instant::now();
    while !cmds
        .lock()
        .expect("lock")
        .iter()
        .any(|c| c.drive_speed_cps > 0)
    {
        assert!(t.elapsed() < Duration::from_secs(20), "car never drove");
        std::thread::sleep(Duration::from_millis(20));
    }
    assert!(
        std::net::TcpStream::connect(("127.0.0.1", port)).is_err(),
        "telemetry port must not listen in race mode"
    );
    let out = runtime.join().expect("thread").expect("run");
    done.store(true, Ordering::Release);
    assert!(out.report.fault.is_none(), "{:?}", out.report.fault);
}
