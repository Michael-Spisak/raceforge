//! Connection to the Python controller host process (ADR-0014): NDJSON over a Unix socket.

use rf_proto::ipc::{self, ChannelValue, Command, FromHost, Note, Observation, RobotInfo, ToHost};
use std::collections::BTreeMap;
use std::io::{self, ErrorKind, Read, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command as Process};
use std::time::{Duration, Instant};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum LinkError {
    #[error("controller missed its deadline")]
    Timeout,
    #[error("controller connection closed")]
    Closed,
    #[error("controller raised: {0}")]
    Controller(String),
    #[error("protocol error: {0}")]
    Protocol(String),
    #[error("io error: {0}")]
    Io(#[from] io::Error),
}

/// A command reply of one control step.
#[derive(Debug, Clone, PartialEq)]
pub struct StepReply {
    pub cmd: Command,
    pub channels: BTreeMap<String, ChannelValue>,
    pub notes: Vec<Note>,
}

pub struct ControllerLink {
    stream: UnixStream,
    pending: Vec<u8>,
    child: Option<Child>,
    socket_path: Option<PathBuf>,
}

impl ControllerLink {
    /// Use an already connected stream (tests, or a host started elsewhere).
    pub fn from_stream(stream: UnixStream) -> Self {
        Self {
            stream,
            pending: Vec::new(),
            child: None,
            socket_path: None,
        }
    }

    /// Bind `socket_path`, start the host process (`--socket <path>` is appended to `cmd`)
    /// and wait up to `timeout` for it to connect.
    pub fn spawn(
        mut cmd: Process,
        socket_path: &Path,
        timeout: Duration,
    ) -> Result<Self, LinkError> {
        let _ = std::fs::remove_file(socket_path);
        let listener = UnixListener::bind(socket_path)?;
        listener.set_nonblocking(true)?;
        let mut child = cmd.arg("--socket").arg(socket_path).spawn()?;
        let start = Instant::now();
        let stream = loop {
            match listener.accept() {
                Ok((s, _)) => break s,
                Err(e) if e.kind() == ErrorKind::WouldBlock => {
                    if let Some(status) = child.try_wait()? {
                        let _ = std::fs::remove_file(socket_path);
                        return Err(LinkError::Protocol(format!(
                            "host exited before connecting: {status}"
                        )));
                    }
                    if start.elapsed() > timeout {
                        let _ = child.kill();
                        let _ = child.wait();
                        let _ = std::fs::remove_file(socket_path);
                        return Err(LinkError::Timeout);
                    }
                    std::thread::sleep(Duration::from_millis(5));
                }
                Err(e) => return Err(e.into()),
            }
        };
        stream.set_nonblocking(false)?;
        Ok(Self {
            stream,
            pending: Vec::new(),
            child: Some(child),
            socket_path: Some(socket_path.to_path_buf()),
        })
    }

    fn send(&mut self, msg: &ToHost) -> Result<(), LinkError> {
        let line = ipc::to_line(msg).map_err(|e| LinkError::Protocol(e.to_string()))?;
        self.stream
            .write_all(line.as_bytes())
            .map_err(|e| match e.kind() {
                ErrorKind::BrokenPipe | ErrorKind::ConnectionReset => LinkError::Closed,
                _ => LinkError::Io(e),
            })
    }

    /// Next complete line, waiting at most until `deadline`.
    fn recv(&mut self, deadline: Instant) -> Result<FromHost, LinkError> {
        loop {
            if let Some(i) = self.pending.iter().position(|&b| b == b'\n') {
                let line: Vec<u8> = self.pending.drain(..=i).collect();
                let text =
                    std::str::from_utf8(&line).map_err(|e| LinkError::Protocol(e.to_string()))?;
                return ipc::from_line(text).map_err(|e| LinkError::Protocol(e.to_string()));
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(LinkError::Timeout);
            }
            self.stream.set_read_timeout(Some(remaining))?;
            let mut buf = [0u8; 8192];
            match self.stream.read(&mut buf) {
                Ok(0) => return Err(LinkError::Closed),
                Ok(n) => self.pending.extend_from_slice(&buf[..n]),
                Err(e) if matches!(e.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut) => {
                    return Err(LinkError::Timeout)
                }
                Err(e) if e.kind() == ErrorKind::Interrupted => {}
                Err(e) => return Err(e.into()),
            }
        }
    }

    /// Send `hello` and wait for `ready` (controller setup) within `timeout`.
    pub fn hello(&mut self, info: &RobotInfo, timeout: Duration) -> Result<(), LinkError> {
        self.send(&ToHost::Hello { info: info.clone() })?;
        match self.recv(Instant::now() + timeout)? {
            FromHost::Ready => Ok(()),
            FromHost::Error { detail, .. } => Err(LinkError::Controller(detail)),
            other => Err(LinkError::Protocol(format!(
                "expected ready, got {other:?}"
            ))),
        }
    }

    /// One control step: send the observation, wait for the matching command until `deadline`.
    pub fn step(
        &mut self,
        seq: u64,
        obs: Observation,
        deadline: Instant,
    ) -> Result<StepReply, LinkError> {
        self.send(&ToHost::Obs { seq, obs })?;
        loop {
            match self.recv(deadline)? {
                FromHost::Cmd {
                    seq: s,
                    cmd,
                    channels,
                    notes,
                } if s == seq => {
                    return Ok(StepReply {
                        cmd,
                        channels,
                        notes,
                    })
                }
                FromHost::Error { seq: s, detail } if s.is_none_or(|s| s == seq) => {
                    return Err(LinkError::Controller(detail))
                }
                // Late reply of an earlier step: skip it.
                FromHost::Cmd { seq: s, .. } | FromHost::Error { seq: Some(s), .. } if s < seq => {}
                other => return Err(LinkError::Protocol(format!("unexpected reply {other:?}"))),
            }
        }
    }

    /// Ask the host to tear down the controller (best effort).
    pub fn shutdown(&mut self) {
        let _ = self.send(&ToHost::Shutdown);
    }

    /// Kill the host process immediately (hung or crashed controller).
    pub fn kill(&mut self) {
        let _ = self.stream.shutdown(std::net::Shutdown::Both);
        if let Some(mut c) = self.child.take() {
            let _ = c.kill();
            let _ = c.wait();
        }
    }
}

impl Drop for ControllerLink {
    fn drop(&mut self) {
        if let Some(mut c) = self.child.take() {
            self.shutdown();
            let t = Instant::now();
            while t.elapsed() < Duration::from_millis(500) {
                if matches!(c.try_wait(), Ok(Some(_))) {
                    break;
                }
                std::thread::sleep(Duration::from_millis(5));
            }
            let _ = c.kill();
            let _ = c.wait();
        }
        if let Some(p) = &self.socket_path {
            let _ = std::fs::remove_file(p);
        }
    }
}
