//! EV3 link client (spec 0005 "EV3 link", ADR-0014).
//!
//! UDP frames over ev3dev's USB-Ethernet gadget (`rf_proto::ev3`). The board sends a
//! [`CommandFrame`] at 100 Hz (keep-alive thread re-sends the latest output, and every new output
//! is sent immediately so a stop never waits); the EV3 answers with [`SensorFrame`]s that a
//! receive thread keeps as the latest value. [`Ev3Link`] implements the runtime's [`Sensors`] and
//! [`Actuators`] traits and converts between SI units and motor degrees / tacho counts.
//!
//! Conventions on the EV3 side: `touch` is a bitmask (bit `i` = sensor port `i + 1` pressed);
//! `lcd` is one of the [`lcd`] codes.

use rf_core::hw::{Actuators, DriveOutput, SensorSnapshot, Sensors};
use rf_proto::ev3::{cmd_flags, status_flags, CommandFrame, SensorFrame};
use std::collections::{BTreeMap, VecDeque};
use std::io::{self, ErrorKind};
use std::net::{SocketAddr, UdpSocket};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

/// LCD status codes shown by the EV3 program.
pub mod lcd {
    pub const RUN: u8 = 0;
    pub const STOPPED: u8 = 1;
    pub const FAULT: u8 = 2;
}

/// How the car is wired to the EV3 and how motor units map to SI (from the car config/bundle).
#[derive(Debug, Clone, PartialEq)]
pub struct Ev3Config {
    /// Motor port index (0 = A .. 3 = D) of the steering motor.
    pub steer_motor: usize,
    /// Motor port index of the drive motor.
    pub drive_motor: usize,
    /// Steering motor degrees per radian of wheel steering angle (gear ratio, sign included).
    pub steer_motor_deg_per_rad: f64,
    /// Drive motor tacho counts (degrees) per metre travelled (wheel + gears, sign included).
    pub drive_counts_per_m: f64,
    /// Ultrasonic sensors: name used by controllers (`front`, `left`, ...) -> sensor port index.
    pub ultrasonic: BTreeMap<String, usize>,
    /// A gyro is connected. The EV3 gyro counts clockwise; heading is reported counter-clockwise.
    pub gyro: bool,
    /// Sensor port index of a touch sensor used as hardware emergency stop.
    pub estop_touch_port: Option<usize>,
    /// No valid frame for this long -> `link_lost` (the runtime then stops; spec: EV3 itself
    /// stops after 150 ms).
    pub link_timeout: Duration,
    /// Command keep-alive period (spec: 100 Hz).
    pub keepalive: Duration,
}

impl Default for Ev3Config {
    fn default() -> Self {
        Self {
            steer_motor: 0,
            drive_motor: 1,
            steer_motor_deg_per_rad: 3.0 * 180.0 / std::f64::consts::PI,
            drive_counts_per_m: 360.0 / (std::f64::consts::PI * 0.056),
            ultrasonic: BTreeMap::new(),
            gyro: true,
            estop_touch_port: None,
            link_timeout: Duration::from_millis(100),
            keepalive: Duration::from_millis(10),
        }
    }
}

fn clamp_i16(x: f64) -> i16 {
    if x.is_finite() {
        x.round().clamp(f64::from(i16::MIN), f64::from(i16::MAX)) as i16
    } else {
        0
    }
}

impl Ev3Config {
    /// Encode a drive output into a command frame (seq/t filled in by the link).
    pub fn command(&self, out: &DriveOutput) -> CommandFrame {
        let mut flags = 0;
        if out.stop {
            flags |= cmd_flags::STOP;
        }
        let (steer, speed) = if out.stop {
            (out.steering_rad, 0.0)
        } else {
            (out.steering_rad, out.speed_m_s)
        };
        CommandFrame {
            steer_target_cdeg: clamp_i16(steer * self.steer_motor_deg_per_rad * 100.0),
            drive_speed_cps: clamp_i16(speed * self.drive_counts_per_m),
            flags,
            lcd: if out.fault {
                lcd::FAULT
            } else if out.stop {
                lcd::STOPPED
            } else {
                lcd::RUN
            },
            ..Default::default()
        }
    }

