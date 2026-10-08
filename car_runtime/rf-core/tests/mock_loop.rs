//! Spec 0005 AC3/AC4/AC6 with an in-process mock controller host (no Python needed).

#![allow(clippy::expect_used)] // test helpers: a failed setup should abort the test

use rf_core::hw::{DriveOutput, MockSensors, RecordingActuators, SensorSnapshot};
use rf_core::link::ControllerLink;
use rf_core::runtime::{Fault, Runtime, RuntimeConfig};
use rf_proto::ipc::{self, Command, FromHost, Mode, RobotInfo, ToHost};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;
use std::time::{Duration, Instant};

/// Timing-sensitive tests in this file run one at a time: parallel control loops on a small CI
/// runner would otherwise measure each other instead of the code under test.
static SERIAL: std::sync::Mutex<()> = std::sync::Mutex::new(());

fn serial() -> std::sync::MutexGuard<'static, ()> {
    SERIAL
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

#[derive(Clone, Copy)]
enum Act {
    Reply(Command),
    Sleep(u64),
    Raise,
    Crash,
}

fn info() -> RobotInfo {
    RobotInfo {
        car_name: "mock".into(),
        sensors: vec!["front".into()],
        max_steer_rad: 0.5,
        max_speed_m_s: 2.0,
        wheelbase_m: 0.2,
        track_m: 0.15,
        control_rate_hz: 50.0,
    }
}

