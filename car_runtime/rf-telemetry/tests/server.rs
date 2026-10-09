//! Telemetry/teleop server over real TCP with a minimal WebSocket client.

#![allow(clippy::expect_used)] // test helpers: a failed setup should abort the test

use rf_core::hw::DriveOutput;
use rf_core::runtime::{Event, Remote, TickRecord, TickSink};
use rf_core::safety::{DeadMan, Teleop};
use rf_proto::ipc::{Command, Mode};
use rf_telemetry::{ws, TelemetryConfig, TelemetryServer};
use serde_json::Value;
use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

struct Rig {
    server: TelemetryServer,
    teleop: Arc<Mutex<DeadMan>>,
    remote: Arc<Remote>,
}

fn rig(token: Option<&str>, max_clients: usize) -> Rig {
    let teleop = Arc::new(Mutex::new(DeadMan::new(Duration::from_millis(300))));
    let remote = Arc::new(Remote::default());
    let cfg = TelemetryConfig {
        bind: "127.0.0.1:0".parse().expect("addr"),
        rate_hz: 20.0,
        token: token.map(str::to_string),
        max_clients,
    };
    let server = TelemetryServer::start(cfg, "car", Mode::Test, teleop.clone(), remote.clone())
        .expect("start");
    Rig {
        server,
        teleop,
        remote,
    }
}

/// Connect; returns the stream after a successful upgrade, or the HTTP status line.
fn connect(rig: &Rig, path: &str) -> Result<TcpStream, String> {
    let mut s = TcpStream::connect(rig.server.local_addr()).expect("connect");
    s.set_read_timeout(Some(Duration::from_secs(2)))
        .expect("timeout");
    write!(
        s,
        "GET {path} HTTP/1.1\r\nHost: car\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\
         Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n"
    )
    .expect("request");
    let mut head = Vec::new();
    let mut b = [0u8; 1];
    while !head.ends_with(b"\r\n\r\n") {
        if s.read(&mut b).expect("response") == 0 {
            break;
        }
        head.push(b[0]);
    }
    let text = String::from_utf8_lossy(&head).to_string();
    let status = text.lines().next().unwrap_or_default().to_string();
    if status.contains(" 101 ") {
        assert!(text.contains("Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo="));
        Ok(s)
    } else {
        Err(status)
    }
}

fn recv(s: &mut TcpStream) -> Value {
    let (op, payload) = ws::read_server_frame(s).expect("frame");
    assert_eq!(op, 1, "text frame expected");
    serde_json::from_slice(&payload).expect("json")
}

/// Next message of the given type (skipping telemetry etc.).
fn recv_type(s: &mut TcpStream, kind: &str) -> Value {
    loop {
        let m = recv(s);
        if m["type"] == kind {
            return m;
        }
    }
}

fn send(s: &mut TcpStream, msg: &str) {
    s.write_all(&ws::encode_client(1, msg.as_bytes(), [7, 1, 9, 3]))
        .expect("send");
}

fn record(seq: u64) -> TickRecord {
    let mut channels = BTreeMap::new();
    channels.insert(
        "state".to_string(),
        rf_proto::ipc::ChannelValue::Text("follow".into()),
    );
    TickRecord {
        mono_ns: seq * 20_000_000,
        wall_offset_ns: None,
        seq,
        mode: Mode::Test,
        state: "follow".into(),
        faults: vec![],
        out: DriveOutput {
            steering_rad: 0.1,
            speed_m_s: 0.4,
            stop: false,
            fault: false,
        },
        obs: None,
        rate_hz: 50.0,
        lateness_us: 100.0,
        tick_us: 400.0,
        deadline_misses: 0,
        channels,
    }
}

