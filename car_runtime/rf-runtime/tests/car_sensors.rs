//! Sensor policy for the LiDAR (spec 0005): critical -> link lost (fault), optional -> degraded.

#![allow(clippy::expect_used)] // test helpers: a failed setup should abort the test

use rf_core::hw::{MockSensors, SensorSnapshot, Sensors};
use rf_lidar::{Lidar, LidarConfig};
use rf_proto::ld06::encode;
use rf_runtime::manifest::SensorPolicy;
use rf_runtime::sensors::{CarSensors, LidarSource};
use std::io::Write;
use std::os::unix::net::UnixStream;
use std::sync::Arc;
use std::time::{Duration, Instant};

fn revolution() -> Vec<u8> {
    (0..30u32)
        .flat_map(|k| {
            let first = k * 12;
            encode(
                3600,
                (first * 100) as u16,
                (((first + 11) % 360) * 100) as u16,
                0,
                &[(800, 200); 12],
            )
        })
        .collect()
}

fn sensors(policy: SensorPolicy) -> (CarSensors<MockSensors>, UnixStream) {
    let (uart, rx) = UnixStream::pair().expect("pair");
    let lidar = Arc::new(Lidar::start(Box::new(rx), LidarConfig::default()));
    let mut ev3 = SensorSnapshot::default();
    ev3.ultrasonic_m.insert("front".into(), Some(1.0));
    let car = CarSensors {
        ev3: Arc::new(MockSensors::new(ev3)),
        lidar: Some(LidarSource {
            lidar,
            policy,
            timeout: Duration::from_millis(150),
        }),
    };
    (car, uart)
}

fn feed_until_scan(car: &CarSensors<MockSensors>, uart: &mut UnixStream) {
    for _ in 0..3 {
        uart.write_all(&revolution()).expect("write");
    }
    uart.write_all(&revolution()[..47]).expect("write");
    let t = Instant::now();
    while car.snapshot().lidar.is_none() {
        assert!(t.elapsed() < Duration::from_secs(2));
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn critical_lidar_missing_or_stale_is_link_lost() {
    let (car, mut uart) = sensors(SensorPolicy::Critical);
    let s = car.snapshot();
    assert!(
        s.link_lost
            .as_deref()
            .is_some_and(|w| w.contains("no scan yet")),
        "{s:?}"
    );
    feed_until_scan(&car, &mut uart);
    let s = car.snapshot();
    assert!(s.link_lost.is_none() && s.lidar_at.is_some());
    assert_eq!(s.lidar.expect("scan").ranges_m[0], Some(0.8));
    assert_eq!(s.ultrasonic_m["front"], Some(1.0)); // EV3 data kept
    std::thread::sleep(Duration::from_millis(200)); // LiDAR stops sending
    let s = car.snapshot();
    assert!(s.lidar.is_none());
    assert!(
        s.link_lost
            .as_deref()
            .is_some_and(|w| w.contains("no scan for")),
        "{s:?}"
    );
}

#[test]
fn optional_lidar_missing_is_degraded_not_lost() {
    let (car, mut uart) = sensors(SensorPolicy::Optional);
    let s = car.snapshot();
    assert_eq!((s.link_lost, s.degraded), (None, vec!["lidar".to_string()]));
    feed_until_scan(&car, &mut uart);
    assert!(car.snapshot().degraded.is_empty());
}

#[test]
fn ev3_link_lost_wins_over_lidar() {
    let (car, _uart) = sensors(SensorPolicy::Critical);
    let lost = CarSensors {
        ev3: Arc::new(MockSensors::new(SensorSnapshot {
            link_lost: Some("ev3".into()),
            ..Default::default()
        })),
        lidar: car.lidar,
    };
    assert_eq!(lost.snapshot().link_lost.as_deref(), Some("ev3"));
}
