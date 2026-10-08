//! EV3 link over a serial byte stream (spec 0011 AC2): the board side (`Ev3Link` + `SerialTransport`)
//! against a mock EV3RT brick that speaks COBS-framed rf-proto frames on a Unix socket pair.

#![allow(clippy::expect_used)] // test helpers: a failed setup should abort the test

use rf_core::hw::{Actuators, DriveOutput, Sensors};
use rf_ev3::{Ev3Config, Ev3Link, SerialTransport, Transport};
use rf_proto::cobs::{self, StreamDecoder};
use rf_proto::ev3::{CommandFrame, Motor, SensorFrame};
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::time::{Duration, Instant};

fn pair() -> (SerialTransport, UnixStream) {
    let (board, brick) = UnixStream::pair().expect("socket pair");
    let reader = board.try_clone().expect("clone");
    (
        SerialTransport::from_stream(Box::new(reader), Box::new(board)).expect("transport"),
        brick,
    )
}

#[test]
fn frames_cross_the_stream_both_ways_with_resync() {
    let (t, mut brick) = pair();
    let cmd = CommandFrame {
        seq: 3,
        drive_speed_cps: 360,
        ..CommandFrame::default()
    };
    t.send(&cmd.encode()).expect("send");
    let mut buf = [0u8; 64];
    let n = brick.read(&mut buf).expect("read");
    let packets = StreamDecoder::new(128).push(&buf[..n]);
    assert_eq!(CommandFrame::decode(&packets[0]).expect("frame").seq, 3);

    // noise (e.g. an EV3RT log line on port 1), then a valid sensor frame
    let s = SensorFrame {
        seq: 9,
        ack_seq: 3,
        ..SensorFrame::default()
    };
    brick.write_all(b"[LOG] hello\n").expect("noise");
    brick.write_all(&[0]).expect("delimiter");
    brick.write_all(&cobs::frame(&s.encode())).expect("frame");
    let mut rbuf = [0u8; 128];
    let mut got = None;
    let end = Instant::now() + Duration::from_secs(2);
    while got.is_none() && Instant::now() < end {
        if let Some(n) = t.recv(&mut rbuf, Duration::from_millis(50)).expect("recv") {
            if let Ok(f) = SensorFrame::decode(&rbuf[..n]) {
                got = Some(f);
            }
        }
    }
    assert_eq!(got.expect("sensor frame").ack_seq, 3);
    assert_eq!(
        t.recv(&mut rbuf, Duration::from_millis(30))
            .expect("timeout"),
        None
    );
}

#[test]
fn closed_stream_is_an_error() {
    let (t, brick) = pair();
    drop(brick);
    let mut buf = [0u8; 64];
    let end = Instant::now() + Duration::from_secs(2);
    loop {
        match t.recv(&mut buf, Duration::from_millis(20)) {
            Err(_) => break,
            Ok(_) => assert!(Instant::now() < end, "no error after the brick closed"),
        }
    }
}

/// The real link logic on top: commands go out at 100 Hz, sensor frames update the snapshot.
#[test]
fn ev3_link_over_serial() {
    let (t, brick) = pair();
    let link = Ev3Link::start(Ev3Config::default(), Box::new(t));
    let mut reader = brick.try_clone().expect("clone");
    let mut writer = brick;
    let echo = std::thread::spawn(move || {
        let mut d = StreamDecoder::new(128);
        let mut buf = [0u8; 256];
        let mut seen = 0u32;
        let end = Instant::now() + Duration::from_millis(600);
        while Instant::now() < end {
            let n = reader.read(&mut buf).unwrap_or(0);
            if n == 0 {
                break;
            }
            for p in d.push(&buf[..n]) {
                let c = CommandFrame::decode(&p).expect("command");
                seen += 1;
                let mut s = SensorFrame {
                    seq: seen,
                    ack_seq: c.seq,
                    ..SensorFrame::default()
                };
                s.motors[1] = Motor {
                    tacho: 720,
                    speed_cps: 360,
                };
                s.flags = rf_proto::ev3::status_flags::LINK_OK;
                if writer.write_all(&cobs::frame(&s.encode())).is_err() {
                    return seen;
                }
            }
        }
        seen
    });
    let end = Instant::now() + Duration::from_secs(2);
    while link.snapshot().link_lost.is_some() {
        assert!(Instant::now() < end, "link never came up");
        std::thread::sleep(Duration::from_millis(10));
    }
    Actuators::send(&link, DriveOutput::default());
    std::thread::sleep(Duration::from_millis(300));
    let snap = link.snapshot();
    assert!(snap.link_lost.is_none());
    assert!(snap.speed_m_s.is_some_and(|v| v > 0.0), "{snap:?}");
    drop(link);
    let seen = echo.join().expect("echo thread");
    assert!(seen >= 20, "only {seen} command frames in ~0.5 s"); // ~100 Hz keep-alive
}