#[test]
fn hello_then_telemetry_at_the_configured_rate() {
    let r = rig(None, 4);
    let mut c = connect(&r, "/").expect("upgrade");
    let hello = recv(&mut c);
    assert_eq!(
        (hello["type"].as_str(), hello["mode"].as_str()),
        (Some("hello"), Some("test"))
    );
    // The runtime ticks at 50 Hz; clients get the latest frame at 20 Hz.
    let feeder = {
        let t = Instant::now();
        let mut seq = 0;
        let mut frames = Vec::new();
        while t.elapsed() < Duration::from_millis(1000) {
            r.server.tick(&record(seq));
            seq += 1;
            std::thread::sleep(Duration::from_millis(20));
        }
        c.set_read_timeout(Some(Duration::from_millis(200)))
            .expect("timeout");
        while let Ok((_, p)) = ws::read_server_frame(&mut c) {
            frames.push(serde_json::from_slice::<Value>(&p).expect("json"));
        }
        frames
    };
    let tel: Vec<_> = feeder.iter().filter(|m| m["type"] == "telemetry").collect();
    assert!(
        (14..=24).contains(&tel.len()),
        "{} telemetry messages in 1 s",
        tel.len()
    );
    let f = &tel.last().expect("frame")["frame"];
    assert_eq!(f["schema"], "telemetry"); // same TelemetryFrame JSON as the logs
    assert_eq!(f["state"], "follow");
    assert_eq!(f["cmd"]["speed_m_s"], 0.4);
    assert!(tel
        .windows(2)
        .all(|w| w[1]["frame"]["seq"].as_u64() > w[0]["frame"]["seq"].as_u64()));
}

#[test]
fn events_are_forwarded() {
    let r = rig(None, 4);
    let mut c = connect(&r, "/").expect("upgrade");
    recv(&mut c);
    r.server.event(
        5,
        &Event {
            t_s: 0.0,
            kind: "fault".into(),
            detail: "Deadline { seq: 3 }".into(),
        },
    );
    let e = recv_type(&mut c, "event");
    assert_eq!(
        (e["kind"].as_str(), e["t"]["mono_ns"].as_u64()),
        (Some("fault"), Some(5))
    );
}