    /// Convert a sensor frame into the runtime's snapshot (SI, controller conventions).
    pub fn snapshot(&self, f: &SensorFrame) -> SensorSnapshot {
        let motor = |i: usize| f.motors.get(i).copied().unwrap_or_default();
        let ultrasonic_m = self
            .ultrasonic
            .iter()
            .map(|(name, &port)| (name.clone(), f.ultrasonic_m(port)))
            .collect();
        let touch = |port: usize| port < 8 && f.touch & (1 << port) != 0;
        SensorSnapshot {
            ultrasonic_m,
            lidar: None,
            yaw_rate_rad_s: self.gyro.then(|| -f64::from(f.gyro_rate_dps).to_radians()),
            heading_rad: self.gyro.then(|| -f64::from(f.gyro_angle_deg).to_radians()),
            speed_m_s: Some(f64::from(motor(self.drive_motor).speed_cps) / self.drive_counts_per_m),
            steering_rad: Some(
                f64::from(motor(self.steer_motor).tacho) / self.steer_motor_deg_per_rad,
            ),
            bumper: BTreeMap::new(),
            battery_v: Some(f64::from(f.battery_mv) / 1000.0),
            estop: f.flags & status_flags::ESTOP_PRESSED != 0
                || self.estop_touch_port.is_some_and(touch),
            link_lost: None,
        }
    }
}

/// Datagram transport to the EV3 (UDP in production; tests may use their own).
pub trait Transport: Send + Sync {
    fn send(&self, frame: &[u8]) -> io::Result<()>;
    /// Wait up to `timeout` for one datagram; `Ok(None)` on timeout.
    fn recv(&self, buf: &mut [u8], timeout: Duration) -> io::Result<Option<usize>>;
}

pub struct UdpTransport(UdpSocket);

impl UdpTransport {
    /// Bind `local` (e.g. the board's gadget address) and talk only to `ev3`.
    pub fn new(local: SocketAddr, ev3: SocketAddr) -> io::Result<Self> {
        let s = UdpSocket::bind(local)?;
        s.connect(ev3)?;
        Ok(Self(s))
    }
}

impl Transport for UdpTransport {
    fn send(&self, frame: &[u8]) -> io::Result<()> {
        match self.0.send(frame) {
            // ICMP port unreachable while the EV3 program is not up yet: not an error for us.
            Err(e) if e.kind() == ErrorKind::ConnectionRefused => Ok(()),
            r => r.map(|_| ()),
        }
    }

    fn recv(&self, buf: &mut [u8], timeout: Duration) -> io::Result<Option<usize>> {
        self.0
            .set_read_timeout(Some(timeout.max(Duration::from_millis(1))))?;
        match self.0.recv(buf) {
            Ok(n) => Ok(Some(n)),
            Err(e)
                if matches!(
                    e.kind(),
                    ErrorKind::WouldBlock | ErrorKind::TimedOut | ErrorKind::ConnectionRefused
                ) =>
            {
                Ok(None)
            }
            Err(e) => Err(e),
        }
    }
}

/// Link statistics (for telemetry and the spec's < 0.1 % loss / < 10 ms latency targets).
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct LinkStats {
    pub tx_frames: u64,
    pub tx_errors: u64,
    pub rx_frames: u64,
    pub rx_bad: u64,
    /// EV3 sensor frames missing according to their sequence numbers.
    pub rx_lost: u64,
    /// Round trip of the last acknowledged command (board send -> sensor frame with its ack).
    pub last_rtt_us: Option<f64>,
}

struct State {
    seq: u32,
    out: DriveOutput,
    latest: Option<(Instant, SensorFrame)>,
    sent: VecDeque<(u32, Instant)>,
    stats: LinkStats,
}

