//! Live telemetry and teleop over WebSocket (spec 0005 "Logging & telemetry", test mode only).
//!
//! Server -> client (JSON text messages):
//! - `{"type":"hello","car":..,"mode":"test","rate_hz":..}` once after connecting
//! - `{"type":"telemetry","frame":{TelemetryFrame}}` at `rate_hz` (same JSON as `/telemetry` logs)
//! - `{"type":"event","t":{"mono_ns":..},"kind":..,"detail":..}` (faults, notes, ...)
//! - `{"type":"ack","cmd":..,"ok":..,"detail":..}` for every command except accepted teleop
//!
//! Client -> server:
//! - `{"type":"stop","reason"?:..}`: operator stop, latched like a fault (runtime `Remote`)
//! - `{"type":"teleop","steer":rad,"speed":m_s}`: drives while refreshed every < 300 ms (dead-man,
//!   speed limits and sanitizing applied by the runtime); `{"type":"teleop_release"}` hands back
//!   to the controller
//! - `{"type":"note","text":..}`: note in the run log
//! - `{"type":"mode",..}`: refused; the mode comes from the bundle (race mode needs the radio check)
//! - `{"type":"radio_check"}` (spec 0030): runs the race-mode radio check now, without arming, and
//!   answers `{"type":"radio_check","ok":..,"violations":[..]}` so the team can fix radios first
//!
//! Access: when a token is configured, clients connect to `ws://car:port/?token=<token>`.
//! The server refuses to start in race mode; `rf-runtime` does not even try.

pub mod ws;

use rf_core::runtime::{Event, Remote, TickRecord, TickSink};
use rf_core::safety::DeadMan;
use rf_proto::ipc::{Command, Mode};
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::io::{self, ErrorKind, Write};
use std::net::{Shutdown, SocketAddr, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};
use ws::{Frame, FrameError};

#[derive(Debug, Clone, PartialEq)]
pub struct TelemetryConfig {
    pub bind: SocketAddr,
    pub rate_hz: f64,
    /// Required in the connection URL (`?token=`) when set.
    pub token: Option<String>,
    pub max_clients: usize,
}

const MAX_NOTE: usize = 2000;
const MAX_QUEUED_EVENTS: usize = 200;
const WRITE_TIMEOUT: Duration = Duration::from_millis(200);

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(PoisonError::into_inner)
}

struct Client {
    id: u64,
    stream: Mutex<TcpStream>,
}

impl Client {
    fn send(&self, frame: &[u8]) -> io::Result<()> {
        lock(&self.stream).write_all(frame)
    }
}

/// The race-mode radio check (spec 0005 AC5), injected by `rf-runtime`: one line per violation.
pub type RadioCheck = Arc<dyn Fn() -> Vec<String> + Send + Sync>;

struct Shared {
    radio_check: Mutex<Option<RadioCheck>>,
    cfg: TelemetryConfig,
    car: String,
    mode: Mode,
    teleop: Arc<Mutex<DeadMan>>,
    remote: Arc<Remote>,
    latest: Mutex<Option<TickRecord>>,
    events: Mutex<VecDeque<Value>>,
    clients: Mutex<Vec<Arc<Client>>>,
    next_id: AtomicU64,
    stop: AtomicBool,
}

pub struct TelemetryServer {
    shared: Arc<Shared>,
    addr: SocketAddr,
    threads: Vec<JoinHandle<()>>,
}

fn token_ok(expected: Option<&str>, given: Option<&str>) -> bool {
    let Some(expected) = expected else {
        return true;
    };
    let given = given.unwrap_or_default().as_bytes();
    let expected = expected.as_bytes();
    // Compare every byte so the time does not depend on where the first difference is.
    let mut diff = given.len() ^ expected.len();
    for (i, e) in expected.iter().enumerate() {
        diff |= usize::from(e ^ given.get(i).copied().unwrap_or(0));
    }
    diff == 0
}

fn ack(cmd: &str, ok: bool, detail: &str) -> String {
    json!({ "type": "ack", "cmd": cmd, "ok": ok, "detail": detail }).to_string()
}

