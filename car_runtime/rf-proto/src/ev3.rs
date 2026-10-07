//! Binary frames between the board and the EV3 brick (little endian, CRC-16 trailer).
//!
//! ```text
//! header: magic u16 = 0x5246 ("RF") | version u8 = 1 | kind u8 | seq u32 | t_ms u32
//! CommandFrame (kind 1, board -> EV3): steer_target_cdeg i16 | drive_speed_cps i16 | flags u8 | led u8
//!                                      | lcd u8 | reserved u8
//! SensorFrame  (kind 2, EV3 -> board): ack_seq u32 | 4 x (tacho i32, speed_cps i16)
//!                                      | 4 x ultrasonic_mm u16 (0xFFFF = none) | gyro_rate_dps i16
//!                                      | gyro_angle_deg i32 | touch u8 | buttons u8 | battery_mv u16
//!                                      | flags u8 | reserved u8
//! trailer: crc16 u16 over everything before it
//! ```

use crate::crc::crc16_ccitt;
use thiserror::Error;

pub const MAGIC: u16 = 0x5246;
pub const VERSION: u8 = 1;
const HEADER_LEN: usize = 12;
pub const COMMAND_LEN: usize = HEADER_LEN + 8 + 2;
pub const SENSOR_LEN: usize = HEADER_LEN + 4 + 4 * 6 + 4 * 2 + 2 + 4 + 1 + 1 + 2 + 1 + 1 + 2;
pub const NO_ECHO: u16 = 0xFFFF;

/// Command flags (board -> EV3).
pub mod cmd_flags {
    pub const STOP: u8 = 1 << 0;
    pub const ESTOP: u8 = 1 << 1;
    pub const RACE: u8 = 1 << 2;
}

/// Status flags (EV3 -> board).
pub mod status_flags {
    pub const LINK_OK: u8 = 1 << 0;
    pub const ESTOP_PRESSED: u8 = 1 << 1;
    pub const FAILSAFE: u8 = 1 << 2;
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum FrameError {
    #[error("frame too short: {0} bytes")]
    TooShort(usize),
    #[error("bad magic {0:#06x}")]
    BadMagic(u16),
    #[error("unsupported version {0}")]
    BadVersion(u8),
    #[error("unexpected frame kind {0}")]
    BadKind(u8),
    #[error("crc mismatch")]
    Crc,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct CommandFrame {
    pub seq: u32,
    pub t_ms: u32,
    /// Steering motor target, centi-degrees of motor rotation.
    pub steer_target_cdeg: i16,
    /// Drive motor speed target, tacho counts per second (EV3 regulates).
    pub drive_speed_cps: i16,
    pub flags: u8,
    pub led: u8,
    pub lcd: u8,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Motor {
    pub tacho: i32,
    pub speed_cps: i16,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct SensorFrame {
    pub seq: u32,
    pub t_ms: u32,
    pub ack_seq: u32,
    /// Ports A..D.
    pub motors: [Motor; 4],
    /// Ports 1..4, millimetres; [`NO_ECHO`] when no reading.
    pub ultrasonic_mm: [u16; 4],
    pub gyro_rate_dps: i16,
    pub gyro_angle_deg: i32,
    pub touch: u8,
    pub buttons: u8,
    pub battery_mv: u16,
    pub flags: u8,
}

struct Writer(Vec<u8>);

impl Writer {
    fn header(kind: u8, seq: u32, t_ms: u32, cap: usize) -> Self {
        let mut v = Vec::with_capacity(cap);
        v.extend_from_slice(&MAGIC.to_le_bytes());
        v.push(VERSION);
        v.push(kind);
        v.extend_from_slice(&seq.to_le_bytes());
        v.extend_from_slice(&t_ms.to_le_bytes());
        Writer(v)
    }
    fn u8(&mut self, x: u8) {
        self.0.push(x);
    }
    fn u16(&mut self, x: u16) {
        self.0.extend_from_slice(&x.to_le_bytes());
    }
    fn i16(&mut self, x: i16) {
        self.0.extend_from_slice(&x.to_le_bytes());
    }
    fn u32(&mut self, x: u32) {
        self.0.extend_from_slice(&x.to_le_bytes());
    }
    fn i32(&mut self, x: i32) {
        self.0.extend_from_slice(&x.to_le_bytes());
    }
    fn finish(mut self) -> Vec<u8> {
        let crc = crc16_ccitt(&self.0);
        self.u16(crc);
        self.0
    }
}

struct Reader<'a> {
    data: &'a [u8],
    pos: usize,
}

impl Reader<'_> {
    fn take<const N: usize>(&mut self) -> [u8; N] {
        let mut out = [0u8; N];
        out.copy_from_slice(&self.data[self.pos..self.pos + N]);
        self.pos += N;
        out
    }
    fn u8(&mut self) -> u8 {
        self.take::<1>()[0]
    }
    fn u16(&mut self) -> u16 {
        u16::from_le_bytes(self.take())
    }
    fn i16(&mut self) -> i16 {
        i16::from_le_bytes(self.take())
    }
    fn u32(&mut self) -> u32 {
        u32::from_le_bytes(self.take())
    }
    fn i32(&mut self) -> i32 {
        i32::from_le_bytes(self.take())
    }
}

fn check(data: &[u8], len: usize, kind: u8) -> Result<Reader<'_>, FrameError> {
    if data.len() < len {
        return Err(FrameError::TooShort(data.len()));
    }
    let data = &data[..len];
    let magic = u16::from_le_bytes([data[0], data[1]]);
    if magic != MAGIC {
        return Err(FrameError::BadMagic(magic));
    }
    if data[2] != VERSION {
        return Err(FrameError::BadVersion(data[2]));
    }
    if data[3] != kind {
        return Err(FrameError::BadKind(data[3]));
    }
    let crc = u16::from_le_bytes([data[len - 2], data[len - 1]]);
    if crc16_ccitt(&data[..len - 2]) != crc {
        return Err(FrameError::Crc);
    }
    Ok(Reader { data, pos: 4 })
}

impl CommandFrame {
    pub const KIND: u8 = 1;

