//! Minimal WebSocket server side (RFC 6455): opening handshake and the frames this server needs.
//!
//! Deliberately small: text, close, ping and pong frames; no fragmentation, no extensions, no
//! binary messages. Client frames must be masked and at most [`MAX_PAYLOAD`] bytes. Anything else
//! is a protocol error and the connection is closed.

use std::io::{self, Read, Write};

const GUID: &str = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
/// Largest client message accepted (commands are a few hundred bytes).
pub const MAX_PAYLOAD: usize = 64 * 1024;
/// Largest handshake request accepted.
pub const MAX_REQUEST: usize = 8 * 1024;

/// SHA-1 (FIPS 180-4), only for the handshake's `Sec-WebSocket-Accept` (not used for security).
pub fn sha1(data: &[u8]) -> [u8; 20] {
    let mut h: [u32; 5] = [
        0x6745_2301,
        0xEFCD_AB89,
        0x98BA_DCFE,
        0x1032_5476,
        0xC3D2_E1F0,
    ];
    let mut msg = data.to_vec();
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&((data.len() as u64).wrapping_mul(8)).to_be_bytes());
    for block in msg.as_chunks::<64>().0 {
        let mut w = [0u32; 80];
        for (i, word) in block.as_chunks::<4>().0.iter().enumerate() {
            w[i] = u32::from_be_bytes(*word);
        }
        for i in 16..80 {
            w[i] = (w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]).rotate_left(1);
        }
        let [mut a, mut b, mut c, mut d, mut e] = h;
        for (i, wi) in w.iter().enumerate() {
            let (f, k) = match i {
                0..=19 => ((b & c) | (!b & d), 0x5A82_7999),
                20..=39 => (b ^ c ^ d, 0x6ED9_EBA1),
                40..=59 => ((b & c) | (b & d) | (c & d), 0x8F1B_BCDC),
                _ => (b ^ c ^ d, 0xCA62_C1D6),
            };
            let t = a
                .rotate_left(5)
                .wrapping_add(f)
                .wrapping_add(e)
                .wrapping_add(k)
                .wrapping_add(*wi);
            e = d;
            d = c;
            c = b.rotate_left(30);
            b = a;
            a = t;
        }
        for (x, y) in h.iter_mut().zip([a, b, c, d, e]) {
            *x = x.wrapping_add(y);
        }
    }
    let mut out = [0u8; 20];
    for (o, x) in out.as_chunks_mut::<4>().0.iter_mut().zip(h) {
        *o = x.to_be_bytes();
    }
    out
}

pub fn base64(data: &[u8]) -> String {
    const T: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    for chunk in data.chunks(3) {
        let b = [
            chunk[0],
            *chunk.get(1).unwrap_or(&0),
            *chunk.get(2).unwrap_or(&0),
        ];
        let n = (u32::from(b[0]) << 16) | (u32::from(b[1]) << 8) | u32::from(b[2]);
        for i in 0..4 {
            if i <= chunk.len() {
                out.push(char::from(T[((n >> (18 - 6 * i)) & 63) as usize]));
            } else {
                out.push('=');
            }
        }
    }
    out
}

pub fn accept_key(client_key: &str) -> String {
    base64(&sha1(format!("{}{GUID}", client_key.trim()).as_bytes()))
}

/// A parsed opening request.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Request {
    pub path: String,
    pub key: String,
}

impl Request {
    /// Value of a query parameter in the request path (`/?token=abc`).
    pub fn query(&self, name: &str) -> Option<&str> {
        let q = self.path.split_once('?')?.1;
        q.split('&').find_map(|kv| {
            kv.split_once('=')
                .filter(|(k, _)| *k == name)
                .map(|(_, v)| v)
        })
    }
}

