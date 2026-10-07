//! Spec 0005 AC3: control loop + EV3 link over UDP + mock EV3 (100 Hz sensor frames) + mock
//! controller host. Also: EV3 going silent mid-run stops the car (link lost fault).

#![allow(clippy::expect_used)] // test helpers: a failed setup should abort the test

use rf_core::link::ControllerLink;
use rf_core::runtime::{Fault, Runtime, RuntimeConfig};
use rf_ev3::{Ev3Config, Ev3Link, UdpTransport};
use rf_proto::ev3::{cmd_flags, CommandFrame, SensorFrame};
use rf_proto::ipc::{self, Command, FromHost, Mode, RobotInfo, ToHost};
use std::io::{BufRead, BufReader, Write};
use std::net::UdpSocket;
use std::os::unix::net::UnixStream;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// Controller: steer proportional to the front distance, constant speed.
fn mock_host() -> ControllerLink {
    let (core, host) = UnixStream::pair().expect("socketpair");
    std::thread::spawn(move || {
        let mut out = host.try_clone().expect("clone");
        for line in BufReader::new(host).lines() {
            let Ok(line) = line else { return };
            let reply = match ipc::from_line::<ToHost>(&line).expect("msg") {
                ToHost::Hello { .. } => FromHost::Ready,
                ToHost::Shutdown => return,
                ToHost::Obs { seq, obs } => {
                    let front = obs
                        .ultrasonic_m
                        .get("front")
                        .copied()
                        .flatten()
                        .unwrap_or(0.0);
                    let cmd = Command {
                        steering_rad: 0.1 * front,
                        speed_m_s: 0.4,
                    };
                    FromHost::Cmd {
                        seq,
                        cmd,
                        channels: Default::default(),
                        notes: vec![],
                    }
                }
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

/// Mock EV3: sends sensor frames at 100 Hz while `alive`, records received commands.
fn mock_ev3(
    sock: UdpSocket,
    alive: Arc<AtomicBool>,
    done: Arc<AtomicBool>,
) -> Arc<Mutex<Vec<CommandFrame>>> {
    let cmds = Arc::new(Mutex::new(Vec::new()));
    let rec = cmds.clone();
    sock.set_read_timeout(Some(Duration::from_millis(2)))
        .expect("timeout");
    std::thread::spawn(move || {
        let mut seq = 0u32;
        let mut last_ack = 0u32;
        let mut next = Instant::now();
        let mut buf = [0u8; 256];
        while !done.load(Ordering::Acquire) {
            while let Ok(n) = sock.recv(&mut buf) {
                if let Ok(c) = CommandFrame::decode(&buf[..n]) {
                    last_ack = c.seq;
                    rec.lock().expect("lock").push(c);
                }
            }
            if Instant::now() >= next {
                next += Duration::from_millis(10);
                if alive.load(Ordering::Acquire) {
                    seq += 1;
                    let f = SensorFrame {
                        seq,
                        ack_seq: last_ack,
                        ultrasonic_mm: [2000, 0, 0, 0],
                        battery_mv: 7900,
                        ..Default::default()
                    };
                    let _ = sock.send(&f.encode());
                }
            }
        }
    });
    cmds
}

#[test]
fn loop_drives_through_ev3_link_and_stops_when_ev3_goes_silent() {
    let ev3_sock = UdpSocket::bind("127.0.0.1:0").expect("bind");
    let board_addr = UdpSocket::bind("127.0.0.1:0")
        .expect("bind")
        .local_addr()
        .expect("addr");
    ev3_sock.connect(board_addr).expect("connect");
    let transport =
        UdpTransport::new(board_addr, ev3_sock.local_addr().expect("addr")).expect("transport");
    let (alive, done) = (
        Arc::new(AtomicBool::new(true)),
        Arc::new(AtomicBool::new(false)),
    );
    let cmds = mock_ev3(ev3_sock, alive.clone(), done.clone());

    let cfg = Ev3Config {
        steer_motor_deg_per_rad: 100.0,
        drive_counts_per_m: 1000.0,
        ultrasonic: [("front".to_string(), 0)].into(),
        ..Default::default()
    };
    let link = Arc::new(Ev3Link::start(cfg, Box::new(transport)));
    std::thread::sleep(Duration::from_millis(50)); // first sensor frames arrive
    let info = RobotInfo {
        car_name: "car".into(),
        sensors: vec!["front".into()],
        max_steer_rad: 0.5,
        max_speed_m_s: 2.0,
        wheelbase_m: 0.2,
        track_m: 0.15,
        control_rate_hz: 50.0,
    };
    let mut rt = Runtime::new(
        RuntimeConfig::new(info, Mode::Test),
        link.clone(),
        link.clone(),
        mock_host(),
        Duration::from_secs(1),
    )
    .expect("ready");

    // Kill the EV3's sensor stream after 0.5 s; the loop must fault and stop.
    let killer = {
        let alive = alive.clone();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(500));
            alive.store(false, Ordering::Release);
        })
    };
    let report = rt.run(&AtomicBool::new(false), Some(50));
    killer.join().expect("join");
    assert!(
        matches!(report.fault, Some(Fault::LinkLost(_))),
        "{:?}",
        report.fault
    );
    std::thread::sleep(Duration::from_millis(30));
    done.store(true, Ordering::Release);

    let cmds = cmds.lock().expect("lock").clone();
    // Driving frames: front 2.0 m -> steer 0.2 rad -> 2000 cdeg; 0.4 m/s -> 400 cps.
    let driving = cmds
        .iter()
        .filter(|c| c.drive_speed_cps == 400 && c.steer_target_cdeg == 2000)
        .count();
    assert!(driving >= 30, "{driving} driving frames");
    // ~100 Hz on the wire (keep-alive + immediate sends) over ~1 s.
    assert!(cmds.len() >= 80, "{} frames", cmds.len());
    // After the fault: only stop frames, LCD shows the fault.
    let last = cmds.last().expect("frames");
    assert!(
        last.flags & cmd_flags::STOP != 0
            && last.drive_speed_cps == 0
            && last.lcd == rf_ev3::lcd::FAULT
    );
    let stats = link.stats();
    assert!(stats.rx_frames >= 40 && stats.rx_bad == 0, "{stats:?}");
    println!("link stats {stats:?}, loop jitter {:?}", report.jitter);
}
