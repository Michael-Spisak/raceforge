//! Streaming parser for LD06/LD19 2D LiDAR packets (230400 baud UART, 47-byte packets).
//!
//! ```text
//! header 0x54 | ver_len 0x2C | speed_dps u16 | start_angle_cdeg u16
//! | 12 x (distance_mm u16, intensity u8) | end_angle_cdeg u16 | timestamp_ms u16 | crc8
//! ```
//!
//! The sensor spins clockwise (seen from above) and reports angles clockwise from its zero mark.
//! [`Revolution::to_ccw_rad`] converts to the controller convention (counter-clockwise from the
//! car's forward axis, radians) using the mounting offset from the car config.

use crate::crc::crc8_ld06;

pub const HEADER: u8 = 0x54;
pub const VER_LEN: u8 = 0x2C;
pub const POINTS_PER_PACKET: usize = 12;
pub const PACKET_LEN: usize = 47;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Point {
    /// Clockwise angle from the sensor's zero mark, centi-degrees in `0..36000`.
    pub angle_cdeg: u16,
    /// Distance in millimetres; 0 means no return.
    pub distance_mm: u16,
    pub intensity: u8,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Packet {
    pub speed_dps: u16,
    pub start_angle_cdeg: u16,
    pub end_angle_cdeg: u16,
    pub timestamp_ms: u16,
    pub points: [Point; POINTS_PER_PACKET],
}

fn u16_at(d: &[u8], i: usize) -> u16 {
    u16::from_le_bytes([d[i], d[i + 1]])
}

impl Packet {
    /// Decode one complete packet; `None` on wrong header, length or CRC.
    pub fn decode(d: &[u8]) -> Option<Self> {
        if d.len() < PACKET_LEN || d[0] != HEADER || d[1] != VER_LEN {
            return None;
        }
        if crc8_ld06(&d[..PACKET_LEN - 1]) != d[PACKET_LEN - 1] {
            return None;
        }
        let start = u16_at(d, 4);
        let end = u16_at(d, 42);
        if start >= 36000 || end >= 36000 {
            return None;
        }
        // Angles of the 12 points are linearly interpolated between start and end (with wrap).
        let span = (u32::from(end) + 36000 - u32::from(start)) % 36000;
        let last = (POINTS_PER_PACKET - 1) as u32;
        let mut points = [Point {
            angle_cdeg: 0,
            distance_mm: 0,
            intensity: 0,
        }; POINTS_PER_PACKET];
        for (i, p) in points.iter_mut().enumerate() {
            let off = 6 + 3 * i;
            let angle = (u32::from(start) + span * i as u32 / last) % 36000;
            *p = Point {
                angle_cdeg: angle as u16,
                distance_mm: u16_at(d, off),
                intensity: d[off + 2],
            };
        }
        Some(Self {
            speed_dps: u16_at(d, 2),
            start_angle_cdeg: start,
            end_angle_cdeg: end,
            timestamp_ms: u16_at(d, 44),
            points,
        })
    }
}

/// Byte-stream parser: feed arbitrary chunks, get complete packets; resynchronises on garbage.
#[derive(Debug, Default)]
pub struct Parser {
    buf: Vec<u8>,
    /// Packets dropped because of a CRC/format error (for link statistics).
    pub dropped: u64,
}

impl Parser {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn push(&mut self, data: &[u8]) -> Vec<Packet> {
        self.buf.extend_from_slice(data);
        let mut out = Vec::new();
        let mut i = 0;
        while i + 1 < self.buf.len() {
            if self.buf[i] != HEADER || self.buf[i + 1] != VER_LEN {
                i += 1;
                continue;
            }
            if i + PACKET_LEN > self.buf.len() {
                break;
            }
            if let Some(p) = Packet::decode(&self.buf[i..i + PACKET_LEN]) {
                out.push(p);
                i += PACKET_LEN;
            } else {
                self.dropped += 1;
                i += 1;
            }
        }
        self.buf.drain(..i);
        out
    }
}

/// One full 360° revolution of points.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Revolution {
    pub points: Vec<Point>,
    /// Timestamp (sensor ms, wrapping) of the packet that completed the revolution.
    pub timestamp_ms: u16,
}

impl Revolution {
    /// Angles counter-clockwise from the car's forward axis in `(-pi, pi]` and ranges in metres.
    ///
    /// `mount_offset_rad` is the counter-clockwise angle of the sensor's zero mark relative to
    /// the car's forward axis. A distance of 0 becomes `None`.
    pub fn to_ccw_rad(&self, mount_offset_rad: f64) -> (Vec<f64>, Vec<Option<f64>>) {
        use std::f64::consts::{PI, TAU};
        let mut angles = Vec::with_capacity(self.points.len());
        let mut ranges = Vec::with_capacity(self.points.len());
        for p in &self.points {
            let cw = f64::from(p.angle_cdeg).to_radians() / 100.0;
            let mut a = (mount_offset_rad - cw).rem_euclid(TAU);
            if a > PI {
                a -= TAU;
            }
            angles.push(a);
            ranges.push((p.distance_mm != 0).then(|| f64::from(p.distance_mm) / 1000.0));
        }
        (angles, ranges)
    }
}

