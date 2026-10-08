//! Spec 0005 AC7: the runtime's MCAP log opens with the simulator's Python reader (`read_frames`).
//! The Python part runs when `RF_PYTHON` is set (CI's rust job sets it).

#![allow(clippy::expect_used)] // test helpers: a failed setup should abort the test

use rf_core::hw::{DriveOutput, MockSensors, RecordingActuators, SensorSnapshot};
use rf_core::link::ControllerLink;
use rf_core::runtime::{Fault, Runtime, RuntimeConfig, TickRecord, TickSink};
use rf_log::mcap::{message_parts, parse};
use rf_log::Logger;
use rf_proto::ipc::{self, ChannelValue, Command, FromHost, Mode, Observation, RobotInfo, ToHost};
use std::collections::BTreeMap;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::sync::atomic::AtomicBool;
use std::sync::Arc;
use std::time::Duration;

fn tmp(name: &str) -> PathBuf {
    std::env::temp_dir().join(format!("rf-log-{}-{name}.mcap", std::process::id()))
}

/// Mock host: drives for `ok_steps` steps with some channels, then raises.
fn mock_host(ok_steps: u64) -> ControllerLink {
    let (core, host) = UnixStream::pair().expect("socketpair");
    std::thread::spawn(move || {
        let mut out = host.try_clone().expect("clone");
        for line in BufReader::new(host).lines() {
            let Ok(line) = line else { return };
            let reply = match ipc::from_line::<ToHost>(&line).expect("msg") {
                ToHost::Hello { .. } => FromHost::Ready,
                ToHost::Shutdown => return,
                ToHost::Obs { seq, .. } if seq < ok_steps => {
                    let mut channels = BTreeMap::new();
                    channels.insert("err.lateral".to_string(), ChannelValue::Float(0.05));
                    channels.insert("state".to_string(), ChannelValue::Text("follow".into()));
                    channels.insert("Bad Key".to_string(), ChannelValue::Int(1)); // dropped
                    let cmd = Command {
                        steering_rad: 0.1,
                        speed_m_s: 0.5,
                    };
                    FromHost::Cmd {
                        seq,
                        cmd,
                        channels,
                        notes: vec![],
                    }
                }
                ToHost::Obs { seq, .. } => FromHost::Error {
                    seq: Some(seq),
                    detail: "boom".into(),
                },
            };
            if out
                .write_all(ipc::to_line(&reply).expect("ser").as_bytes())
                .is_err()
            {
                return;
            }
        }
    });
    ControllerLink::from_stream(core)
}

fn info() -> RobotInfo {
    RobotInfo {
        car_name: "car".into(),
        sensors: vec!["front".into()],
        max_steer_rad: 0.5,
        max_speed_m_s: 2.0,
        wheelbase_m: 0.2,
        track_m: 0.15,
        control_rate_hz: 50.0,
    }
}

fn messages(path: &Path) -> (Vec<(u16, serde_json::Value)>, bool) {
    let bytes = std::fs::read(path).expect("read log");
    let (recs, closed) = parse(&bytes).expect("mcap magic");
    let msgs = recs
        .iter()
        .filter_map(message_parts)
        .map(|(ch, _, _, data)| (ch, serde_json::from_slice(data).expect("json")))
        .collect();
    (msgs, closed)
}