#[test]
fn teleop_reaches_the_dead_man_and_release_hands_back() {
    let r = rig(None, 4);
    let mut c = connect(&r, "/").expect("upgrade");
    recv(&mut c);
    send(&mut c, r#"{"type":"teleop","steer":-0.2,"speed":0.3}"#);
    let t = Instant::now();
    loop {
        let state = r.teleop.lock().expect("lock").state(Instant::now());
        if state
            == Teleop::Drive(Command {
                steering_rad: -0.2,
                speed_m_s: 0.3,
            })
        {
            break;
        }
        assert!(t.elapsed() < Duration::from_secs(1), "{state:?}");
        std::thread::sleep(Duration::from_millis(5));
    }
    send(&mut c, r#"{"type":"teleop_release"}"#);
    assert_eq!(recv_type(&mut c, "ack")["ok"], true);
    assert_eq!(
        r.teleop.lock().expect("lock").state(Instant::now()),
        Teleop::Off
    );
    // Invalid teleop is acknowledged as refused, the dead-man is untouched.
    send(&mut c, r#"{"type":"teleop","steer":"left"}"#);
    let a = recv_type(&mut c, "ack");
    assert_eq!(
        (a["cmd"].as_str(), a["ok"].as_bool()),
        (Some("teleop"), Some(false))
    );
    assert_eq!(
        r.teleop.lock().expect("lock").state(Instant::now()),
        Teleop::Off
    );
}

#[test]
fn stop_note_mode_and_unknown_commands() {
    let r = rig(None, 4);
    let mut c = connect(&r, "/").expect("upgrade");
    recv(&mut c);
    assert!(!r.remote.stop_pending());
    send(&mut c, r#"{"type":"note","text":"cone moved"}"#);
    assert_eq!(recv_type(&mut c, "ack")["ok"], true);
    send(&mut c, r#"{"type":"note","text":""}"#);
    assert_eq!(recv_type(&mut c, "ack")["ok"], false);
    send(&mut c, r#"{"type":"mode","mode":"race"}"#);
    let a = recv_type(&mut c, "ack");
    assert_eq!(a["ok"], false);
    assert!(a["detail"]
        .as_str()
        .unwrap_or_default()
        .contains("radio check"));
    send(&mut c, r#"{"type":"self_destruct"}"#);
    assert_eq!(recv_type(&mut c, "ack")["detail"], "unknown command");
    send(&mut c, "not json");
    assert_eq!(recv_type(&mut c, "ack")["detail"], "not JSON");
    send(&mut c, r#"{"type":"stop","reason":"kid on track"}"#);
    assert_eq!(recv_type(&mut c, "ack")["ok"], true);
    // The runtime picks the stop up at its next tick (tested in rf-core); here: it was requested.
    assert!(r.remote.stop_pending());
}

#[test]
fn token_is_required_when_configured() {
    let r = rig(Some("a-long-random-token"), 4);
    assert_eq!(
        connect(&r, "/").err().as_deref(),
        Some("HTTP/1.1 401 Unauthorized")
    );
    assert_eq!(
        connect(&r, "/?token=wrong").err().as_deref(),
        Some("HTTP/1.1 401 Unauthorized")
    );
    let mut c = connect(&r, "/?token=a-long-random-token").expect("upgrade");
    assert_eq!(recv(&mut c)["type"], "hello");
}

#[test]
fn client_limit_and_bad_requests() {
    let r = rig(None, 1);
    let mut first = connect(&r, "/").expect("upgrade");
    recv(&mut first);
    assert_eq!(
        connect(&r, "/").err().as_deref(),
        Some("HTTP/1.1 503 Service Unavailable")
    );
    // Plain HTTP (no upgrade) is rejected.
    let mut s = TcpStream::connect(r.server.local_addr()).expect("connect");
    s.write_all(b"GET / HTTP/1.1\r\nHost: car\r\n\r\n")
        .expect("write");
    let mut resp = String::new();
    s.read_to_string(&mut resp).expect("read");
    assert!(resp.starts_with("HTTP/1.1 400"), "{resp}");
}

#[test]
fn protocol_violation_closes_the_connection() {
    let r = rig(None, 4);
    let mut c = connect(&r, "/").expect("upgrade");
    recv(&mut c);
    c.write_all(&ws::text("unmasked from a client"))
        .expect("write");
    loop {
        let (op, payload) = ws::read_server_frame(&mut c).expect("frame");
        if op == 8 {
            assert_eq!(payload, 1002u16.to_be_bytes());
            break;
        }
    }
    let t = Instant::now();
    while r.server.clients() > 0 {
        assert!(t.elapsed() < Duration::from_secs(1));
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn refuses_to_start_in_race_mode() {
    let teleop = Arc::new(Mutex::new(DeadMan::new(Duration::from_millis(300))));
    let cfg = TelemetryConfig {
        bind: "127.0.0.1:0".parse().expect("addr"),
        rate_hz: 20.0,
        token: None,
        max_clients: 4,
    };
    let err = TelemetryServer::start(cfg, "car", Mode::Race, teleop, Arc::new(Remote::default()))
        .err()
        .expect("refused");
    assert_eq!(err.kind(), std::io::ErrorKind::PermissionDenied);
}

#[test]
fn dropping_the_server_closes_clients() {
    let r = rig(None, 4);
    let mut c = connect(&r, "/").expect("upgrade");
    recv(&mut c);
    let addr = r.server.local_addr();
    drop(r);
    loop {
        match ws::read_server_frame(&mut c) {
            Ok((8, payload)) => {
                assert_eq!(payload, 1001u16.to_be_bytes());
                break;
            }
            Ok(_) => {}
            Err(_) => break, // socket closed
        }
    }
    assert!(TcpStream::connect(addr).is_err(), "no longer listening");
}

#[test]
fn radio_check_runs_on_request_without_arming() {
    let rig = rig(None, 4);
    let mut s = connect(&rig, "/").expect("upgrade");
    recv_type(&mut s, "hello");
    send(&mut s, r#"{"type":"radio_check"}"#);
    let a = recv_type(&mut s, "ack");
    assert_eq!(a["cmd"], "radio_check");
    assert_eq!(a["ok"], false); // no check injected: not available

    let blocked = "radio phy0 (wlan, phy0) is not blocked (rfkill)";
    rig.server
        .set_radio_check(Arc::new(move || vec![blocked.to_string()]));
    send(&mut s, r#"{"type":"radio_check"}"#);
    let r = recv_type(&mut s, "radio_check");
    assert_eq!(r["ok"], false);
    assert_eq!(r["violations"][0], blocked);

    rig.server.set_radio_check(Arc::new(Vec::new));
    send(&mut s, r#"{"type":"radio_check"}"#);
    assert_eq!(recv_type(&mut s, "radio_check")["ok"], true);
}
