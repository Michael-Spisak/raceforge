//! COBS framing for the serial EV3 links (spec 0011): `COBS(frame) ‖ 0x00`.
//!
//! Same algorithm as `ev3rt/common/rf_proto.c` (the EV3RT side); encoded data never contains `0x00`,
//! so the delimiter resynchronises the stream after noise (e.g. EV3RT's emergency log on port 1).

/// Encodes `data` (no delimiter appended).
pub fn encode(data: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(data.len() + data.len() / 254 + 2);
    let mut code_pos = 0;
    out.push(0);
    let mut code: u8 = 1;
    for &b in data {
        if b == 0 {
            out[code_pos] = code;
            code = 1;
            code_pos = out.len();
            out.push(0);
        } else {
            out.push(b);
            code += 1;
            if code == 0xFF {
                out[code_pos] = code;
                code = 1;
                code_pos = out.len();
                out.push(0);
            }
        }
    }
    out[code_pos] = code;
    out
}

/// Decodes one COBS packet (without delimiter); `None` if it is not valid COBS.
pub fn decode(data: &[u8]) -> Option<Vec<u8>> {
    let mut out = Vec::with_capacity(data.len());
    let mut i = 0;
    while i < data.len() {
        let code = data[i] as usize;
        i += 1;
        if code == 0 || i + code - 1 > data.len() {
            return None;
        }
        for _ in 1..code {
            if data[i] == 0 {
                return None;
            }
            out.push(data[i]);
            i += 1;
        }
        if code != 0xFF && i < data.len() {
            out.push(0);
        }
    }
    Some(out)
}

/// A frame ready for the serial line: `COBS(frame) ‖ 0x00`.
pub fn frame(data: &[u8]) -> Vec<u8> {
    let mut out = encode(data);
    out.push(0);
    out
}

/// Byte-stream decoder: collects bytes up to each `0x00` and yields the decoded packets.
/// Packets longer than `max` or with invalid COBS are dropped and counted.
#[derive(Debug)]
pub struct StreamDecoder {
    buf: Vec<u8>,
    max: usize,
    overflow: bool,
    pub dropped: u64,
}

impl StreamDecoder {
    pub fn new(max: usize) -> Self {
        Self {
            buf: Vec::with_capacity(max),
            max,
            overflow: false,
            dropped: 0,
        }
    }

    /// Feed bytes; returns the complete packets they finished.
    pub fn push(&mut self, bytes: &[u8]) -> Vec<Vec<u8>> {
        let mut out = Vec::new();
        for &b in bytes {
            if b != 0 {
                if self.buf.len() < self.max {
                    self.buf.push(b);
                } else {
                    self.overflow = true;
                }
                continue;
            }
            if self.buf.is_empty() {
                continue;
            }
            match (!self.overflow).then(|| decode(&self.buf)).flatten() {
                Some(p) if !p.is_empty() => out.push(p),
                _ => self.dropped += 1,
            }
            self.buf.clear();
            self.overflow = false;
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ev3::CommandFrame;

    #[test]
    fn round_trip_sizes() {
        for n in [1usize, 2, 22, 62, 253, 254, 255, 300, 600] {
            let data: Vec<u8> = (0..n)
                .map(|i| {
                    if (i * 37 + n) % 5 == 0 {
                        0
                    } else {
                        (i * 13 + 1) as u8
                    }
                })
                .collect();
            let enc = encode(&data);
            assert!(!enc.contains(&0));
            assert!(enc.len() <= n + n / 254 + 1);
            assert_eq!(decode(&enc).unwrap(), data);
        }
        assert_eq!(decode(&[5, 1]), None);
        assert_eq!(decode(&[0]), None);
    }

    /// Golden: the same command frame encoded by ev3rt/common (C) — COBS bytes on the wire.
    #[test]
    fn golden_matches_ev3rt_c() {
        let c = CommandFrame {
            seq: 7,
            t_ms: 1234,
            steer_target_cdeg: -1500,
            drive_speed_cps: 360,
            flags: 1,
            led: 2,
            lcd: 1,
        };
        let wire = frame(&c.encode());
        assert_eq!(
            hex(&wire),
            "064652010107010103d204010824fa680101020103244f00",
        );
    }

    #[test]
    fn stream_resyncs_after_garbage() {
        let c = CommandFrame {
            seq: 42,
            steer_target_cdeg: 100,
            drive_speed_cps: 200,
            ..CommandFrame::default()
        };
        let wire = frame(&c.encode());
        let mut d = StreamDecoder::new(80);
        let mut got = d.push(&[0x13, 0x37, 0x00, 0xAA, 0xBB, 0xCC]);
        got.extend(d.push(&wire));
        got.extend(d.push(&wire));
        assert_eq!(got.len(), 1);
        assert_eq!(CommandFrame::decode(&got[0]).unwrap().seq, 42);
        assert_eq!(d.dropped, 2);
        assert!(d.push(&[0x55; 200]).is_empty());
        assert!(d.push(&[0]).is_empty());
        assert_eq!(d.dropped, 3);
    }

    fn hex(b: &[u8]) -> String {
        b.iter().map(|x| format!("{x:02x}")).collect()
    }
}