impl Shared {
    /// Handle one command; returns the ack to send, if any.
    fn command(&self, text: &str) -> Option<String> {
        let Ok(v) = serde_json::from_str::<Value>(text) else {
            return Some(ack("?", false, "not JSON"));
        };
        let kind = v["type"].as_str().unwrap_or("?");
        match kind {
            "stop" => {
                let reason = v["reason"].as_str().unwrap_or("operator stop via telemetry");
                self.remote.request_stop(&reason.chars().take(200).collect::<String>());
                Some(ack(kind, true, "stopping; resume needs a restart or new deploy"))
            }
            "teleop" => {
                let (Some(steer), Some(speed)) = (v["steer"].as_f64(), v["speed"].as_f64()) else {
                    return Some(ack(kind, false, "steer and speed (numbers) required"));
                };
                if !(steer.is_finite() && speed.is_finite()) {
                    return Some(ack(kind, false, "non-finite value"));
                }
                let cmd = Command { steering_rad: steer, speed_m_s: speed };
                let accepted = lock(&self.teleop).update(Instant::now(), cmd, self.mode);
                // Accepted teleop is not acknowledged (it arrives many times a second).
                (!accepted).then(|| ack(kind, false, "teleop refused in race mode"))
            }
            "teleop_release" => {
                lock(&self.teleop).release();
                Some(ack(kind, true, "controller drives again"))
            }
            "note" => match v["text"].as_str().map(str::trim) {
                Some(t) if !t.is_empty() && t.len() <= MAX_NOTE => {
                    self.remote.note(t);
                    Some(ack(kind, true, ""))
                }
                _ => Some(ack(kind, false, "text (1-2000 characters) required")),
            },
            "radio_check" => {
                let check = lock(&self.radio_check).clone();
                Some(match check {
                    Some(f) => {
                        let violations = f();
                        let ok = violations.is_empty();
                        json!({ "type": "radio_check", "ok": ok, "violations": violations })
                        .to_string()
                    }
                    None => ack(kind, false, "radio check not available on this runtime"),
                })
            }
            "mode" => Some(ack(
                kind,
                false,
                "the mode comes from the deployed bundle; race mode arms only after the radio check",
            )),
            _ => Some(ack(kind, false, "unknown command")),
        }
    }

    fn broadcast(&self, frame: &[u8]) {
        let clients: Vec<Arc<Client>> = lock(&self.clients).clone();
        for c in clients {
            if c.send(frame).is_err() {
                self.drop_client(c.id);
            }
        }
    }

    fn drop_client(&self, id: u64) {
        let mut clients = lock(&self.clients);
        if let Some(i) = clients.iter().position(|c| c.id == id) {
            let c = clients.remove(i);
            let _ = lock(&c.stream).shutdown(Shutdown::Both);
        }
    }

    fn handle(self: &Arc<Self>, mut stream: TcpStream) {
        let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
        let _ = stream.set_write_timeout(Some(WRITE_TIMEOUT));
        let req = match ws::read_request(&mut stream) {
            Ok(r) => r,
            Err(status) => {
                let _ = ws::write_reject(&mut stream, status);
                return;
            }
        };
        if !token_ok(self.cfg.token.as_deref(), req.query("token")) {
            let _ = ws::write_reject(&mut stream, 401);
            return;
        }
        if lock(&self.clients).len() >= self.cfg.max_clients {
            let _ = ws::write_reject(&mut stream, 503);
            return;
        }
        let Ok(writer) = stream.try_clone() else {
            return;
        };
        if ws::write_accept(&mut stream, &req).is_err() {
            return;
        }
        let client = Arc::new(Client {
            id: self.next_id.fetch_add(1, Ordering::Relaxed),
            stream: Mutex::new(writer),
        });
        let hello = json!({
            "type": "hello", "car": self.car, "mode": "test", "rate_hz": self.cfg.rate_hz,
        });
        if client.send(&ws::text(&hello.to_string())).is_err() {
            return;
        }
        lock(&self.clients).push(client.clone());
        // Block on reads from here on: a timeout in the middle of a frame would desynchronize the
        // stream. Shutdown and dropped clients close the socket, which ends the read.
        let _ = stream.set_read_timeout(None);
        while !self.stop.load(Ordering::Acquire) {
            match ws::read_frame(&mut stream) {
                Ok(Frame::Text(t)) => {
                    if let Some(a) = self.command(&t) {
                        if client.send(&ws::text(&a)).is_err() {
                            break;
                        }
                    }
                }
                Ok(Frame::Ping(p)) => {
                    if client.send(&ws::encode(10, &p)).is_err() {
                        break;
                    }
                }
                Ok(Frame::Pong(_)) => {}
                Ok(Frame::Close) => {
                    let _ = client.send(&ws::close(1000));
                    break;
                }
                Err(FrameError::Protocol(code)) => {
                    let _ = client.send(&ws::close(code));
                    break;
                }
                Err(FrameError::Io(_)) => break,
            }
        }
        self.drop_client(client.id);
    }
}