/// Most points one revolution may collect. A real one has about 450 (4500 points/s at 10 Hz) and
/// about 900 at the slowest scan rate; more means the angle is not advancing (e.g. a stalled scan
/// motor), so the revolution is dropped instead of growing without bound.
pub const MAX_REVOLUTION_POINTS: usize = 2000;

/// Collects packets into revolutions; a revolution ends when the angle wraps past zero.
#[derive(Debug, Default)]
pub struct RevolutionBuilder {
    current: Vec<Point>,
    last_angle: Option<u16>,
    /// After a dropped revolution the next one starts mid-way: discard it as well.
    resync: bool,
    /// Revolutions dropped because they exceeded [`MAX_REVOLUTION_POINTS`].
    pub dropped: u64,
}

impl RevolutionBuilder {
    pub fn new() -> Self {
        Self::default()
    }

    /// Points collected for the revolution in progress.
    pub fn pending_points(&self) -> usize {
        self.current.len()
    }

    pub fn push(&mut self, packet: &Packet) -> Option<Revolution> {
        let mut done = None;
        for p in &packet.points {
            if let Some(last) = self.last_angle {
                if p.angle_cdeg < last && !self.current.is_empty() {
                    let points = std::mem::take(&mut self.current);
                    if !std::mem::take(&mut self.resync) {
                        done = Some(Revolution {
                            points,
                            timestamp_ms: packet.timestamp_ms,
                        });
                    }
                }
            }
            if self.current.len() == MAX_REVOLUTION_POINTS {
                self.current.clear();
                self.dropped += 1;
                self.resync = true;
            }
            self.last_angle = Some(p.angle_cdeg);
            self.current.push(*p);
        }
        done
    }
}