struct Shared {
    cfg: Ev3Config,
    transport: Box<dyn Transport>,
    state: Mutex<State>,
    start: Instant,
    stop: AtomicBool,
}

impl Shared {
    fn state(&self) -> MutexGuard<'_, State> {
        self.state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    fn transmit(&self, new_out: Option<DriveOutput>) {
        let mut st = self.state();
        if let Some(o) = new_out {
            st.out = o;
        }
        let mut f = self.cfg.command(&st.out);
        st.seq = st.seq.wrapping_add(1);
        f.seq = st.seq;
        f.t_ms = self.start.elapsed().as_millis() as u32;
        let now = Instant::now();
        st.sent.push_back((f.seq, now));
        if st.sent.len() > 64 {
            st.sent.pop_front();
        }
        // Send while holding the lock so frames leave in sequence order.
        match self.transport.send(&f.encode()) {
            Ok(()) => st.stats.tx_frames += 1,
            Err(_) => st.stats.tx_errors += 1,
        }
    }

    fn received(&self, data: &[u8]) {
        let now = Instant::now();
        let mut st = self.state();
        let Ok(f) = SensorFrame::decode(data) else {
            st.stats.rx_bad += 1;
            return;
        };
        if let Some((_, prev)) = st.latest {
            let gap = f.seq.wrapping_sub(prev.seq);
            if gap == 0 || gap > u32::MAX / 2 {
                return; // duplicate or reordered (older) frame
            }
            st.stats.rx_lost += u64::from(gap - 1);
        }
        if let Some(&(_, t)) = st.sent.iter().find(|(s, _)| *s == f.ack_seq) {
            st.stats.last_rtt_us = Some(now.duration_since(t).as_secs_f64() * 1e6);
        }
        st.stats.rx_frames += 1;
        st.latest = Some((now, f));
    }
}

/// Running EV3 link: keep-alive sender + receiver threads. Sends a stop frame when dropped.
pub struct Ev3Link {
    shared: Arc<Shared>,
    threads: Vec<JoinHandle<()>>,
}

impl Ev3Link {
    pub fn start(cfg: Ev3Config, transport: Box<dyn Transport>) -> Self {
        let shared = Arc::new(Shared {
            cfg,
            transport,
            state: Mutex::new(State {
                seq: 0,
                out: DriveOutput::STOP,
                latest: None,
                sent: VecDeque::new(),
                stats: LinkStats::default(),
            }),
            start: Instant::now(),
            stop: AtomicBool::new(false),
        });
        let tx = {
            let s = shared.clone();
            std::thread::spawn(move || {
                let mut next = Instant::now();
                while !s.stop.load(Ordering::Acquire) {
                    s.transmit(None);
                    next += s.cfg.keepalive;
                    let now = Instant::now();
                    if next > now {
                        std::thread::sleep(next - now);
                    } else {
                        next = now;
                    }
                }
            })
        };
        let rx = {
            let s = shared.clone();
            std::thread::spawn(move || {
                let mut buf = [0u8; 512];
                while !s.stop.load(Ordering::Acquire) {
                    match s.transport.recv(&mut buf, Duration::from_millis(20)) {
                        Ok(Some(n)) => s.received(&buf[..n]),
                        Ok(None) => {}
                        Err(_) => std::thread::sleep(Duration::from_millis(5)),
                    }
                }
            })
        };
        Self {
            shared,
            threads: vec![tx, rx],
        }
    }

    pub fn stats(&self) -> LinkStats {
        self.shared.state().stats
    }

    /// Latest raw sensor frame and its age (for the `/ev3_raw` log channel).
    pub fn latest_raw(&self) -> Option<(Duration, SensorFrame)> {
        self.shared.state().latest.map(|(t, f)| (t.elapsed(), f))
    }
}