/// Mock host: answers `hello` with `ready`, then `behave(seq)` for every observation.
fn mock_host(behave: impl Fn(u64) -> Act + Send + 'static) -> ControllerLink {
    let (core, host) = UnixStream::pair().expect("socketpair");
    std::thread::spawn(move || {
        let mut out = host.try_clone().expect("clone");
        for line in BufReader::new(host).lines() {
            let Ok(line) = line else { return };
            let reply = match ipc::from_line::<ToHost>(&line).expect("valid msg") {
                ToHost::Hello { .. } => FromHost::Ready,
                ToHost::Shutdown => return,
                ToHost::Obs { seq, .. } => match behave(seq) {
                    Act::Reply(cmd) => FromHost::Cmd {
                        seq,
                        cmd,
                        channels: Default::default(),
                        notes: vec![],
                    },
                    Act::Sleep(ms) => {
                        std::thread::sleep(Duration::from_millis(ms));
                        FromHost::Cmd {
                            seq,
                            cmd: Command::default(),
                            channels: Default::default(),
                            notes: vec![],
                        }
                    }
                    Act::Raise => FromHost::Error {
                        seq: Some(seq),
                        detail: "RuntimeError: boom".into(),
                    },
                    Act::Crash => return,
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

fn runtime(
    cfg: RuntimeConfig,
    link: ControllerLink,
) -> (
    Runtime<MockSensors, RecordingActuators>,
    Arc<RecordingActuators>,
    Arc<MockSensors>,
) {
    let act = Arc::new(RecordingActuators::default());
    let sensors = Arc::new(MockSensors::new(SensorSnapshot::default()));
    let rt = Runtime::new(
        cfg,
        sensors.clone(),
        act.clone(),
        link,
        Duration::from_secs(1),
    )
    .expect("hello");
    (rt, act, sensors)
}

const DRIVE: Command = Command {
    steering_rad: 0.1,
    speed_m_s: 1.0,
};

#[test]
fn ac3_loop_runs_at_50hz_and_forwards_commands() {
    let _serial = serial();
    let (mut rt, act, _) = runtime(
        RuntimeConfig::new(info(), Mode::Test),
        mock_host(|_| Act::Reply(DRIVE)),
    );
    let t = Instant::now();
    let report = rt.run(&AtomicBool::new(false), Some(50));
    let elapsed = t.elapsed();
    assert_eq!(report.ticks, 50);
    assert!(report.fault.is_none(), "{:?}", report.fault);
    // 50 ticks at 50 Hz ~ 1 s.
    assert!(
        elapsed >= Duration::from_millis(970) && elapsed < Duration::from_millis(1300),
        "{elapsed:?}"
    );
    let outs = act.outputs();
    let drives = outs.iter().filter(|(_, o)| !o.stop).count();
    assert_eq!(drives, 50);
    assert!(outs
        .iter()
        .any(|(_, o)| o.speed_m_s == 1.0 && o.steering_rad == 0.1));
    // Generous bound for shared CI runners; the Pi target (p99 < 2 ms) is measured in HIL.
    assert!(report.jitter.p99_us < 10_000.0, "{:?}", report.jitter);
    println!("jitter {:?} step {:?}", report.jitter, report.step);
}

fn first_stop_after(outs: &[(Instant, DriveOutput)], t: Instant) -> Option<Instant> {
    outs.iter()
        .find(|(ts, o)| *ts >= t && o.stop && o.fault)
        .map(|(ts, _)| *ts)
}

#[test]
fn ac4_hanging_controller_stops_within_50ms() {
    let _serial = serial();
    let (mut rt, act, _) = runtime(
        RuntimeConfig::new(info(), Mode::Test),
        mock_host(|seq| {
            if seq < 10 {
                Act::Reply(DRIVE)
            } else {
                Act::Sleep(100)
            }
        }),
    );
    let report = rt.run(&AtomicBool::new(false), Some(20));
    assert_eq!(report.fault, Some(Fault::Deadline { seq: 10 }));
    assert!(report.events.iter().any(|e| e.kind == "fault"));
    let outs = act.outputs();
    // The last drive command is the step before the hang; the stop must follow within 50 ms of
    // the moment the hanging step was due (deadline 15 ms after it was sent).
    let last_drive = outs
        .iter()
        .rev()
        .find(|(_, o)| !o.stop)
        .map(|(t, _)| *t)
        .expect("drove");
    let stop = first_stop_after(&outs, last_drive).expect("stopped");
    assert!(
        stop - last_drive < Duration::from_millis(20 + 50),
        "{:?}",
        stop - last_drive
    );
    // Nothing but stop commands after the fault.
    assert!(outs.iter().filter(|(t, _)| *t > stop).all(|(_, o)| o.stop));
}

#[test]
fn ac4_raising_controller_stops_and_logs() {
    let _serial = serial();
    let (mut rt, act, _) = runtime(
        RuntimeConfig::new(info(), Mode::Test),
        mock_host(|seq| {
            if seq < 3 {
                Act::Reply(DRIVE)
            } else {
                Act::Raise
            }
        }),
    );
    let report = rt.run(&AtomicBool::new(false), Some(10));
    assert!(
        matches!(report.fault, Some(Fault::Controller { seq: 3, ref detail }) if detail.contains("boom"))
    );
    let outs = act.outputs();
    let last = outs.last().expect("outputs").1;
    assert!(last.stop && last.fault);
}

#[test]
fn crashed_controller_is_a_fault() {
    let _serial = serial();
    let (mut rt, _, _) = runtime(
        RuntimeConfig::new(info(), Mode::Test),
        mock_host(|seq| {
            if seq < 2 {
                Act::Reply(DRIVE)
            } else {
                Act::Crash
            }
        }),
    );
    let report = rt.run(&AtomicBool::new(false), Some(5));
    assert_eq!(report.fault, Some(Fault::LinkClosed { seq: 2 }));
}

#[test]
fn estop_latches_without_calling_controller() {
    let _serial = serial();
    let (mut rt, act, sensors) = runtime(
        RuntimeConfig::new(info(), Mode::Test),
        mock_host(|_| Act::Reply(DRIVE)),
    );
    sensors.set(SensorSnapshot {
        estop: true,
        ..Default::default()
    });
    let report = rt.run(&AtomicBool::new(false), Some(3));
    assert_eq!(report.fault, Some(Fault::EStop));
    assert!(act.outputs().iter().all(|(_, o)| o.stop));
}

#[test]
fn lost_sensor_link_latches_fault() {
    let _serial = serial();
    let (mut rt, act, sensors) = runtime(
        RuntimeConfig::new(info(), Mode::Test),
        mock_host(|_| Act::Reply(DRIVE)),
    );
    sensors.set(SensorSnapshot {
        link_lost: Some("ev3".into()),
        ..Default::default()
    });
    let report = rt.run(&AtomicBool::new(false), Some(3));
    assert_eq!(report.fault, Some(Fault::LinkLost("ev3".into())));
    assert!(act.outputs().iter().all(|(_, o)| o.stop));
}

#[test]
fn ac6_speed_limit_enforced_regardless_of_controller() {
    let _serial = serial();
    let mut cfg = RuntimeConfig::new(info(), Mode::Test);
    cfg.test_speed_limit_m_s = 0.5;
    let wild = Command {
        steering_rad: 3.0,
        speed_m_s: 50.0,
    };
    let (mut rt, act, _) = runtime(cfg, mock_host(move |_| Act::Reply(wild)));
    rt.run(&AtomicBool::new(false), Some(5));
    for (_, o) in act.outputs().iter().filter(|(_, o)| !o.stop) {
        assert_eq!(o.speed_m_s, 0.5);
        assert_eq!(o.steering_rad, 0.5);
    }
}

#[test]
fn ac6_teleop_dead_man_stops_when_not_refreshed() {
    let _serial = serial();
    let (mut rt, act, _) = runtime(
        RuntimeConfig::new(info(), Mode::Test),
        mock_host(|_| Act::Reply(DRIVE)),
    );
    let teleop = Command {
        steering_rad: -0.2,
        speed_m_s: 0.3,
    };
    rt.teleop
        .lock()
        .expect("lock")
        .update(Instant::now(), teleop, Mode::Test);
    // 25 ticks = 500 ms: teleop drives for ~300 ms, then the dead-man stops the car.
    rt.run(&AtomicBool::new(false), Some(25));
    let outs = act.outputs();
    let driving: Vec<_> = outs.iter().filter(|(_, o)| !o.stop).collect();
    assert!(
        !driving.is_empty()
            && driving
                .iter()
                .all(|(_, o)| o.speed_m_s == 0.3 && o.steering_rad == -0.2)
    );
    assert!(driving.len() < 20, "{} teleop ticks", driving.len());
    let tail = &outs[outs.len() - 3..];
    assert!(tail.iter().all(|(_, o)| o.stop && o.speed_m_s == 0.0));
}