/// Read and validate the HTTP upgrade request. `Err` carries the HTTP status to answer with.
pub fn read_request<R: Read>(r: &mut R) -> Result<Request, u16> {
    let mut buf = Vec::with_capacity(512);
    let mut byte = [0u8; 1];
    while !buf.ends_with(b"\r\n\r\n") {
        if buf.len() >= MAX_REQUEST {
            return Err(431);
        }
        match r.read(&mut byte) {
            Ok(1) => buf.push(byte[0]),
            _ => return Err(400),
        }
    }
    let text = std::str::from_utf8(&buf).map_err(|_| 400u16)?;
    let mut lines = text.split("\r\n");
    let mut first = lines.next().unwrap_or_default().split(' ');
    let (method, path, version) = (first.next(), first.next(), first.next());
    if method != Some("GET") || version != Some("HTTP/1.1") {
        return Err(400);
    }
    let mut key = None;
    let (mut upgrade, mut ws_version) = (false, false);
    for line in lines {
        let Some((name, value)) = line.split_once(':') else {
            continue;
        };
        let value = value.trim();
        match name.trim().to_ascii_lowercase().as_str() {
            "upgrade" => upgrade = value.eq_ignore_ascii_case("websocket"),
            "sec-websocket-version" => ws_version = value == "13",
            "sec-websocket-key" => key = Some(value.to_string()),
            _ => {}
        }
    }
    match (upgrade, ws_version, key) {
        (true, true, Some(key)) if !key.is_empty() => Ok(Request {
            path: path.unwrap_or("/").to_string(),
            key,
        }),
        _ => Err(400),
    }
}

pub fn write_accept<W: Write>(w: &mut W, req: &Request) -> io::Result<()> {
    write!(
        w,
        "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\
         Sec-WebSocket-Accept: {}\r\n\r\n",
        accept_key(&req.key)
    )?;
    w.flush()
}