impl Sensors for Ev3Link {
    fn snapshot(&self) -> SensorSnapshot {
        let latest = self.shared.state().latest;
        let timeout = self.shared.cfg.link_timeout;
        match latest {
            Some((t, f)) if t.elapsed() <= timeout => self.shared.cfg.snapshot(&f),
            Some((t, _)) => SensorSnapshot {
                link_lost: Some(format!(
                    "ev3: no valid frame for {} ms",
                    t.elapsed().as_millis()
                )),
                ..Default::default()
            },
            None => SensorSnapshot {
                link_lost: Some("ev3: no frame received yet".into()),
                ..Default::default()
            },
        }
    }
}

impl Actuators for Ev3Link {
    fn send(&self, out: DriveOutput) {
        self.shared.transmit(Some(out));
    }
}

impl Drop for Ev3Link {
    fn drop(&mut self) {
        self.shared.stop.store(true, Ordering::Release);
        for t in self.threads.drain(..) {
            let _ = t.join();
        }
        self.shared.transmit(Some(DriveOutput::STOP));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rf_proto::ev3::Motor;

    fn cfg() -> Ev3Config {
        Ev3Config {
            steer_motor_deg_per_rad: 100.0,
            drive_counts_per_m: 1000.0,
            ultrasonic: [("front".to_string(), 0), ("left".to_string(), 1)].into(),
            estop_touch_port: Some(3),
            ..Default::default()
        }
    }

    #[test]
    fn command_conversion() {
        let f = cfg().command(&DriveOutput {
            steering_rad: 0.2,
            speed_m_s: 0.5,
            stop: false,
            fault: false,
        });
        assert_eq!(f.steer_target_cdeg, 2000); // 0.2 rad * 100 deg/rad = 20 deg
        assert_eq!(f.drive_speed_cps, 500);
        assert_eq!((f.flags, f.lcd), (0, lcd::RUN));
    }

    #[test]
    fn stop_and_fault_frames_have_zero_speed() {
        let f = cfg().command(&DriveOutput {
            speed_m_s: 1.0,
            ..DriveOutput::FAULT_STOP
        });
        assert_eq!(f.drive_speed_cps, 0);
        assert_eq!(f.flags & cmd_flags::STOP, cmd_flags::STOP);
        assert_eq!(f.lcd, lcd::FAULT);
    }

    #[test]
    fn out_of_range_and_non_finite_are_clamped() {
        let c = cfg();
        let f = c.command(&DriveOutput {
            steering_rad: 100.0,
            speed_m_s: f64::NAN,
            stop: false,
            fault: false,
        });
        assert_eq!(f.steer_target_cdeg, i16::MAX);
        assert_eq!(f.drive_speed_cps, 0);
    }

    #[test]
    fn sensor_conversion() {
        let mut f = SensorFrame {
            ultrasonic_mm: [420, rf_proto::ev3::NO_ECHO, 0, 0],
            ..Default::default()
        };
        f.motors[0] = Motor {
            tacho: 10,
            speed_cps: 0,
        }; // steering
        f.motors[1] = Motor {
            tacho: 0,
            speed_cps: 300,
        }; // drive
        f.gyro_angle_deg = 90; // EV3: clockwise
        f.gyro_rate_dps = -10;
        f.battery_mv = 7800;
        let s = cfg().snapshot(&f);
        assert_eq!(s.ultrasonic_m["front"], Some(0.42));
        assert_eq!(s.ultrasonic_m["left"], None);
        assert_eq!(s.steering_rad, Some(0.1));
        assert_eq!(s.speed_m_s, Some(0.3));
        assert!((s.heading_rad.unwrap_or_default() + std::f64::consts::FRAC_PI_2).abs() < 1e-12);
        assert!(s.yaw_rate_rad_s.unwrap_or_default() > 0.0);
        assert_eq!(s.battery_v, Some(7.8));
        assert!(!s.estop);
        f.touch = 1 << 3;
        assert!(cfg().snapshot(&f).estop);
        f.touch = 0;
        f.flags = status_flags::ESTOP_PRESSED;
        assert!(cfg().snapshot(&f).estop);
    }
}
