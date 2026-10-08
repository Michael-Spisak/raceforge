//! On-car MCAP logging (spec 0005 "Logging & telemetry", ADR-0013).
//!
//! [`Logger`] is a [`TickSink`]: the control thread hands over records through a bounded
//! queue (never blocks; records are dropped and counted when the writer falls behind) and a
//! writer thread appends them to an MCAP file:
//!
//! - `/telemetry`: `TelemetryFrame` JSON (schema `raceforge.TelemetryFrame`, identical to the
//!   simulator's recorder, so `raceforge.sim.record.read_frames` reads car logs too)
//! - `/events`: faults, notes and runtime events
//! - `/lidar_raw`: each new LiDAR revolution once (not every tick), reduced to at most
//!   [`LIDAR_MAX_POINTS`] points
//! - `/ev3_raw`: every frame on the EV3 link in wire units (sent commands, received sensor
//!   frames, undecodable datagrams), fed by [`Logger::ev3_raw`]; link latency (`ack_seq`) and
//!   loss (`seq` gaps) can be measured from it

pub mod frame;
pub mod mcap;

use mcap::McapWriter;
use rf_core::runtime::{Event, TickRecord, TickSink};
use rf_proto::ev3::RawFrame;
use std::fs::File;
use std::io::{self, BufWriter};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{self, Receiver, SyncSender, TrySendError};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

pub const TELEMETRY_TOPIC: &str = "/telemetry";
pub const EVENTS_TOPIC: &str = "/events";
pub const LIDAR_TOPIC: &str = "/lidar_raw";
pub const EV3_TOPIC: &str = "/ev3_raw";
pub const LIDAR_MAX_POINTS: usize = 360;
/// JSON Schema of `TelemetryFrame` v1, exported from `raceforge.core` (drift-checked by
/// `tests/car/test_log_schema.py`).
pub const TELEMETRY_SCHEMA: &str = include_str!("../schemas/telemetry.v1.schema.json");

const LIDAR_SCHEMA: &str = r#"{"type":"object","properties":{"t":{"type":"object","properties":{"mono_ns":{"type":"integer"}}},"angles_rad":{"type":"array","items":{"type":["number","null"]}},"ranges_m":{"type":"array","items":{"type":["number","null"]}}}}"#;

const EV3_SCHEMA: &str = r#"{"type":"object","properties":{"t":{"type":"object","properties":{"mono_ns":{"type":"integer"}}},"dir":{"enum":["tx","rx","rx_bad"]},"seq":{"type":"integer"},"t_ms":{"type":"integer"},"ack_seq":{"type":"integer"},"steer_target_cdeg":{"type":"integer"},"drive_speed_cps":{"type":"integer"},"flags":{"type":"integer"},"led":{"type":"integer"},"lcd":{"type":"integer"},"motors":{"type":"array","items":{"type":"object","properties":{"tacho":{"type":"integer"},"speed_cps":{"type":"integer"}}}},"ultrasonic_mm":{"type":"array","items":{"type":"integer"}},"gyro_rate_dps":{"type":"integer"},"gyro_angle_deg":{"type":"integer"},"touch":{"type":"integer"},"buttons":{"type":"integer"},"battery_mv":{"type":"integer"},"len":{"type":"integer"},"error":{"type":"string"}},"required":["t","dir"]}"#;

const EVENT_SCHEMA: &str = r#"{"type":"object","properties":{"t":{"type":"object","properties":{"mono_ns":{"type":"integer"}}},"kind":{"type":"string"},"detail":{"type":"string"}}}"#;

enum Msg {
    Tick(Box<TickRecord>),
    Event(u64, Event),
    Ev3(u64, RawFrame),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct LogStats {
    pub written: u64,
    pub dropped: u64,
}

pub struct Logger {
    tx: Mutex<Option<SyncSender<Msg>>>,
    dropped: Arc<AtomicU64>,
    thread: Mutex<Option<JoinHandle<io::Result<u64>>>>,
    pub path: PathBuf,
}

impl Logger {
    /// Create `path` and start the writer thread. `queue` = records buffered before dropping;
    /// the file is flushed at least every `flush_every` so a power cut loses little.
    pub fn start(path: &Path, queue: usize, flush_every: Duration) -> io::Result<Self> {
        let mut w = McapWriter::new(BufWriter::new(File::create(path)?), "raceforge-car-runtime")?;
        let tel_schema = w.schema(
            "raceforge.TelemetryFrame",
            "jsonschema",
            TELEMETRY_SCHEMA.as_bytes(),
        )?;
        let tel = w.channel(tel_schema, TELEMETRY_TOPIC, "json")?;
        let ev_schema = w.schema("raceforge.Event", "jsonschema", EVENT_SCHEMA.as_bytes())?;
        let ev = w.channel(ev_schema, EVENTS_TOPIC, "json")?;
        let lidar_schema =
            w.schema("raceforge.LidarScan", "jsonschema", LIDAR_SCHEMA.as_bytes())?;
        let lidar = w.channel(lidar_schema, LIDAR_TOPIC, "json")?;
        let ev3_schema = w.schema("raceforge.Ev3Frame", "jsonschema", EV3_SCHEMA.as_bytes())?;
        let ev3 = w.channel(ev3_schema, EV3_TOPIC, "json")?;
        w.flush()?;
        let (tx, rx) = mpsc::sync_channel(queue);
        let ch = Channels {
            tel,
            ev,
            lidar,
            ev3,
        };
        let thread = std::thread::spawn(move || write_loop(w, rx, ch, flush_every));
        Ok(Self {
            tx: Mutex::new(Some(tx)),
            dropped: Arc::new(AtomicU64::new(0)),
            thread: Mutex::new(Some(thread)),
            path: path.to_path_buf(),
        })
    }

