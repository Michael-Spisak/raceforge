//! Safety limits applied by Rust after the controller, so controller code cannot bypass them (AC6).

use rf_proto::ipc::{Command, Mode};
use std::time::{Duration, Instant};

/// Final command gate: sanitize (finite, car limits), then the test-mode speed limit.
pub fn limit(
    cmd: Command,
    mode: Mode,
    max_steer_rad: f64,
    max_speed_m_s: f64,
    test_limit_m_s: f64,
) -> Command {
    let c = cmd.sanitized(max_steer_rad, max_speed_m_s);
    match mode {
        Mode::Race => c,
        _ => {
            let v = test_limit_m_s.abs().min(max_speed_m_s.abs());
            Command {
                speed_m_s: c.speed_m_s.clamp(-v, v),
                ..c
            }
        }
    }
}

/// What the teleop dead-man says this tick.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Teleop {
    /// No teleop: the controller drives.
    Off,
    /// Fresh teleop command: it overrides the controller.
    Drive(Command),
    /// Teleop was engaged but no message within the timeout: stop.
    Expired,
}

/// Teleop dead-man: every teleop message must be refreshed within `timeout` (spec: 300 ms).
#[derive(Debug, Clone)]
pub struct DeadMan {
    timeout: Duration,
    last: Option<(Instant, Command)>,
}

impl DeadMan {
    pub fn new(timeout: Duration) -> Self {
        Self {
            timeout,
            last: None,
        }
    }

    /// A teleop message arrived. Refused (returns `false`) in race mode.
    pub fn update(&mut self, now: Instant, cmd: Command, mode: Mode) -> bool {
        if mode == Mode::Race {
            return false;
        }
        self.last = Some((now, cmd));
        true
    }

    /// Operator released teleop: control goes back to the controller.
    pub fn release(&mut self) {
        self.last = None;
    }

    pub fn state(&self, now: Instant) -> Teleop {
        match self.last {
            None => Teleop::Off,
            Some((t, cmd)) if now.saturating_duration_since(t) <= self.timeout => {
                Teleop::Drive(cmd)
            }
            Some(_) => Teleop::Expired,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const C: Command = Command {
        steering_rad: 0.3,
        speed_m_s: 3.0,
    };

    #[test]
    fn test_mode_speed_limit() {
        let c = limit(C, Mode::Test, 0.5, 2.0, 1.0);
        assert_eq!(
            c,
            Command {
                steering_rad: 0.3,
                speed_m_s: 1.0
            }
        );
        let c = limit(
            Command {
                speed_m_s: -5.0,
                ..C
            },
            Mode::Test,
            0.5,
            2.0,
            1.0,
        );
        assert_eq!(c.speed_m_s, -1.0);
        // Test limit can never exceed the car maximum.
        assert_eq!(limit(C, Mode::Test, 0.5, 2.0, 9.0).speed_m_s, 2.0);
    }

    #[test]
    fn race_mode_lifts_test_limit_but_not_car_limit() {
        let c = limit(C, Mode::Race, 0.2, 2.0, 1.0);
        assert_eq!(
            c,
            Command {
                steering_rad: 0.2,
                speed_m_s: 2.0
            }
        );
    }

    #[test]
    fn non_finite_is_stop() {
        let c = limit(
            Command {
                steering_rad: f64::NAN,
                speed_m_s: 1.0,
            },
            Mode::Test,
            0.5,
            2.0,
            1.0,
        );
        assert_eq!(c, Command::default());
    }

    #[test]
    fn dead_man_expires() {
        let t0 = Instant::now();
        let mut d = DeadMan::new(Duration::from_millis(300));
        assert_eq!(d.state(t0), Teleop::Off);
        assert!(d.update(t0, C, Mode::Test));
        assert_eq!(d.state(t0 + Duration::from_millis(299)), Teleop::Drive(C));
        assert_eq!(d.state(t0 + Duration::from_millis(301)), Teleop::Expired);
        d.release();
        assert_eq!(d.state(t0 + Duration::from_millis(400)), Teleop::Off);
    }

    #[test]
    fn teleop_refused_in_race_mode() {
        let mut d = DeadMan::new(Duration::from_millis(300));
        assert!(!d.update(Instant::now(), C, Mode::Race));
        assert_eq!(d.state(Instant::now()), Teleop::Off);
    }
}
