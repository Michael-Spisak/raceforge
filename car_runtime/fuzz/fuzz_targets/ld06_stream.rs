//! LD06/LD19 LiDAR UART stream: the streaming parser, the revolution builder and the conversion
//! to the car frame never panic, however the bytes are split into reads, keep their
//! documented ranges, and never hold more than one capped revolution in memory.

#![no_main]

use libfuzzer_sys::fuzz_target;
use rf_proto::ld06::{Parser, RevolutionBuilder, MAX_REVOLUTION_POINTS};
use std::f64::consts::PI;

fuzz_target!(|data: &[u8]| {
    // The first byte picks the read size (1..=64), the rest is the byte stream.
    let Some((&size, stream)) = data.split_first() else {
        return;
    };
    let mut parser = Parser::new();
    let mut revs = RevolutionBuilder::new();
    for chunk in stream.chunks(usize::from(size % 64) + 1) {
        for packet in parser.push(chunk) {
            assert!(
                packet.points.iter().all(|p| p.angle_cdeg < 36000),
                "{packet:?}"
            );
            let done = revs.push(&packet);
            assert!(revs.pending_points() <= MAX_REVOLUTION_POINTS);
            if let Some(rev) = done {
                assert!(rev.points.len() <= MAX_REVOLUTION_POINTS);
                let (angles, ranges) = rev.to_ccw_rad(0.3);
                assert_eq!(angles.len(), ranges.len());
                assert!(angles.iter().all(|a| *a > -PI && *a <= PI), "{angles:?}");
                assert!(ranges.iter().flatten().all(|r| r.is_finite() && *r > 0.0));
            }
        }
    }
});
