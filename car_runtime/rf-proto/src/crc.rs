//! Checksums used by the protocols.

/// CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection, no final xor).
pub fn crc16_ccitt(data: &[u8]) -> u16 {
    let mut crc: u16 = 0xFFFF;
    for &byte in data {
        crc ^= u16::from(byte) << 8;
        for _ in 0..8 {
            crc = if crc & 0x8000 != 0 { (crc << 1) ^ 0x1021 } else { crc << 1 };
        }
    }
    crc
}

/// CRC-8 as used by LD06/LD19 LiDARs (poly 0x4D, init 0x00, no reflection).
pub fn crc8_ld06(data: &[u8]) -> u8 {
    let mut crc: u8 = 0;
    for &byte in data {
        crc ^= byte;
        for _ in 0..8 {
            crc = if crc & 0x80 != 0 { (crc << 1) ^ 0x4D } else { crc << 1 };
        }
    }
    crc
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crc16_check_value() {
        // Standard check value for CRC-16/CCITT-FALSE.
        assert_eq!(crc16_ccitt(b"123456789"), 0x29B1);
    }

    #[test]
    fn crc8_detects_single_bit_flips() {
        let data = [0x54u8, 0x2C, 0x10, 0x20, 0x30];
        let base = crc8_ld06(&data);
        for i in 0..data.len() {
            for bit in 0..8 {
                let mut d = data;
                d[i] ^= 1 << bit;
                assert_ne!(crc8_ld06(&d), base);
            }
        }
    }
}
