//! Runtime events (faults, resume, notes) as text lines for the system journal, so a fault shows
//! up in `journalctl -u rf-runtime` while the car is on the bench, not only in the MCAP log.
//!
//! [`EventPrinter`] is a [`TickSink`] whose `event` never blocks the control thread: lines go
//! through a bounded queue to a writer thread (a stalled journal drops lines, never ticks).

use rf_core::runtime::{Event, TickRecord, TickSink};
use std::io::Write;
use std::sync::mpsc::{self, SyncSender};
use std::sync::Mutex;
use std::thread::JoinHandle;

pub struct EventPrinter {
    tx: Mutex<Option<SyncSender<String>>>,
    thread: Mutex<Option<JoinHandle<()>>>,
}

impl EventPrinter {
    /// Print events to `out` (stderr on the board: systemd sends it to the journal).
    pub fn start(mut out: Box<dyn Write + Send>) -> Self {
        let (tx, rx) = mpsc::sync_channel::<String>(256);
        let thread = std::thread::spawn(move || {
            for line in rx {
                let _ = writeln!(out, "{line}");
                let _ = out.flush();
            }
        });
        Self {
            tx: Mutex::new(Some(tx)),
            thread: Mutex::new(Some(thread)),
        }
    }

    /// Print what is queued and stop the writer thread.
    pub fn close(&self) {
        self.tx
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .take();
        let handle = self
            .thread
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .take();
        if let Some(h) = handle {
            let _ = h.join();
        }
    }
}

impl Drop for EventPrinter {
    fn drop(&mut self) {
        self.close();
    }
}

impl TickSink for EventPrinter {
    fn tick(&self, _rec: &TickRecord) {}

    fn event(&self, mono_ns: u64, e: &Event) {
        let line = format!(
            "event t={:.3}s {}: {}",
            mono_ns as f64 / 1e9,
            e.kind,
            e.detail
        );
        let guard = self
            .tx
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if let Some(tx) = guard.as_ref() {
            let _ = tx.try_send(line); // full or closed: drop the line, never block
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    #[derive(Clone, Default)]
    struct Shared(Arc<Mutex<Vec<u8>>>);

    impl Write for Shared {
        fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
            self.0
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .extend_from_slice(buf);
            Ok(buf.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }

    /// A writer that never returns (a stalled journal).
    struct Stuck;

    impl Write for Stuck {
        fn write(&mut self, _buf: &[u8]) -> std::io::Result<usize> {
            std::thread::park();
            Ok(0)
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }

    fn event(kind: &str, detail: &str) -> Event {
        Event {
            t_s: 0.0,
            kind: kind.into(),
            detail: detail.into(),
        }
    }

    #[test]
    fn events_become_journal_lines() {
        let out = Shared::default();
        let p = EventPrinter::start(Box::new(out.clone()));
        p.event(1_500_000_000, &event("fault", "EStop"));
        p.event(3_000_000_000, &event("resumed", "EStop"));
        p.close();
        let text = String::from_utf8(out.0.lock().map(|v| v.clone()).unwrap_or_default())
            .unwrap_or_default();
        assert_eq!(
            text,
            "event t=1.500s fault: EStop\nevent t=3.000s resumed: EStop\n"
        );
    }

    #[test]
    fn a_stalled_journal_never_blocks_the_control_thread() {
        let p = EventPrinter::start(Box::new(Stuck));
        let t = std::time::Instant::now();
        for i in 0..10_000 {
            p.event(i, &event("note", "x"));
        }
        assert!(t.elapsed() < std::time::Duration::from_millis(500));
        // (the writer thread stays parked; the test process exits without joining it)
        std::mem::forget(p);
    }
}
