//! Serial transport to an EV3 running EV3RT (spec 0011): the same rf-proto frames, COBS-framed
//! (`rf_proto::cobs`) over a byte stream — UART on sensor port 1, USB CDC or Bluetooth SPP.

use crate::Transport;
use rf_proto::cobs::{self, StreamDecoder};
use std::io::{self, Read, Write};
use std::path::Path;
use std::process::Command;
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::sync::Mutex;
use std::time::Duration;

/// Largest packet accepted from the EV3 (a sensor frame is 62 bytes).
const MAX_PACKET: usize = 128;

pub struct SerialTransport {
    writer: Mutex<Box<dyn Write + Send>>,
    packets: Mutex<Receiver<Vec<u8>>>,
}

impl SerialTransport {
    /// Configure `device` with `stty` (raw, `baud`) and open it for reading and writing.
    pub fn open(device: &Path, baud: u32) -> io::Result<Self> {
        let status = Command::new("stty")
            .arg("-F")
            .arg(device)
            .args([
                baud.to_string().as_str(),
                "raw",
                "-echo",
                "-echoe",
                "-echok",
                "-ixon",
                "-ixoff",
                "-crtscts",
                "cs8",
                "-cstopb",
                "-parenb",
            ])
            .status()?;
        if !status.success() {
            return Err(io::Error::other(format!(
                "stty failed for {}",
                device.display()
            )));
        }
        let file = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open(device)?;
        let reader = file.try_clone()?;
        Self::from_stream(Box::new(reader), Box::new(file))
    }

    /// Wrap any byte stream (tests use a Unix socket pair). A thread reads `reader` and decodes
    /// packets; it ends when the stream closes.
    pub fn from_stream(
        mut reader: Box<dyn Read + Send>,
        writer: Box<dyn Write + Send>,
    ) -> io::Result<Self> {
        let (tx, rx) = mpsc::channel();
        std::thread::Builder::new()
            .name("ev3-serial-rx".into())
            .spawn(move || {
                let mut decoder = StreamDecoder::new(MAX_PACKET);
                let mut buf = [0u8; 256];
                loop {
                    match reader.read(&mut buf) {
                        Ok(0) => return,
                        Ok(n) => {
                            for p in decoder.push(&buf[..n]) {
                                if tx.send(p).is_err() {
                                    return;
                                }
                            }
                        }
                        Err(e) if e.kind() == io::ErrorKind::Interrupted => {}
                        Err(_) => return,
                    }
                }
            })?;
        Ok(Self {
            writer: Mutex::new(writer),
            packets: Mutex::new(rx),
        })
    }
}

impl Transport for SerialTransport {
    fn send(&self, frame: &[u8]) -> io::Result<()> {
        let mut w = self.writer.lock().unwrap_or_else(|e| e.into_inner());
        w.write_all(&cobs::frame(frame))?;
        w.flush()
    }

    fn recv(&self, buf: &mut [u8], timeout: Duration) -> io::Result<Option<usize>> {
        let rx = self.packets.lock().unwrap_or_else(|e| e.into_inner());
        match rx.recv_timeout(timeout.max(Duration::from_millis(1))) {
            Ok(p) => {
                let n = p.len().min(buf.len());
                buf[..n].copy_from_slice(&p[..n]);
                Ok(Some(n))
            }
            Err(RecvTimeoutError::Timeout) => Ok(None),
            Err(RecvTimeoutError::Disconnected) => Err(io::Error::new(
                io::ErrorKind::BrokenPipe,
                "EV3 serial link closed",
            )),
        }
    }
}
