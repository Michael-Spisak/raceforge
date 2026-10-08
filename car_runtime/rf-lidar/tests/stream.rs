//! rf-lidar fed with a synthetic LD06 stream over a socket (stands in for the UART).

#![allow(clippy::expect_used)] // test helpers: a failed setup should abort the test

use rf_lidar::{Lidar, LidarConfig};
use rf_proto::ld06::encode;
use std::f64::consts::{FRAC_PI_2, PI};
use std::io::Write;
use std::os::unix::net::UnixStream;
use std::time::{Duration, Instant};

/// One revolution of 30 packets x 12 points (360 points, 1 degree apart) starting at `start_deg`.
/// Range = 1 m everywhere except a wall 0.4 m away at 90 deg clockwise (the car's right side).
fn revolution(start_deg: u32) -> Vec<u8> {
    let mut out = Vec::new();
    for k in 0..30u32 {
        let first = (start_deg + k * 12) % 360;
        let mut pts = [(1000u16, 200u8); 12];
        for (i, p) in pts.iter_mut().enumerate() {
            let deg = (first + i as u32) % 360;
            if (85..=95).contains(&deg) {
                p.0 = 400;
            }
            if deg == 180 {
                p.0 = 0; // no return
            }
        }
        let start = (first * 100) as u16;
        let end = (((first + 11) % 360) * 100) as u16;
        out.extend(encode(3600, start, end, k as u16, &pts));
    }
    out
}

fn wait_for(lidar: &Lidar, revs: u64) {
    let t = Instant::now();
    while lidar.stats().revolutions < revs {
        assert!(t.elapsed() < Duration::from_secs(2), "{:?}", lidar.stats());
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn decodes_revolutions_and_converts_to_controller_convention() {
    let (mut uart, rx) = UnixStream::pair().expect("pair");
    let lidar = Lidar::start(Box::new(rx), LidarConfig::default());
    // Start mid-revolution (the first, partial revolution must be dropped), then 2 full ones,
    // fed in odd-sized chunks with some line noise in between.
    let mut stream = revolution(200)[..20 * 47].to_vec();
    stream.extend([0x00, 0x54, 0x99]);
    stream.extend(revolution(0));
    stream.extend(revolution(0));
    stream.extend(revolution(0)[..47].to_vec()); // wrap -> completes the last revolution
    for chunk in stream.chunks(61) {
        uart.write_all(chunk).expect("write");
    }
    wait_for(&lidar, 2);
    let stats = lidar.stats();
    assert_eq!(stats.speed_dps, 3600);
    assert_eq!(stats.bad_packets, 0);
    let (at, scan) = lidar.latest().expect("scan");
    assert!(at.elapsed() < Duration::from_secs(1));
    assert_eq!(scan.angles_rad.len(), 360);
    assert!(scan
        .angles_rad
        .iter()
        .all(|a| *a > -PI - 1e-9 && *a <= PI + 1e-9));
    // The wall at 90 deg clockwise appears on the right: about -pi/2 in the CCW convention.
    let near: Vec<f64> = scan
        .angles_rad
        .iter()
        .zip(&scan.ranges_m)
        .filter(|(_, r)| **r == Some(0.4))
        .map(|(a, _)| *a)
        .collect();
    assert_eq!(near.len(), 11);
    assert!(near.iter().all(|a| (a + FRAC_PI_2).abs() < 0.1), "{near:?}");
    assert_eq!(scan.ranges_m.iter().filter(|r| r.is_none()).count(), 1);
}

#[test]
fn mount_offset_rotates_the_scan() {
    let (mut uart, rx) = UnixStream::pair().expect("pair");
    // Sensor mounted with its zero mark pointing left (+90 deg CCW).
    let lidar = Lidar::start(
        Box::new(rx),
        LidarConfig {
            mount_offset_rad: FRAC_PI_2,
            ..Default::default()
        },
    );
    for _ in 0..3 {
        uart.write_all(&revolution(0)).expect("write");
    }
    uart.write_all(&revolution(0)[..47]).expect("write");
    wait_for(&lidar, 2);
    let (_, scan) = lidar.latest().expect("scan");
    let wall = scan
        .angles_rad
        .iter()
        .zip(&scan.ranges_m)
        .find(|(_, r)| **r == Some(0.4))
        .map(|(a, _)| *a)
        .expect("wall");
    // 90 deg clockwise of a zero mark that points left = straight ahead.
    assert!(wall.abs() < 0.1, "{wall}");
}

#[test]
fn short_revolutions_are_dropped_and_end_of_stream_is_reported() {
    let (mut uart, rx) = UnixStream::pair().expect("pair");
    let lidar = Lidar::start(
        Box::new(rx),
        LidarConfig {
            min_points: 500,
            ..Default::default()
        },
    );
    for _ in 0..3 {
        uart.write_all(&revolution(0)).expect("write");
    }
    uart.write_all(&revolution(0)[..47]).expect("write");
    drop(uart); // cable unplugged
    let t = Instant::now();
    while !lidar.stats().ended {
        assert!(t.elapsed() < Duration::from_secs(2));
        std::thread::sleep(Duration::from_millis(5));
    }
    let stats = lidar.stats();
    assert_eq!(stats.revolutions, 0);
    assert!(stats.short_revolutions >= 2, "{stats:?}");
    assert!(lidar.latest().is_none());
}

#[test]
fn stalled_scan_motor_stops_scans_and_recovers() {
    let (mut uart, rx) = UnixStream::pair().expect("pair");
    let lidar = Lidar::start(Box::new(rx), LidarConfig::default());
    for _ in 0..3 {
        uart.write_all(&revolution(0)).expect("write");
    }
    uart.write_all(&revolution(0)[..47]).expect("write");
    wait_for(&lidar, 2);
    let (scan_at, _) = lidar.latest().expect("scan");

    // The motor stalls: packets keep coming, always at the same angle. No revolution ever ends;
    // it is dropped at the point cap, and no new scan appears, so the scan goes stale and the
    // runtime's sensor policy (critical: fault, optional: degrade) takes over.
    let stuck = encode(0, 4000, 4000, 0, &[(800, 100); 12]);
    for _ in 0..400 {
        uart.write_all(&stuck).expect("write"); // 4800 points
    }
    let t = Instant::now();
    while lidar.stats().dropped_revolutions < 2 {
        assert!(t.elapsed() < Duration::from_secs(2), "{:?}", lidar.stats());
        std::thread::sleep(Duration::from_millis(5));
    }
    assert_eq!(lidar.stats().revolutions, 2);
    assert_eq!(lidar.latest().map(|(at, _)| at), Some(scan_at));

    // The motor recovers: the partial revolution is discarded, full ones are reported again.
    for _ in 0..2 {
        uart.write_all(&revolution(0)).expect("write");
    }
    uart.write_all(&revolution(0)[..47]).expect("write");
    wait_for(&lidar, 3);
    let (at, scan) = lidar.latest().expect("scan");
    assert!(at > scan_at);
    assert_eq!(scan.angles_rad.len(), 360);
}
