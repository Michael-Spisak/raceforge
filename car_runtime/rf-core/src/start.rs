//! Race start (spec 0031): the car waits in READY until a start signal, then drives.
//!
//! - `Button`: press and release the start button, then a countdown (default 3 s) runs.
//! - `Wire`: a pull-away cable on a touch-sensor port (dry contact). It must have been seen
//!   closed (plugged in) once; the moment it opens, the car starts — no countdown, so all cars on
//!   one start box go together.
//!
//! While not started the runtime does not call the controller and keeps the motors stopped; the
//! emergency stop and link checks stay active. Without a start config the car drives at once
//! (the behaviour before this spec).

use std::time::{Duration, Instant};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StartMethod {
    Button,
    Wire,
}

#[derive(Debug, Clone, PartialEq)]
pub struct StartConfig {
    pub methods: Vec<StartMethod>,
    pub countdown: Duration,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Gate {
    /// Waiting for a start signal; `wire_ready` = the start cable is plugged in.
    Ready { wire_ready: bool },
    /// Button pressed; this much time is left.
    Countdown(Duration),
    /// Started on this tick (`method`), or earlier (`None`).
    Go(Option<StartMethod>),
}

#[derive(Debug)]
pub struct StartGate {
    cfg: StartConfig,
    countdown_until: Option<Instant>,
    button_down: bool,
    wire_armed: bool,
    started: bool,
}

impl StartGate {
    pub fn new(cfg: StartConfig) -> Self {
        Self {
            cfg,
            countdown_until: None,
            button_down: false,
            wire_armed: false,
            started: false,
        }
    }

    pub fn started(&self) -> bool {
        self.started
    }

    /// `button`: start button pressed now; `wire_closed`: start cable contact (None = no cable).
    pub fn update(&mut self, now: Instant, button: bool, wire_closed: Option<bool>) -> Gate {
        if self.started {
            return Gate::Go(None);
        }
        if self.cfg.methods.contains(&StartMethod::Wire) {
            match wire_closed {
                Some(true) => self.wire_armed = true,
                Some(false) if self.wire_armed => return self.go(StartMethod::Wire),
                _ => {}
            }
        }
        if self.cfg.methods.contains(&StartMethod::Button) {
            let released = self.button_down && !button;
            self.button_down = button;
            if released && self.countdown_until.is_none() {
                self.countdown_until = Some(now + self.cfg.countdown);
            }
        }
        match self.countdown_until {
            Some(until) if now >= until => self.go(StartMethod::Button),
            Some(until) => Gate::Countdown(until - now),
            None => Gate::Ready {
                wire_ready: self.wire_armed,
            },
        }
    }

    fn go(&mut self, method: StartMethod) -> Gate {
        self.started = true;
        Gate::Go(Some(method))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn gate(methods: &[StartMethod]) -> StartGate {
        StartGate::new(StartConfig {
            methods: methods.to_vec(),
            countdown: Duration::from_secs(3),
        })
    }

    #[test]
    fn button_starts_after_release_and_countdown() {
        let t0 = Instant::now();
        let mut g = gate(&[StartMethod::Button]);
        assert_eq!(g.update(t0, false, None), Gate::Ready { wire_ready: false });
        assert_eq!(g.update(t0, true, None), Gate::Ready { wire_ready: false });
        assert!(matches!(g.update(t0, false, None), Gate::Countdown(_)));
        assert!(matches!(
            g.update(t0 + Duration::from_millis(2900), true, None),
            Gate::Countdown(_)
        ));
        assert_eq!(
            g.update(t0 + Duration::from_secs(3), false, None),
            Gate::Go(Some(StartMethod::Button))
        );
        assert_eq!(
            g.update(t0 + Duration::from_secs(4), false, None),
            Gate::Go(None)
        );
    }

    #[test]
    fn wire_must_be_plugged_in_before_pulling_it_starts() {
        let t0 = Instant::now();
        let mut g = gate(&[StartMethod::Wire]);
        // never plugged in: an open contact does not start the car
        assert_eq!(
            g.update(t0, false, Some(false)),
            Gate::Ready { wire_ready: false }
        );
        assert_eq!(
            g.update(t0, false, Some(true)),
            Gate::Ready { wire_ready: true }
        );
        assert_eq!(
            g.update(t0, false, Some(false)),
            Gate::Go(Some(StartMethod::Wire))
        );
    }

    #[test]
    fn unconfigured_methods_are_ignored() {
        let t0 = Instant::now();
        let mut g = gate(&[StartMethod::Wire]);
        g.update(t0, true, None);
        assert_eq!(g.update(t0, false, None), Gate::Ready { wire_ready: false });
        let mut g = gate(&[StartMethod::Button]);
        g.update(t0, false, Some(true));
        assert_eq!(
            g.update(t0, false, Some(false)),
            Gate::Ready { wire_ready: false }
        );
    }
}
