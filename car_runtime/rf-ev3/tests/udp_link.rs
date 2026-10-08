//! EV3 link over real UDP sockets on localhost with a mock EV3 (spec 0005 EV3 link, AC3).

#![allow(clippy::expect_used)] // test helpers: a failed setup should abort the test

use rf_core::hw::{Actuators, DriveOutput, Sensors};
use rf_ev3::{Ev3Config, Ev3Link, UdpTransport};
use rf_proto::ev3::{cmd_flags, CommandFrame, SensorFrame, SENSOR_LEN};
use std::net::UdpSocket;
use std::time::{Duration, Instant};

/// Timing-sensitive tests in this file run one at a time: parallel control loops on a small CI
/// runner would otherwise measure each other instead of the code under test.
static SERIAL: std::sync::Mutex<()> = std::sync::Mutex::new(());

fn serial() -> std::sync::MutexGuard<'static, ()> {
    SERIAL
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

struct MockEv3 {
    sock: UdpSocket,
}

impl MockEv3 {
    fn recv_cmds(&self, window: Duration) -> Vec<(Instant, CommandFrame)> {
        let end = Instant::now() + window;
        let mut out = Vec::new();
        let mut buf = [0u8; 256];
        while let Some(left) = end.checked_duration_since(Instant::now()) {
            self.sock
                .set_read_timeout(Some(left.max(Duration::from_millis(1))))
                .expect("timeout");
            if let Ok(n) = self.sock.recv(&mut buf) {
                out.push((
                    Instant::now(),
                    CommandFrame::decode(&buf[..n]).expect("valid command frame"),
                ));
            }
        }
        out
    }

    fn send(&self, f: &SensorFrame) {
        self.sock.send(&f.encode()).expect("send");
    }
}

fn setup(cfg: Ev3Config) -> (Ev3Link, MockEv3) {
    let ev3 = UdpSocket::bind("127.0.0.1:0").expect("bind ev3");
    let board = UdpSocket::bind("127.0.0.1:0").expect("bind board");
    let board_addr = board.local_addr().expect("addr");
    drop(board);
    let transport =
        UdpTransport::new(board_addr, ev3.local_addr().expect("addr")).expect("transport");
    ev3.connect(board_addr).expect("connect");
    (
        Ev3Link::start(cfg, Box::new(transport)),
        MockEv3 { sock: ev3 },
    )
}

fn cfg() -> Ev3Config {
    Ev3Config {
        steer_motor_deg_per_rad: 100.0,
        drive_counts_per_m: 1000.0,
        ultrasonic: [("front".to_string(), 0)].into(),
        ..Default::default()
    }
}

#[test]
fn keepalive_at_100hz_with_increasing_seq_and_initial_stop() {
    let _serial = serial();
    let (_link, ev3) = setup(cfg());
    let cmds = ev3.recv_cmds(Duration::from_millis(200));
    // ~20 frames expected; allow scheduling slack on CI.
    assert!(
        cmds.len() >= 12 && cmds.len() <= 25,
        "{} frames",
        cmds.len()
    );
    assert!(cmds.windows(2).all(|w| w[1].1.seq > w[0].1.seq));
    // Nothing commanded yet: the link starts in stop.
    assert!(cmds
        .iter()
        .all(|(_, c)| c.flags & cmd_flags::STOP != 0 && c.drive_speed_cps == 0));
}

#[test]
fn new_output_is_sent_immediately_and_repeated() {
    let _serial = serial();
    let (link, ev3) = setup(cfg());
    ev3.recv_cmds(Duration::from_millis(30));
    let t = Instant::now();
    link.send(DriveOutput {
        steering_rad: 0.1,
        speed_m_s: 0.5,
        stop: false,
        fault: false,
    });
    let cmds = ev3.recv_cmds(Duration::from_millis(60));
    let first = cmds
        .iter()
        .find(|(_, c)| c.drive_speed_cps == 500)
        .expect("drive frame");
    assert!(first.0 - t < Duration::from_millis(8), "{:?}", first.0 - t);
    assert_eq!(first.1.steer_target_cdeg, 1000);
    // Keep-alive repeats the latest output.
    assert!(
        cmds.iter()
            .filter(|(_, c)| c.drive_speed_cps == 500)
            .count()
            >= 3
    );
    link.send(DriveOutput::FAULT_STOP);
    let after = ev3.recv_cmds(Duration::from_millis(30));
    assert!(after
        .iter()
        .all(|(_, c)| c.flags & cmd_flags::STOP != 0 && c.drive_speed_cps == 0));
}

#[test]
fn sensor_frames_become_snapshots_and_staleness_is_link_lost() {
    let _serial = serial();
    let (link, ev3) = setup(cfg());
    assert!(link.snapshot().link_lost.is_some(), "no frame yet");
    // Ack the most recent command so the link can measure the round trip.
    let last_cmd = ev3
        .recv_cmds(Duration::from_millis(30))
        .last()
        .expect("cmd")
        .1
        .seq;
    ev3.send(&SensorFrame {
        seq: 1,
        ack_seq: last_cmd,
        ultrasonic_mm: [1500, 0, 0, 0],
        battery_mv: 8000,
        ..Default::default()
    });
    std::thread::sleep(Duration::from_millis(20));
    let s = link.snapshot();
    assert_eq!(s.link_lost, None);
    assert_eq!(s.ultrasonic_m["front"], Some(1.5));
    assert_eq!(s.battery_v, Some(8.0));
    let stats = link.stats();
    assert_eq!(stats.rx_frames, 1);
    assert!(stats.last_rtt_us.is_some());

    // Corrupted and duplicate frames are counted/ignored; a sequence gap counts as loss.
    let mut bad = SensorFrame {
        seq: 2,
        ..Default::default()
    }
    .encode();
    bad[SENSOR_LEN - 1] ^= 0xFF;
    ev3.sock.send(&bad).expect("send");
    ev3.send(&SensorFrame {
        seq: 1,
        ..Default::default()
    });
    ev3.send(&SensorFrame {
        seq: 4,
        ultrasonic_mm: [700, 0, 0, 0],
        ..Default::default()
    });
    std::thread::sleep(Duration::from_millis(20));
    let stats = link.stats();
    assert_eq!((stats.rx_frames, stats.rx_bad, stats.rx_lost), (2, 1, 2));
    assert_eq!(link.snapshot().ultrasonic_m["front"], Some(0.7));

    // No frames for longer than the link timeout -> link lost.
    std::thread::sleep(Duration::from_millis(120));
    assert!(link.snapshot().link_lost.is_some());
}

#[test]
fn drop_sends_final_stop() {
    let _serial = serial();
    let (link, ev3) = setup(cfg());
    link.send(DriveOutput {
        steering_rad: 0.0,
        speed_m_s: 1.0,
        stop: false,
        fault: false,
    });
    ev3.recv_cmds(Duration::from_millis(20));
    drop(link);
    let cmds = ev3.recv_cmds(Duration::from_millis(30));
    let last = cmds.last().expect("final frame").1;
    assert!(last.flags & cmd_flags::STOP != 0 && last.drive_speed_cps == 0);
}