pub fn write_reject<W: Write>(w: &mut W, status: u16) -> io::Result<()> {
    let reason = match status {
        401 => "Unauthorized",
        431 => "Request Header Fields Too Large",
        503 => "Service Unavailable",
        _ => "Bad Request",
    };
    write!(
        w,
        "HTTP/1.1 {status} {reason}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    )?;
    w.flush()
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Frame {
    Text(String),
    Ping(Vec<u8>),
    Pong(Vec<u8>),
    Close,
}

#[derive(Debug, PartialEq, Eq)]
pub enum FrameError {
    /// Close with this status code (RFC 6455 7.4.1).
    Protocol(u16),
    /// The connection ended or failed.
    Io(io::ErrorKind),
}

impl From<io::Error> for FrameError {
    fn from(e: io::Error) -> Self {
        FrameError::Io(e.kind())
    }
}

/// Read one client frame (must be masked, unfragmented, at most [`MAX_PAYLOAD`]).
pub fn read_frame<R: Read>(r: &mut R) -> Result<Frame, FrameError> {
    let mut head = [0u8; 2];
    r.read_exact(&mut head)?;
    let fin = head[0] & 0x80 != 0;
    let opcode = head[0] & 0x0F;
    if head[0] & 0x70 != 0 {
        return Err(FrameError::Protocol(1002)); // reserved bits: no extensions negotiated
    }
    if head[1] & 0x80 == 0 {
        return Err(FrameError::Protocol(1002)); // client frames must be masked
    }
    let len = match head[1] & 0x7F {
        126 => {
            let mut b = [0u8; 2];
            r.read_exact(&mut b)?;
            u64::from(u16::from_be_bytes(b))
        }
        127 => {
            let mut b = [0u8; 8];
            r.read_exact(&mut b)?;
            u64::from_be_bytes(b)
        }
        n => u64::from(n),
    };
    if len > MAX_PAYLOAD as u64 {
        return Err(FrameError::Protocol(1009));
    }
    let mut mask = [0u8; 4];
    r.read_exact(&mut mask)?;
    let mut payload = vec![0u8; len as usize];
    r.read_exact(&mut payload)?;
    for (i, b) in payload.iter_mut().enumerate() {
        *b ^= mask[i % 4];
    }
    if !fin || opcode == 0 {
        return Err(FrameError::Protocol(1003)); // fragmentation not supported
    }
    match opcode {
        1 => String::from_utf8(payload)
            .map(Frame::Text)
            .map_err(|_| FrameError::Protocol(1007)),
        8 => Ok(Frame::Close),
        9 if len <= 125 => Ok(Frame::Ping(payload)),
        10 if len <= 125 => Ok(Frame::Pong(payload)),
        _ => Err(FrameError::Protocol(1003)), // binary or unknown
    }
}

fn header(opcode: u8, len: usize, masked: bool) -> Vec<u8> {
    let mut h = vec![0x80 | opcode];
    let m = if masked { 0x80 } else { 0 };
    if len < 126 {
        h.push(m | len as u8);
    } else if len <= usize::from(u16::MAX) {
        h.push(m | 126);
        h.extend_from_slice(&(len as u16).to_be_bytes());
    } else {
        h.push(m | 127);
        h.extend_from_slice(&(len as u64).to_be_bytes());
    }
    h
}

/// Server frame (unmasked).
pub fn encode(opcode: u8, payload: &[u8]) -> Vec<u8> {
    let mut f = header(opcode, payload.len(), false);
    f.extend_from_slice(payload);
    f
}

pub fn text(s: &str) -> Vec<u8> {
    encode(1, s.as_bytes())
}

pub fn close(code: u16) -> Vec<u8> {
    encode(8, &code.to_be_bytes())
}

/// Client frame (masked) - used by tests and tools that talk to the server.
pub fn encode_client(opcode: u8, payload: &[u8], mask: [u8; 4]) -> Vec<u8> {
    let mut f = header(opcode, payload.len(), true);
    f.extend_from_slice(&mask);
    f.extend(payload.iter().enumerate().map(|(i, b)| b ^ mask[i % 4]));
    f
}

/// Read one server frame (unmasked) - for tests and tools.
pub fn read_server_frame<R: Read>(r: &mut R) -> io::Result<(u8, Vec<u8>)> {
    let mut head = [0u8; 2];
    r.read_exact(&mut head)?;
    let len = match head[1] & 0x7F {
        126 => {
            let mut b = [0u8; 2];
            r.read_exact(&mut b)?;
            usize::from(u16::from_be_bytes(b))
        }
        127 => {
            let mut b = [0u8; 8];
            r.read_exact(&mut b)?;
            usize::try_from(u64::from_be_bytes(b)).map_err(io::Error::other)?
        }
        n => usize::from(n),
    };
    let mut payload = vec![0u8; len];
    r.read_exact(&mut payload)?;
    Ok((head[0] & 0x0F, payload))
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn hex(b: &[u8]) -> String {
        b.iter().map(|x| format!("{x:02x}")).collect()
    }

    #[test]
    fn sha1_vectors() {
        assert_eq!(hex(&sha1(b"")), "da39a3ee5e6b4b0d3255bfef95601890afd80709");
        assert_eq!(
            hex(&sha1(b"abc")),
            "a9993e364706816aba3e25717850c26c9cd0d89d"
        );
        assert_eq!(
            hex(&sha1(
                b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"
            )),
            "84983e441c3bd26ebaae4aa1f95129e5e54670f1"
        );
        assert_eq!(
            hex(&sha1(&vec![b'a'; 1_000_000])),
            "34aa973cd4c4daa4f61eeb2bdbad27316534016f"
        );
    }

    #[test]
    fn base64_and_rfc6455_accept_key() {
        assert_eq!(base64(b""), "");
        assert_eq!(base64(b"f"), "Zg==");
        assert_eq!(base64(b"fo"), "Zm8=");
        assert_eq!(base64(b"foobar"), "Zm9vYmFy");
        // Example from RFC 6455 section 1.3.
        assert_eq!(
            accept_key("dGhlIHNhbXBsZSBub25jZQ=="),
            "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
        );
    }

    #[test]
    fn handshake_request_parsing() {
        let ok = "GET /?token=abc&x=1 HTTP/1.1\r\nHost: car\r\nUpgrade: WebSocket\r\nConnection: Upgrade\r\n\
                  Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n";
        let req = read_request(&mut ok.as_bytes()).expect("valid");
        assert_eq!(req.query("token"), Some("abc"));
        assert_eq!(req.query("nope"), None);
        let mut out = Vec::new();
        write_accept(&mut out, &req).expect("write");
        assert!(String::from_utf8_lossy(&out)
            .contains("Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo="));
        for bad in [
            ok.replace("GET", "POST"),
            ok.replace("Sec-WebSocket-Version: 13", "Sec-WebSocket-Version: 8"),
            ok.replace("Upgrade: WebSocket\r\n", ""),
            ok.replace("Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n", ""),
            "GET / HTTP/1.1\r\nHost: car\r\n".to_string(), // never ends
        ] {
            assert_eq!(read_request(&mut bad.as_bytes()), Err(400), "{bad:?}");
        }
        let huge = format!("GET / HTTP/1.1\r\nX: {}\r\n\r\n", "a".repeat(MAX_REQUEST));
        assert_eq!(read_request(&mut huge.as_bytes()), Err(431));
    }

    #[test]
    fn frame_rules() {
        let m = [1, 2, 3, 4];
        assert_eq!(
            read_frame(&mut &encode_client(1, b"hi", m)[..]),
            Ok(Frame::Text("hi".into()))
        );
        assert_eq!(
            read_frame(&mut &encode_client(8, &[0x03, 0xE8], m)[..]),
            Ok(Frame::Close)
        );
        assert_eq!(
            read_frame(&mut &encode_client(9, b"p", m)[..]),
            Ok(Frame::Ping(b"p".to_vec()))
        );
        // Unmasked client frame, binary, fragment, invalid UTF-8, oversize.
        assert_eq!(
            read_frame(&mut &text("hi")[..]),
            Err(FrameError::Protocol(1002))
        );
        assert_eq!(
            read_frame(&mut &encode_client(2, b"x", m)[..]),
            Err(FrameError::Protocol(1003))
        );
        let mut frag = encode_client(1, b"x", m);
        frag[0] &= 0x7F;
        assert_eq!(read_frame(&mut &frag[..]), Err(FrameError::Protocol(1003)));
        assert_eq!(
            read_frame(&mut &encode_client(1, &[0xFF, 0xFE], m)[..]),
            Err(FrameError::Protocol(1007))
        );
        let big = encode_client(1, &vec![b'a'; MAX_PAYLOAD + 1], m);
        assert_eq!(read_frame(&mut &big[..]), Err(FrameError::Protocol(1009)));
        // A huge declared length is refused before allocating.
        let mut lie = vec![0x81, 0x80 | 127];
        lie.extend_from_slice(&u64::MAX.to_be_bytes());
        assert_eq!(read_frame(&mut &lie[..]), Err(FrameError::Protocol(1009)));
    }

    proptest! {
        #[test]
        fn text_roundtrip(s in ".{0,300}", mask: [u8; 4]) {
            let f = encode_client(1, s.as_bytes(), mask);
            prop_assert_eq!(read_frame(&mut &f[..]), Ok(Frame::Text(s)));
        }

        #[test]
        fn server_frames_roundtrip(len in 0usize..70_000) {
            let payload = vec![b'x'; len];
            let (op, got) = read_server_frame(&mut &encode(1, &payload)[..]).expect("frame");
            prop_assert_eq!(op, 1);
            prop_assert_eq!(got.len(), len);
        }

        #[test]
        fn garbage_never_panics(data in proptest::collection::vec(any::<u8>(), 0..200)) {
            let _ = read_frame(&mut &data[..]);
            let _ = read_request(&mut &data[..]);
        }
    }
}
