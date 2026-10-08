//! Spec 0005 AC3/AC4 end-to-end with the real Python controller host (`raceforge.car.host`).
//!
//! Needs a Python with raceforge installed: set `RF_PYTHON` (e.g. `../.venv/bin/python`).
//! Skipped (passes with a note) when it is not set, so plain `cargo test` works without Python.

#![allow(clippy::expect_used)] // test helpers: a failed setup should abort the test

use rf_core::hw::{MockSensors, RecordingActuators, SensorSnapshot};
use rf_core::link::ControllerLink;
use rf_core::runtime::{Fault, Runtime, RuntimeConfig};
use rf_proto::ipc::{Mode, RobotInfo};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;
use std::time::Duration;

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

fn python() -> Option<String> {
    let p = std::env::var("RF_PYTHON").ok();
    if p.is_none() {
        eprintln!("RF_PYTHON not set: skipping Python host test");
    }
    p
}

fn info() -> RobotInfo {
    RobotInfo {
        car_name: "car".into(),
        sensors: vec!["front".into(), "left".into(), "right".into(), "gyro".into()],
        max_steer_rad: 0.5,
        max_speed_m_s: 2.0,
        wheelbase_m: 0.2,
        track_m: 0.15,
        control_rate_hz: 50.0,
    }
}

fn spawn(python: &str, controller: &Path, name: &str) -> ControllerLink {
    let mut cmd = Command::new(python);
    cmd.env("PYTHONPATH", repo().join("src"))
        .args(["-m", "raceforge.car.host", "--controller"])
        .arg(controller);
    let sock = std::env::temp_dir().join(format!("rf-test-{}-{name}.sock", std::process::id()));
    ControllerLink::spawn(cmd, &sock, Duration::from_secs(30)).expect("host connects")
}

fn corridor() -> SensorSnapshot {
    let mut s = SensorSnapshot::default();
    s.ultrasonic_m.insert("front".into(), Some(2.0));
    s.ultrasonic_m.insert("left".into(), Some(0.4));
    s.ultrasonic_m.insert("right".into(), Some(0.8));
    s.heading_rad = Some(0.0);
    s.yaw_rate_rad_s = Some(0.0);
    s.speed_m_s = Some(0.3);
    s
}

fn run(link: ControllerLink, ticks: u64) -> (rf_core::runtime::RunReport, Arc<RecordingActuators>) {
    let act = Arc::new(RecordingActuators::default());
    let sensors = Arc::new(MockSensors::new(corridor()));
    let mut rt = Runtime::new(
        RuntimeConfig::new(info(), Mode::Test),
        sensors,
        act.clone(),
        link,
        Duration::from_secs(30),
    )
    .expect("controller ready");
    (rt.run(&AtomicBool::new(false), Some(ticks)), act)
}

fn write_controller(name: &str, body: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("rf-ctrl-{}", std::process::id()));
    std::fs::create_dir_all(&dir).expect("tmp dir");
    let path = dir.join(format!("{name}.py"));
    let src = format!(
        "import time\nfrom raceforge.control import Command, Controller, Observation\n\
         class C(Controller):\n    def step(self, obs: Observation) -> Command:\n{body}\n"
    );
    std::fs::write(&path, src).expect("write controller");
    path
}

#[test]
fn ac3_wall_follow_template_through_python_host() {
    let _serial = serial();
    let Some(py) = python() else { return };
    let link = spawn(
        &py,
        &repo().join("controllers/templates/wall_follow.py"),
        "wf",
    );
    let (report, act) = run(link, 50);
    assert!(report.fault.is_none(), "{:?}", report.fault);
    assert_eq!(report.ticks, 50);
    let drives: Vec<_> = act.outputs().into_iter().filter(|(_, o)| !o.stop).collect();
    assert_eq!(drives.len(), 50);
    // Left wall closer than right: the template steers right (negative) and drives forward.
    assert!(drives
        .iter()
        .any(|(_, o)| o.steering_rad < 0.0 && o.speed_m_s > 0.0));
    assert!(report.last_channels.contains_key("state"));
    println!(
        "python host: jitter {:?} step {:?}",
        report.jitter, report.step
    );
}

#[test]
fn ac4_sleeping_python_controller_is_stopped() {
    let _serial = serial();
    let Some(py) = python() else { return };
    let ctrl = write_controller(
        "sleepy",
        "        if obs.t_s > 0.2:\n            time.sleep(0.1)\n        return Command(0.0, 1.0)",
    );
    let (report, act) = run(spawn(&py, &ctrl, "sleepy"), 30);
    assert!(
        matches!(report.fault, Some(Fault::Deadline { .. })),
        "{:?}",
        report.fault
    );
    let outs = act.outputs();
    let last_drive = outs
        .iter()
        .rev()
        .find(|(_, o)| !o.stop)
        .map(|(t, _)| *t)
        .expect("drove");
    let stop = outs
        .iter()
        .find(|(t, o)| *t >= last_drive && o.fault)
        .map(|(t, _)| *t)
        .expect("stop");
    assert!(
        stop - last_drive < Duration::from_millis(70),
        "{:?}",
        stop - last_drive
    );
}

#[test]
fn ac4_raising_python_controller_is_stopped() {
    let _serial = serial();
    let Some(py) = python() else { return };
    let ctrl = write_controller(
        "raising",
        "        if obs.t_s > 0.1:\n            raise ValueError('sensor confusion')\n        return Command(0.0, 1.0)",
    );
    let (report, _) = run(spawn(&py, &ctrl, "raising"), 30);
    let Some(Fault::Controller { detail, .. }) = report.fault else {
        panic!("{:?}", report.fault)
    };
    assert!(detail.contains("sensor confusion"), "{detail}");
}
