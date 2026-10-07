//! Minimal MCAP writer (<https://mcap.dev/spec>): header, schemas, channels, messages, footer.
//!
//! No chunks and no summary/index section: every record is appended as it comes, so a file cut
//! off by a power loss keeps everything written before the cut. Readers without an index (the
//! Python `mcap` reader, Foxglove) scan such files linearly.

use std::io::{self, Write};

pub const MAGIC: &[u8; 8] = b"\x89MCAP0\r\n";

mod op {
    pub const HEADER: u8 = 0x01;
    pub const FOOTER: u8 = 0x02;
    pub const SCHEMA: u8 = 0x03;
    pub const CHANNEL: u8 = 0x04;
    pub const MESSAGE: u8 = 0x05;
    pub const DATA_END: u8 = 0x0F;
}

fn put_str(buf: &mut Vec<u8>, s: &str) {
    buf.extend_from_slice(&(s.len() as u32).to_le_bytes());
    buf.extend_from_slice(s.as_bytes());
}

pub struct McapWriter<W: Write> {
    w: W,
    next_schema: u16,
    next_channel: u16,
    pub messages: u64,
}

impl<W: Write> McapWriter<W> {
    pub fn new(mut w: W, library: &str) -> io::Result<Self> {
        w.write_all(MAGIC)?;
        let mut body = Vec::new();
        put_str(&mut body, ""); // profile
        put_str(&mut body, library);
        let mut this = Self {
            w,
            next_schema: 1,
            next_channel: 0,
            messages: 0,
        };
        this.record(op::HEADER, &body)?;
        Ok(this)
    }

    fn record(&mut self, opcode: u8, body: &[u8]) -> io::Result<()> {
        self.w.write_all(&[opcode])?;
        self.w.write_all(&(body.len() as u64).to_le_bytes())?;
        self.w.write_all(body)
    }

    /// Register a schema; returns its id (ids start at 1, 0 means "no schema").
    pub fn schema(&mut self, name: &str, encoding: &str, data: &[u8]) -> io::Result<u16> {
        let id = self.next_schema;
        self.next_schema += 1;
        let mut body = Vec::with_capacity(data.len() + name.len() + 16);
        body.extend_from_slice(&id.to_le_bytes());
        put_str(&mut body, name);
        put_str(&mut body, encoding);
        body.extend_from_slice(&(data.len() as u32).to_le_bytes());
        body.extend_from_slice(data);
        self.record(op::SCHEMA, &body)?;
        Ok(id)
    }

    pub fn channel(
        &mut self,
        schema_id: u16,
        topic: &str,
        message_encoding: &str,
    ) -> io::Result<u16> {
        let id = self.next_channel;
        self.next_channel += 1;
        let mut body = Vec::new();
        body.extend_from_slice(&id.to_le_bytes());
        body.extend_from_slice(&schema_id.to_le_bytes());
        put_str(&mut body, topic);
        put_str(&mut body, message_encoding);
        body.extend_from_slice(&0u32.to_le_bytes()); // empty metadata map
        self.record(op::CHANNEL, &body)?;
        Ok(id)
    }

    pub fn message(
        &mut self,
        channel: u16,
        sequence: u32,
        log_time_ns: u64,
        data: &[u8],
    ) -> io::Result<()> {
        let mut head = [0u8; 22];
        head[0..2].copy_from_slice(&channel.to_le_bytes());
        head[2..6].copy_from_slice(&sequence.to_le_bytes());
        head[6..14].copy_from_slice(&log_time_ns.to_le_bytes());
        head[14..22].copy_from_slice(&log_time_ns.to_le_bytes()); // publish time
        self.w.write_all(&[op::MESSAGE])?;
        self.w
            .write_all(&((head.len() + data.len()) as u64).to_le_bytes())?;
        self.w.write_all(&head)?;
        self.w.write_all(data)?;
        self.messages += 1;
        Ok(())
    }

    pub fn flush(&mut self) -> io::Result<()> {
        self.w.flush()
    }

    /// Write data-end, footer (no summary) and the closing magic; returns the inner writer.
    pub fn finish(mut self) -> io::Result<W> {
        self.record(op::DATA_END, &0u32.to_le_bytes())?; // data section CRC not computed
        self.record(op::FOOTER, &[0u8; 20])?; // summary start, summary offset start, summary CRC
        self.w.write_all(MAGIC)?;
        self.w.flush()?;
        Ok(self.w)
    }
}

/// A parsed record (used by tests and log tools).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Record {
    pub opcode: u8,
    pub body: Vec<u8>,
}

/// Split an MCAP byte stream into records. Stops at a truncated record (power loss) and
/// reports whether the file was closed properly (footer + trailing magic).
pub fn parse(data: &[u8]) -> Option<(Vec<Record>, bool)> {
    let rest = data.strip_prefix(MAGIC.as_slice())?;
    let mut pos = 0;
    let mut out = Vec::new();
    while rest.len() >= pos + 9 {
        let opcode = rest[pos];
        let mut len = [0u8; 8];
        len.copy_from_slice(&rest[pos + 1..pos + 9]);
        let len = usize::try_from(u64::from_le_bytes(len)).ok()?;
        let Some(end) = (pos + 9).checked_add(len).filter(|&e| e <= rest.len()) else {
            break;
        };
        out.push(Record {
            opcode,
            body: rest[pos + 9..end].to_vec(),
        });
        pos = end;
        if opcode == op::FOOTER {
            return Some((out, rest[pos..] == MAGIC[..]));
        }
    }
    Some((out, false))
}

/// Topic, sequence, log time and payload of a message record.
pub fn message_parts(r: &Record) -> Option<(u16, u32, u64, &[u8])> {
    if r.opcode != op::MESSAGE || r.body.len() < 22 {
        return None;
    }
    let b = &r.body;
    Some((
        u16::from_le_bytes([b[0], b[1]]),
        u32::from_le_bytes([b[2], b[3], b[4], b[5]]),
        u64::from_le_bytes([b[6], b[7], b[8], b[9], b[10], b[11], b[12], b[13]]),
        &b[22..],
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn layout_roundtrip() {
        let mut w = McapWriter::new(Vec::new(), "test").expect("header");
        let s = w.schema("S", "jsonschema", b"{}").expect("schema");
        let c = w.channel(s, "/t", "json").expect("channel");
        w.message(c, 7, 42, b"{\"a\":1}").expect("msg");
        let bytes = w.finish().expect("finish");
        assert!(bytes.ends_with(MAGIC));
        let (recs, closed) = parse(&bytes).expect("parse");
        assert!(closed);
        let ops: Vec<u8> = recs.iter().map(|r| r.opcode).collect();
        assert_eq!(
            ops,
            vec![
                op::HEADER,
                op::SCHEMA,
                op::CHANNEL,
                op::MESSAGE,
                op::DATA_END,
                op::FOOTER
            ]
        );
        assert_eq!(s, 1);
        assert_eq!(message_parts(&recs[3]), Some((0, 7, 42, &b"{\"a\":1}"[..])));
    }

    #[test]
    fn truncated_file_keeps_complete_records() {
        let mut w = McapWriter::new(Vec::new(), "test").expect("header");
        let c = w.channel(0, "/t", "json").expect("channel");
        for i in 0..10 {
            w.message(c, i, u64::from(i), b"0123456789").expect("msg");
        }
        let bytes = w.finish().expect("finish");
        let cut = &bytes[..bytes.len() - 60]; // cut inside the last message
        let (recs, closed) = parse(cut).expect("parse");
        assert!(!closed);
        let msgs = recs.iter().filter_map(message_parts).count();
        assert_eq!(msgs, 9);
    }
}