/// Validate every /telemetry message with the Python model; returns the frame count.
fn python_read_frames(path: &Path) -> Option<usize> {
    let py = std::env::var("RF_PYTHON").ok()?;
    let repo = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let out = std::process::Command::new(py)
        .env("PYTHONPATH", repo.join("src"))
        .args(["-c", "import sys; from pathlib import Path; from raceforge.sim.record import read_frames; \
                      f = read_frames(Path(sys.argv[1])); print(len(f), f[-1].mode.value, f[-1].state, len(f[-1].faults))"])
        .arg(path)
        .output()
        .expect("python runs");
    assert!(
        out.status.success(),
        "python failed: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    let text = String::from_utf8_lossy(&out.stdout).to_string();
    println!("python read_frames: {text}");
    text.split_whitespace().next().and_then(|n| n.parse().ok())
}

#[test]
fn ac7_runtime_log_readable_by_python() {
    let path = tmp("runtime");
    let logger = Arc::new(Logger::start(&path, 256, Duration::from_millis(100)).expect("logger"));
    let mut snap = SensorSnapshot::default();
    snap.ultrasonic_m.insert("front".into(), Some(1.2));
    snap.ultrasonic_m.insert("left".into(), None);
    snap.yaw_rate_rad_s = Some(0.05);
    snap.speed_m_s = Some(0.4);
    snap.battery_v = Some(7.9);
    let mut rt = Runtime::new(
        RuntimeConfig::new(info(), Mode::Test),
        Arc::new(MockSensors::new(snap)),
        Arc::new(RecordingActuators::default()),
        mock_host(20),
        Duration::from_secs(1),
    )
    .expect("ready");
    rt.add_sink(logger.clone());
    let report = rt.run(&AtomicBool::new(false), Some(25));
    assert!(matches!(
        report.fault,
        Some(Fault::Controller { seq: 20, .. })
    ));
    let stats = logger.close().expect("close");
    assert_eq!(stats.dropped, 0);

    let (msgs, closed) = messages(&path);
    assert!(closed);
    let tel: Vec<_> = msgs
        .iter()
        .filter(|(ch, _)| *ch == 0)
        .map(|(_, v)| v)
        .collect();
    let ev: Vec<_> = msgs
        .iter()
        .filter(|(ch, _)| *ch == 1)
        .map(|(_, v)| v)
        .collect();
    assert_eq!(tel.len(), 25);
    assert_eq!(tel[0]["state"], "follow");
    assert_eq!(tel[0]["cmd"]["speed_m_s"], 0.5);
    assert_eq!(tel[0]["meas"]["sensors"]["us_front"]["distance_m"], 1.2);
    assert!(tel[0]["meas"]["sensors"]["us_left"]["distance_m"].is_null());
    assert_eq!(tel[0]["channels"]["err.lateral"], 0.05);
    assert!(tel[0]["channels"].get("Bad Key").is_none());
    assert_eq!(tel[24]["state"], "fault");
    assert_eq!(tel[24]["cmd"]["speed_m_s"], 0.0);
    assert!(tel
        .windows(2)
        .all(|w| w[1]["seq"].as_u64() > w[0]["seq"].as_u64()));
    assert!(ev
        .iter()
        .any(|e| e["kind"] == "fault" && e["detail"].as_str().is_some_and(|d| d.contains("boom"))));

    if let Some(n) = python_read_frames(&path) {
        assert_eq!(n, 25);
    }
    let _ = std::fs::remove_file(&path);
}

#[test]
fn ac7_edge_case_values_stay_valid_for_the_python_model() {
    let path = tmp("edge");
    let logger = Logger::start(&path, 64, Duration::from_millis(50)).expect("logger");
    let mut us = BTreeMap::new();
    us.insert("front".to_string(), Some(-1.0)); // impossible -> null
    us.insert("rear".to_string(), Some(f64::NAN));
    us.insert("Side.L".to_string(), Some(1.0)); // invalid sensor id -> dropped
    let mut channels = BTreeMap::new();
    channels.insert("nan".to_string(), ChannelValue::Float(f64::NAN));
    for i in 0..80 {
        channels.insert(format!("c{i:02}"), ChannelValue::Int(i)); // > 64 channels -> capped
    }
    let rec = TickRecord {
        mono_ns: 1_000,
        wall_offset_ns: Some(1_700_000_000_000_000_000),
        seq: 0,
        mode: Mode::Race,
        state: String::new(),
        faults: vec![],
        out: DriveOutput {
            steering_rad: f64::INFINITY,
            speed_m_s: 1.0,
            stop: false,
            fault: false,
        },
        obs: Some(Observation {
            ultrasonic_m: us,
            yaw_rate_rad_s: Some(f64::NAN),
            steering_rad: Some(f64::NEG_INFINITY),
            battery_v: Some(-3.0),
            ..Default::default()
        }),
        rate_hz: 50.0,
        lateness_us: -5.0,
        tick_us: 300.0,
        deadline_misses: 0,
        channels,
    };
    logger.tick(&rec);
    logger.tick(&TickRecord {
        seq: 1,
        obs: None,
        ..rec.clone()
    });
    logger.close().expect("close");
    let (msgs, _) = messages(&path);
    assert_eq!(msgs.len(), 2);
    let f = &msgs[0].1;
    assert!(f["meas"]["sensors"]["us_front"]["distance_m"].is_null());
    assert!(f["meas"]["sensors"].get("us_Side.L").is_none());
    assert!(f["meas"]["sensors"].get("gyro").is_none());
    assert_eq!(f["channels"].as_object().map(|c| c.len()), Some(64));
    assert_eq!(f["cmd"]["steering_rad"], 0.0);
    assert_eq!(f["state"], "run");
    if let Some(n) = python_read_frames(&path) {
        assert_eq!(n, 2);
    }
    let _ = std::fs::remove_file(&path);
}

#[test]
fn full_queue_drops_instead_of_blocking() {
    let path = tmp("drop");
    let logger = Logger::start(&path, 1, Duration::from_millis(50)).expect("logger");
    let rec = TickRecord {
        mono_ns: 0,
        wall_offset_ns: None,
        seq: 0,
        mode: Mode::Test,
        state: "run".into(),
        faults: vec![],
        out: DriveOutput::STOP,
        obs: None,
        rate_hz: 50.0,
        lateness_us: 0.0,
        tick_us: 0.0,
        deadline_misses: 0,
        channels: BTreeMap::new(),
    };
    let t = std::time::Instant::now();
    for i in 0..10_000 {
        logger.tick(&TickRecord {
            seq: i,
            ..rec.clone()
        });
    }
    assert!(
        t.elapsed() < Duration::from_millis(500),
        "producer blocked: {:?}",
        t.elapsed()
    );
    let stats = logger.close().expect("close");
    assert_eq!(stats.written + stats.dropped, 10_000);
    let _ = std::fs::remove_file(&path);
}

#[test]
fn each_lidar_revolution_is_logged_once() {
    let path = tmp("lidar");
    let logger = Logger::start(&path, 64, Duration::from_millis(50)).expect("logger");
    let scan = |t_s: f64| rf_proto::ipc::LidarScan {
        angles_rad: (0..450).map(|i| f64::from(i) * 0.014).collect(),
        ranges_m: vec![Some(1.5); 450],
        t_s,
    };
    let rec = |seq: u64, t_s: f64| TickRecord {
        mono_ns: seq * 20_000_000,
        wall_offset_ns: None,
        seq,
        mode: Mode::Test,
        state: "run".into(),
        faults: vec![],
        out: DriveOutput::STOP,
        obs: Some(Observation {
            lidar: Some(scan(t_s)),
            ..Default::default()
        }),
        rate_hz: 50.0,
        lateness_us: 0.0,
        tick_us: 0.0,
        deadline_misses: 0,
        channels: BTreeMap::new(),
    };
    // 50 Hz ticks, 10 Hz LiDAR: the same revolution is seen by 5 ticks in a row.
    for seq in 0..10 {
        logger.tick(&rec(seq, 0.1 * (seq / 5) as f64));
    }
    logger.close().expect("close");
    let (msgs, _) = messages(&path);
    let lidar: Vec<_> = msgs
        .iter()
        .filter(|(ch, _)| *ch == 2)
        .map(|(_, v)| v)
        .collect();
    assert_eq!(lidar.len(), 2);
    assert_eq!(lidar[1]["t"]["mono_ns"], 100_000_000);
    assert_eq!(lidar[0]["angles_rad"].as_array().map(Vec::len), Some(225)); // 450 -> <= 360
                                                                            // The /telemetry frames stay valid without the LiDAR (it lives in /lidar_raw only).
    if let Some(n) = python_read_frames(&path) {
        assert_eq!(n, 10);
    }
    if let Ok(py) = std::env::var("RF_PYTHON") {
        let out = std::process::Command::new(py)
            .args(["-c", "import sys; from mcap.reader import make_reader; \
                          f = open(sys.argv[1], 'rb'); \
                          print(sum(1 for _ in make_reader(f).iter_messages(topics=['/lidar_raw'])))"])
            .arg(&path)
            .output()
            .expect("python");
        assert_eq!(
            String::from_utf8_lossy(&out.stdout).trim(),
            "2",
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
    }
    let _ = std::fs::remove_file(&path);
}