    pub fn encode(&self) -> Vec<u8> {
        let mut w = Writer::header(Self::KIND, self.seq, self.t_ms, COMMAND_LEN);
        w.i16(self.steer_target_cdeg);
        w.i16(self.drive_speed_cps);
        w.u8(self.flags);
        w.u8(self.led);
        w.u8(self.lcd);
        w.u8(0);
        w.finish()
    }

    pub fn decode(data: &[u8]) -> Result<Self, FrameError> {
        let mut r = check(data, COMMAND_LEN, Self::KIND)?;
        Ok(Self {
            seq: r.u32(),
            t_ms: r.u32(),
            steer_target_cdeg: r.i16(),
            drive_speed_cps: r.i16(),
            flags: r.u8(),
            led: r.u8(),
            lcd: r.u8(),
        })
    }
}

impl SensorFrame {
    pub const KIND: u8 = 2;

    pub fn encode(&self) -> Vec<u8> {
        let mut w = Writer::header(Self::KIND, self.seq, self.t_ms, SENSOR_LEN);
        w.u32(self.ack_seq);
        for m in &self.motors {
            w.i32(m.tacho);
            w.i16(m.speed_cps);
        }
        for &u in &self.ultrasonic_mm {
            w.u16(u);
        }
        w.i16(self.gyro_rate_dps);
        w.i32(self.gyro_angle_deg);
        w.u8(self.touch);
        w.u8(self.buttons);
        w.u16(self.battery_mv);
        w.u8(self.flags);
        w.u8(0);
        w.finish()
    }

