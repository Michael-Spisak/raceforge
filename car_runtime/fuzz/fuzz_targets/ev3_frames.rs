//! EV3 link frames (UDP from the brick): decoding never panics, and every decoded frame
//! re-encodes to the same frame.

#![no_main]

use libfuzzer_sys::fuzz_target;
use rf_proto::crc::crc16_ccitt;
use rf_proto::ev3::{CommandFrame, SensorFrame};

fn check(data: &[u8]) {
    if let Ok(f) = SensorFrame::decode(data) {
        assert_eq!(SensorFrame::decode(&f.encode()), Ok(f));
    }
    if let Ok(f) = CommandFrame::decode(data) {
        assert_eq!(CommandFrame::decode(&f.encode()), Ok(f));
    }
}

fuzz_target!(|data: &[u8]| {
    check(data);
    // Again with a valid CRC-16 trailer: random inputs almost never pass the CRC, so without
    // this the fuzzer would not reach the field decoding behind it (ADR-0019).
    if let Some(n) = data.len().checked_sub(2) {
        let mut v = data.to_vec();
        let crc = crc16_ccitt(&v[..n]);
        v[n..].copy_from_slice(&crc.to_le_bytes());
        check(&v);
    }
});