/// Encode a packet (used by tests, the mock LiDAR and fuzz seeds).
pub fn encode(
    speed_dps: u16,
    start_cdeg: u16,
    end_cdeg: u16,
    ts_ms: u16,
    pts: &[(u16, u8); 12],
) -> Vec<u8> {
    let mut v = Vec::with_capacity(PACKET_LEN);
    v.push(HEADER);
    v.push(VER_LEN);
    v.extend_from_slice(&speed_dps.to_le_bytes());
    v.extend_from_slice(&start_cdeg.to_le_bytes());
    for &(dist, intensity) in pts {
        v.extend_from_slice(&dist.to_le_bytes());
        v.push(intensity);
    }
    v.extend_from_slice(&end_cdeg.to_le_bytes());
    v.extend_from_slice(&ts_ms.to_le_bytes());
    v.push(crc8_ld06(&v));
    v
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    /// A real packet captured from an LD06 (from the vendor's protocol manual example).
    const SAMPLE: [u8; PACKET_LEN] = [
        0x54, 0x2C, 0x68, 0x08, 0xAB, 0x7E, 0xE0, 0x00, 0xE4, 0xDC, 0x00, 0xE2, 0xD9, 0x00, 0xE5,
        0xD5, 0x00, 0xE3, 0xD3, 0x00, 0xE4, 0xD0, 0x00, 0xE9, 0xCD, 0x00, 0xE4, 0xCA, 0x00, 0xE2,
        0xC7, 0x00, 0xE9, 0xC5, 0x00, 0xE5, 0xC2, 0x00, 0xE5, 0xC0, 0x00, 0xE5, 0xBE, 0x82, 0x3A,
        0x1A, 0x50,
    ];

    #[test]
    fn decodes_sample_packet() {
        let p = Packet::decode(&SAMPLE).expect("sample decodes");
        assert_eq!(p.speed_dps, 2152);
        assert_eq!(p.start_angle_cdeg, 32427);
        assert_eq!(p.end_angle_cdeg, 33470);
        assert_eq!(p.timestamp_ms, 6714);
        assert_eq!(
            p.points[0],
            Point {
                angle_cdeg: 32427,
                distance_mm: 224,
                intensity: 228
            }
        );
        assert_eq!(p.points[11].angle_cdeg, 33470);
        assert_eq!(p.points[11].distance_mm, 192);
        // Evenly spaced in between.
        assert_eq!(p.points[1].angle_cdeg, 32427 + (33470 - 32427) / 11);
    }

    #[test]
    fn wraparound_interpolation() {
        let bytes = encode(3600, 35800, 200, 0, &[(1000, 200); 12]);
        let p = Packet::decode(&bytes).expect("decodes");
        assert_eq!(p.points[0].angle_cdeg, 35800);
        assert_eq!(p.points[11].angle_cdeg, 200);
        assert!(p.points.iter().all(|pt| pt.angle_cdeg < 36000));
    }

    #[test]
    fn parser_resyncs_after_garbage_and_bad_crc() {
        let mut stream = vec![0x00, 0x54, 0x11, 0xFF];
        let mut bad = SAMPLE;
        bad[10] ^= 0x01;
        stream.extend_from_slice(&bad);
        stream.extend_from_slice(&SAMPLE);
        let mut parser = Parser::new();
        // Feed in odd-sized chunks.
        let mut got = Vec::new();
        for chunk in stream.chunks(5) {
            got.extend(parser.push(chunk));
        }
        assert_eq!(got.len(), 1);
        assert_eq!(parser.dropped, 1);
    }

    #[test]
    fn revolution_and_ccw_conversion() {
        let mut b = RevolutionBuilder::new();
        let mut revs = Vec::new();
        // 30 packets of 12 points spanning 0..360 deg twice.
        for k in 0..60u32 {
            let start = (k * 1200 % 36000) as u16;
            let end = ((k * 1200 + 1100) % 36000) as u16;
            let bytes = encode(3600, start, end, k as u16, &[(500, 100); 12]);
            let p = Packet::decode(&bytes).expect("decodes");
            revs.extend(b.push(&p));
        }
        assert_eq!(revs.len(), 1);
        assert_eq!(revs[0].points.len(), 30 * 12);
        let (a, r) = revs[0].to_ccw_rad(0.0);
        // Sensor 0 deg = forward; 90 deg clockwise = right = -pi/2 in CCW convention.
        let i90 = revs[0]
            .points
            .iter()
            .position(|p| p.angle_cdeg == 9000)
            .expect("90 deg");
        assert!((a[i90] + std::f64::consts::FRAC_PI_2).abs() < 1e-9);
        assert_eq!(r[0], Some(0.5));
        assert!(a
            .iter()
            .all(|x| *x > -std::f64::consts::PI - 1e-12 && *x <= std::f64::consts::PI));
    }

    #[test]
    fn stuck_angle_is_bounded_then_resyncs() {
        // A stalled scan motor keeps reporting the same angle: the angle never wraps, so no
        // revolution ends. Memory must stay bounded and the partial data is dropped.
        let stuck = Packet::decode(&encode(0, 1000, 1000, 0, &[(500, 100); 12])).expect("decodes");
        let mut b = RevolutionBuilder::new();
        for _ in 0..10_000 {
            assert_eq!(b.push(&stuck), None);
            assert!(b.pending_points() <= MAX_REVOLUTION_POINTS);
        }
        assert_eq!(b.dropped, 10_000 * 12 / (MAX_REVOLUTION_POINTS + 1) as u64);

        // The motor recovers: the revolution in progress started mid-way and is discarded; the
        // next full revolution is reported as usual.
        let mut revs = Vec::new();
        for k in 0..60u32 {
            let start = (k * 1200 % 36000) as u16;
            let end = ((k * 1200 + 1100) % 36000) as u16;
            let p = Packet::decode(&encode(3600, start, end, k as u16, &[(500, 100); 12]))
                .expect("decodes");
            revs.extend(b.push(&p));
        }
        assert_eq!(revs.len(), 1);
        assert_eq!(revs[0].points.len(), 30 * 12);
        assert_eq!(revs[0].points[0].angle_cdeg, 0);
    }

    proptest! {
        #[test]
        fn encode_decode_roundtrip(speed: u16, start in 0u16..36000, end in 0u16..36000, ts: u16,
                                   pts in proptest::array::uniform12((any::<u16>(), any::<u8>()))) {
            let p = Packet::decode(&encode(speed, start, end, ts, &pts)).expect("decodes");
            prop_assert_eq!(p.speed_dps, speed);
            prop_assert_eq!(p.start_angle_cdeg, start);
            prop_assert_eq!(p.timestamp_ms, ts);
            for (pt, (d, i)) in p.points.iter().zip(pts) {
                prop_assert_eq!(pt.distance_mm, d);
                prop_assert_eq!(pt.intensity, i);
            }
        }

        #[test]
        fn random_stream_never_panics(data in proptest::collection::vec(any::<u8>(), 0..512)) {
            let mut parser = Parser::new();
            for p in parser.push(&data) {
                prop_assert!(p.points.iter().all(|pt| pt.angle_cdeg < 36000));
            }
        }
    }
}