impl TelemetryServer {
    /// Bind and start serving. Refuses race mode (spec 0005 AC5: no listening socket in race).
    pub fn start(
        cfg: TelemetryConfig,
        car: &str,
        mode: Mode,
        teleop: Arc<Mutex<DeadMan>>,
        remote: Arc<Remote>,
    ) -> io::Result<Self> {
        if mode == Mode::Race {
            return Err(io::Error::new(
                ErrorKind::PermissionDenied,
                "telemetry server is test mode only",
            ));
        }
        let listener = TcpListener::bind(cfg.bind)?;
        listener.set_nonblocking(true)?;
        let addr = listener.local_addr()?;
        let period = Duration::from_secs_f64(1.0 / cfg.rate_hz.clamp(1.0, 50.0));
        let shared = Arc::new(Shared {
            radio_check: Mutex::new(None),
            cfg,
            car: car.to_string(),
            mode,
            teleop,
            remote,
            latest: Mutex::new(None),
            events: Mutex::new(VecDeque::new()),
            clients: Mutex::new(Vec::new()),
            next_id: AtomicU64::new(1),
            stop: AtomicBool::new(false),
        });
        let accept = {
            let s = shared.clone();
            std::thread::spawn(move || {
                while !s.stop.load(Ordering::Acquire) {
                    match listener.accept() {
                        Ok((stream, _)) => {
                            let _ = stream.set_nonblocking(false);
                            let s2 = s.clone();
                            std::thread::spawn(move || s2.handle(stream));
                        }
                        Err(e) if e.kind() == ErrorKind::WouldBlock => {
                            std::thread::sleep(Duration::from_millis(20))
                        }
                        Err(_) => std::thread::sleep(Duration::from_millis(100)),
                    }
                }
            })
        };
        let broadcaster = {
            let s = shared.clone();
            std::thread::spawn(move || {
                let mut last_seq = None;
                while !s.stop.load(Ordering::Acquire) {
                    std::thread::sleep(period);
                    let events: Vec<Value> = lock(&s.events).drain(..).collect();
                    for e in events {
                        s.broadcast(&ws::text(&e.to_string()));
                    }
                    let rec = lock(&s.latest).clone();
                    if let Some(rec) = rec.filter(|r| Some(r.seq) != last_seq) {
                        last_seq = Some(rec.seq);
                        let msg = json!({ "type": "telemetry", "frame": rf_log::frame::telemetry_frame(&rec) });
                        s.broadcast(&ws::text(&msg.to_string()));
                    }
                }
            })
        };
        Ok(Self {
            shared,
            addr,
            threads: vec![accept, broadcaster],
        })
    }

    /// Lets clients run the race-mode radio check from test mode (`radio_check` command).
    pub fn set_radio_check(&self, check: RadioCheck) {
        *lock(&self.shared.radio_check) = Some(check);
    }

    pub fn local_addr(&self) -> SocketAddr {
        self.addr
    }

    pub fn clients(&self) -> usize {
        lock(&self.shared.clients).len()
    }
}

impl TickSink for TelemetryServer {
    fn tick(&self, rec: &TickRecord) {
        *lock(&self.shared.latest) = Some(rec.clone());
    }

    fn event(&self, mono_ns: u64, e: &Event) {
        let mut q = lock(&self.shared.events);
        if q.len() >= MAX_QUEUED_EVENTS {
            q.pop_front();
        }
        q.push_back(json!({
            "type": "event", "t": { "mono_ns": mono_ns }, "kind": e.kind, "detail": e.detail,
        }));
    }
}

impl Drop for TelemetryServer {
    fn drop(&mut self) {
        self.shared.stop.store(true, Ordering::Release);
        for c in lock(&self.shared.clients).drain(..) {
            let _ = c.send(&ws::close(1001));
            let _ = lock(&c.stream).shutdown(Shutdown::Both);
        }
        for t in self.threads.drain(..) {
            let _ = t.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn token_check() {
        assert!(token_ok(None, None));
        assert!(token_ok(Some("secret-token-123"), Some("secret-token-123")));
        assert!(!token_ok(Some("secret-token-123"), Some("secret-token-12")));
        assert!(!token_ok(
            Some("secret-token-123"),
            Some("secret-token-1234")
        ));
        assert!(!token_ok(Some("secret-token-123"), None));
        assert!(!token_ok(Some("a"), Some("")));
    }
}
