//! LD06/LD19 LiDAR driver (spec 0005 `rf-lidar`).
//!
//! A reader thread feeds the UART byte stream through `rf_proto::ld06` and keeps the latest
//! complete revolution, converted to the controller convention (angles counter-clockwise from
//! the car's forward axis in `(-pi, pi]`, ranges in metres, `None` = no return). The first
//! revolution after start is partial and is dropped.
//!
//! On the board the serial port is configured with `stty` (230400 baud, raw), so the driver
//! needs neither a serial crate nor `unsafe` termios calls.

use rf_proto::ipc::LidarScan;
use rf_proto::ld06::{Parser, RevolutionBuilder};
use std::io::{self, Read};
use std::path::Path;
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Instant;

pub const BAUD: u32 = 230_400;

#[derive(Debug, Clone, PartialEq)]
pub struct LidarConfig {
    /// Counter-clockwise angle of the sensor's zero mark relative to the car's forward axis.
    pub mount_offset_rad: f64,
    /// Revolutions with fewer points are discarded (start-up, motor not yet at speed).
    pub min_points: usize,
}

impl Default for LidarConfig {
    fn default() -> Self {
        Self {
            mount_offset_rad: 0.0,
            min_points: 100,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct LidarStats {
    pub bytes: u64,
    pub packets: u64,
    pub bad_packets: u64,
    pub revolutions: u64,
    pub short_revolutions: u64,
    /// Partial revolutions dropped because the angle never wrapped (e.g. stalled motor).
    pub overflowed_revolutions: u64,
    /// Rotation speed reported by the last packet (degrees per second; nominal 3600 = 10 Hz).
    pub speed_dps: u16,
    /// The reader stopped (end of stream or read error).
    pub ended: bool,
}

#[derive(Default)]
struct State {
    latest: Option<(Instant, LidarScan)>,
    stats: LidarStats,
}

pub struct Lidar {
    state: Arc<Mutex<State>>,
    stop: Arc<AtomicBool>,
}

fn lock(m: &Mutex<State>) -> MutexGuard<'_, State> {
    m.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
}

impl Lidar {
    /// Start reading LD06 packets from any byte stream (serial port, socket, test data).
    pub fn start(mut reader: Box<dyn Read + Send>, cfg: LidarConfig) -> Self {
        let state = Arc::new(Mutex::new(State::default()));
        let stop = Arc::new(AtomicBool::new(false));
        let (st, stp) = (state.clone(), stop.clone());
        // The thread is detached: a blocking read cannot be interrupted, and the LiDAR streams
        // continuously, so it notices `stop` on the next chunk.
        std::thread::spawn(move || {
            let mut parser = Parser::new();
            let mut revs = RevolutionBuilder::new();
            let mut first = true;
            let mut buf = [0u8; 1024];
            while !stp.load(Ordering::Acquire) {
                let n = match reader.read(&mut buf) {
                    Ok(0) => break,
                    Ok(n) => n,
                    Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                    Err(_) => break,
                };
                let packets = parser.push(&buf[..n]);
                let mut s = lock(&st);
                s.stats.bytes += n as u64;
                s.stats.packets += packets.len() as u64;
                s.stats.bad_packets = parser.dropped;
                for p in &packets {
                    s.stats.speed_dps = p.speed_dps;
                    let rev = revs.push(p);
                    s.stats.overflowed_revolutions = revs.dropped;
                    let Some(rev) = rev else { continue };
                    if first {
                        first = false; // started mid-revolution
                        continue;
                    }
                    if rev.points.len() < cfg.min_points {
                        s.stats.short_revolutions += 1;
                        continue;
                    }
                    let (angles_rad, ranges_m) = rev.to_ccw_rad(cfg.mount_offset_rad);
                    s.stats.revolutions += 1;
                    s.latest = Some((
                        Instant::now(),
                        LidarScan {
                            angles_rad,
                            ranges_m,
                            t_s: 0.0,
                        },
                    ));
                }
            }
            lock(&st).stats.ended = true;
        });
        Self { state, stop }
    }

    /// Configure `device` (230400 baud, raw) with `stty` and start reading it.
    pub fn open_serial(device: &Path, cfg: LidarConfig) -> io::Result<Self> {
        let status = Command::new("stty")
            .arg("-F")
            .arg(device)
            .args([
                BAUD.to_string().as_str(),
                "raw",
                "-echo",
                "-echoe",
                "-echok",
                "-ixon",
                "-ixoff",
            ])
            .status()?;
        if !status.success() {
            return Err(io::Error::other(format!(
                "stty failed for {}",
                device.display()
            )));
        }
        Ok(Self::start(Box::new(std::fs::File::open(device)?), cfg))
    }

    /// Latest complete revolution and when it finished (`t_s` is filled in by the runtime).
    pub fn latest(&self) -> Option<(Instant, LidarScan)> {
        lock(&self.state).latest.clone()
    }

    pub fn stats(&self) -> LidarStats {
        lock(&self.state).stats
    }
}

impl Drop for Lidar {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
    }
}