    fn send(&self, m: Msg) {
        let guard = self
            .tx
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if let Some(tx) = guard.as_ref() {
            if let Err(TrySendError::Full(_) | TrySendError::Disconnected(_)) = tx.try_send(m) {
                self.dropped.fetch_add(1, Ordering::Relaxed);
            }
        }
    }

    /// Log one EV3 link frame (`/ev3_raw`); `mono_ns` on the runtime's clock. Never blocks.
    pub fn ev3_raw(&self, mono_ns: u64, f: &RawFrame) {
        self.send(Msg::Ev3(mono_ns, f.clone()));
    }

    /// Stop accepting records, write everything queued and close the file properly.
    pub fn close(&self) -> io::Result<LogStats> {
        self.tx
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .take();
        let handle = self
            .thread
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .take();
        let written = match handle {
            Some(h) => h
                .join()
                .map_err(|_| io::Error::other("log writer panicked"))??,
            None => 0,
        };
        Ok(LogStats {
            written,
            dropped: self.dropped.load(Ordering::Relaxed),
        })
    }
}

impl Drop for Logger {
    fn drop(&mut self) {
        let _ = self.close();
    }
}

impl TickSink for Logger {
    fn tick(&self, rec: &TickRecord) {
        self.send(Msg::Tick(Box::new(rec.clone())));
    }

    fn event(&self, mono_ns: u64, e: &Event) {
        self.send(Msg::Event(mono_ns, e.clone()));
    }
}

struct Channels {
    tel: u16,
    ev: u16,
    lidar: u16,
    ev3: u16,
}

fn write_loop(
    mut w: McapWriter<BufWriter<File>>,
    rx: Receiver<Msg>,
    ch: Channels,
    flush_every: Duration,
) -> io::Result<u64> {
    let mut last_flush = Instant::now();
    let mut ev_seq = 0u32;
    let mut lidar_seq = 0u32;
    let mut ev3_seq = 0u32;
    let mut last_scan_t: Option<f64> = None;
    loop {
        let msg = match rx.recv_timeout(flush_every) {
            Ok(m) => Some(m),
            Err(mpsc::RecvTimeoutError::Timeout) => None,
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        };
        match msg {
            Some(Msg::Tick(r)) => {
                let data =
                    serde_json::to_vec(&frame::telemetry_frame(&r)).map_err(io::Error::other)?;
                w.message(ch.tel, r.seq as u32, r.mono_ns, &data)?;
                if let Some(scan) = r.obs.as_ref().and_then(|o| o.lidar.as_ref()) {
                    if last_scan_t != Some(scan.t_s) {
                        last_scan_t = Some(scan.t_s);
                        let ns = if scan.t_s.is_finite() && scan.t_s >= 0.0 {
                            (scan.t_s * 1e9) as u64
                        } else {
                            r.mono_ns
                        };
                        let data =
                            serde_json::to_vec(&frame::lidar_raw(scan, ns, LIDAR_MAX_POINTS))
                                .map_err(io::Error::other)?;
                        w.message(ch.lidar, lidar_seq, ns, &data)?;
                        lidar_seq = lidar_seq.wrapping_add(1);
                    }
                }
            }
            Some(Msg::Event(ns, e)) => {
                let data = serde_json::to_vec(&serde_json::json!({
                    "t": { "mono_ns": ns }, "kind": e.kind, "detail": e.detail
                }))
                .map_err(io::Error::other)?;
                w.message(ch.ev, ev_seq, ns, &data)?;
                ev_seq = ev_seq.wrapping_add(1);
            }
            Some(Msg::Ev3(ns, f)) => {
                let data = serde_json::to_vec(&frame::ev3_raw(&f, ns)).map_err(io::Error::other)?;
                w.message(ch.ev3, ev3_seq, ns, &data)?;
                ev3_seq = ev3_seq.wrapping_add(1);
            }
            None => {}
        }
        if last_flush.elapsed() >= flush_every {
            w.flush()?;
            last_flush = Instant::now();
        }
    }
    let n = w.messages;
    w.finish()?;
    Ok(n)
}
