//! On-car MCAP logging (spec 0005 "Logging & telemetry", ADR-0013).
//!
//! [`Logger`] is a [`TickSink`]: the control thread hands over records through a bounded
//! queue (never blocks; records are dropped and counted when the writer falls behind) and a
//! writer thread appends them to an MCAP file:
//!
//! - `/telemetry`: `TelemetryFrame` JSON (schema `raceforge.TelemetryFrame`, identical to the
//!   simulator's recorder, so `raceforge.sim.record.read_frames` reads car logs too)
//! - `/events`: faults, notes and runtime events
//!
//! `/ev3_raw` and `/lidar_raw` follow with the LiDAR driver.

pub mod frame;
pub mod mcap;

use mcap::McapWriter;
use rf_core::runtime::{Event, TickRecord, TickSink};
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
/// JSON Schema of `TelemetryFrame` v1, exported from `raceforge.core` (drift-checked by
/// `tests/car/test_log_schema.py`).
pub const TELEMETRY_SCHEMA: &str = include_str!("../schemas/telemetry.v1.schema.json");

const EVENT_SCHEMA: &str = r#"{"type":"object","properties":{"t":{"type":"object","properties":{"mono_ns":{"type":"integer"}}},"kind":{"type":"string"},"detail":{"type":"string"}}}"#;

enum Msg {
    Tick(Box<TickRecord>),
    Event(u64, Event),
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
        w.flush()?;
        let (tx, rx) = mpsc::sync_channel(queue);
        let thread = std::thread::spawn(move || write_loop(w, rx, tel, ev, flush_every));
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

fn write_loop(
    mut w: McapWriter<BufWriter<File>>,
    rx: Receiver<Msg>,
    tel: u16,
    ev: u16,
    flush_every: Duration,
) -> io::Result<u64> {
    let mut last_flush = Instant::now();
    let mut ev_seq = 0u32;
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
                w.message(tel, r.seq as u32, r.mono_ns, &data)?;
            }
            Some(Msg::Event(ns, e)) => {
                let data = serde_json::to_vec(&serde_json::json!({
                    "t": { "mono_ns": ns }, "kind": e.kind, "detail": e.detail
                }))
                .map_err(io::Error::other)?;
                w.message(ev, ev_seq, ns, &data)?;
                ev_seq = ev_seq.wrapping_add(1);
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