    pub fn decode(data: &[u8]) -> Result<Self, FrameError> {
        let mut r = check(data, SENSOR_LEN, Self::KIND)?;
        let seq = r.u32();
        let t_ms = r.u32();
        let ack_seq = r.u32();
        let mut motors = [Motor::default(); 4];
        for m in &mut motors {
            m.tacho = r.i32();
            m.speed_cps = r.i16();
        }
        let mut ultrasonic_mm = [0u16; 4];
        for u in &mut ultrasonic_mm {
            *u = r.u16();
        }
        Ok(Self {
            seq,
            t_ms,
            ack_seq,
            motors,
            ultrasonic_mm,
            gyro_rate_dps: r.i16(),
            gyro_angle_deg: r.i32(),
            touch: r.u8(),
            buttons: r.u8(),
            battery_mv: r.u16(),
            flags: r.u8(),
        })
    }

    /// Ultrasonic reading of port `index` (0-based) in metres, `None` when there is no echo.
    pub fn ultrasonic_m(&self, index: usize) -> Option<f64> {
        let mm = *self.ultrasonic_mm.get(index)?;
        (mm != NO_ECHO).then(|| f64::from(mm) / 1000.0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn sensor_strategy() -> impl Strategy<Value = SensorFrame> {
        (
            any::<u32>(),
            any::<u32>(),
            any::<u32>(),
            proptest::array::uniform4((any::<i32>(), any::<i16>())),
            proptest::array::uniform4(any::<u16>()),
            any::<i16>(),
            any::<i32>(),
            (any::<u8>(), any::<u8>(), any::<u16>(), any::<u8>()),
        )
            .prop_map(
                |(seq, t_ms, ack, motors, us, rate, angle, (touch, buttons, mv, flags))| {
                    SensorFrame {
                        seq,
                        t_ms,
                        ack_seq: ack,
                        motors: motors.map(|(tacho, speed_cps)| Motor { tacho, speed_cps }),
                        ultrasonic_mm: us,
                        gyro_rate_dps: rate,
                        gyro_angle_deg: angle,
                        touch,
                        buttons,
                        battery_mv: mv,
                        flags,
                    }
                },
            )
    }

    proptest! {
        #[test]
        fn command_roundtrip(seq: u32, t: u32, steer: i16, speed: i16, flags: u8, led: u8, lcd: u8) {
            let f = CommandFrame { seq, t_ms: t, steer_target_cdeg: steer, drive_speed_cps: speed, flags, led, lcd };
            let bytes = f.encode();
            prop_assert_eq!(bytes.len(), COMMAND_LEN);
            prop_assert_eq!(CommandFrame::decode(&bytes), Ok(f));
        }

        #[test]
        fn sensor_roundtrip(f in sensor_strategy()) {
            let bytes = f.encode();
            prop_assert_eq!(bytes.len(), SENSOR_LEN);
            prop_assert_eq!(SensorFrame::decode(&bytes), Ok(f));
        }

        #[test]
        fn corrupted_frames_are_rejected(f in sensor_strategy(), idx in 0usize..SENSOR_LEN, bit in 0u8..8) {
            let mut bytes = f.encode();
            bytes[idx] ^= 1 << bit;
            prop_assert!(SensorFrame::decode(&bytes).is_err());
        }

        #[test]
        fn random_bytes_never_panic(data in proptest::collection::vec(any::<u8>(), 0..128)) {
            let _ = SensorFrame::decode(&data);
            let _ = CommandFrame::decode(&data);
        }
    }

    #[test]
    fn ultrasonic_conversion() {
        let f = SensorFrame {
            ultrasonic_mm: [1234, NO_ECHO, 0, 30],
            ..Default::default()
        };
        assert_eq!(f.ultrasonic_m(0), Some(1.234));
        assert_eq!(f.ultrasonic_m(1), None);
        assert_eq!(f.ultrasonic_m(9), None);
    }

    #[test]
    fn wrong_kind_rejected() {
        let bytes = CommandFrame::default().encode();
        assert_eq!(
            SensorFrame::decode(&bytes),
            Err(FrameError::TooShort(COMMAND_LEN))
        );
        let mut padded = bytes.clone();
        padded.resize(SENSOR_LEN, 0);
        assert!(matches!(
            SensorFrame::decode(&padded),
            Err(FrameError::BadKind(1))
        ));
    }
}
