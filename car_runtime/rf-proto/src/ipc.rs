//! JSON messages between the Rust core and the Python controller host (ADR-0014).
//!
//! Transport: Unix domain socket, one JSON object per line (newline-delimited JSON).
//! Field names and units mirror `raceforge.control.types` (spec 0004) exactly, so the host can
//! build `Observation` / read `Command` without any mapping:
//!
//! ```text
//! core -> host: {"type":"hello","info":{...RobotInfo}}      once, before the first step
//!               {"type":"obs","seq":n,"obs":{...Observation}} every tick
//!               {"type":"shutdown"}                          controller teardown, then exit
//! host -> core: {"type":"ready"}                             after Controller.setup()
//!               {"type":"cmd","seq":n,"cmd":{...},"channels":{...},"notes":[...]}
//!               {"type":"error","seq":n,"detail":"traceback"} controller raised
//! ```

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Controller mode, same strings as `raceforge.core.telemetry.Mode`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    #[default]
    Test,
    Race,
    Sim,
    Hil,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RobotInfo {
    pub car_name: String,
    pub sensors: Vec<String>,
    pub max_steer_rad: f64,
    pub max_speed_m_s: f64,
    pub wheelbase_m: f64,
    pub track_m: f64,
    pub control_rate_hz: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LidarScan {
    pub angles_rad: Vec<f64>,
    pub ranges_m: Vec<Option<f64>>,
    pub t_s: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PoseEstimate {
    pub x_m: f64,
    pub y_m: f64,
    pub heading_rad: f64,
    pub confidence: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct Observation {
    pub t_s: f64,
    pub dt_s: f64,
    #[serde(default)]
    pub ultrasonic_m: BTreeMap<String, Option<f64>>,
    #[serde(default)]
    pub lidar: Option<LidarScan>,
    #[serde(default)]
    pub yaw_rate_rad_s: Option<f64>,
    #[serde(default)]
    pub heading_rad: Option<f64>,
    #[serde(default)]
    pub speed_m_s: Option<f64>,
    #[serde(default)]
    pub steering_rad: Option<f64>,
    #[serde(default)]
    pub bumper: BTreeMap<String, bool>,
    #[serde(default)]
    pub battery_v: Option<f64>,
    #[serde(default)]
    pub pose_estimate: Option<PoseEstimate>,
    #[serde(default)]
    pub mode: Mode,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize, Default)]
pub struct Command {
    pub steering_rad: f64,
    pub speed_m_s: f64,
}

impl Command {
    /// Validate and clamp a controller command: non-finite values become a stop command,
    /// steering is clamped to `max_steer_rad`, speed to `[-max_speed, max_speed]`.
    pub fn sanitized(self, max_steer_rad: f64, max_speed_m_s: f64) -> Self {
        if !(self.steering_rad.is_finite() && self.speed_m_s.is_finite()) {
            return Self::default();
        }
        let s = max_steer_rad.abs();
        let v = max_speed_m_s.abs();
        Self {
            steering_rad: self.steering_rad.clamp(-s, s),
            speed_m_s: self.speed_m_s.clamp(-v, v),
        }
    }
}

/// A telemetry channel value emitted by the controller (`RobotIO.emit`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum ChannelValue {
    Bool(bool),
    Int(i64),
    Float(f64),
    Text(String),
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Note {
    pub text: String,
    #[serde(default)]
    pub tags: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum ToHost {
    Hello { info: RobotInfo },
    Obs { seq: u64, obs: Observation },
    Shutdown,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum FromHost {
    Ready,
    Cmd {
        seq: u64,
        cmd: Command,
        #[serde(default)]
        channels: BTreeMap<String, ChannelValue>,
        #[serde(default)]
        notes: Vec<Note>,
    },
    Error {
        seq: Option<u64>,
        detail: String,
    },
}

/// Serialise a message as one line (with trailing newline).
pub fn to_line<T: Serialize>(msg: &T) -> serde_json::Result<String> {
    let mut s = serde_json::to_string(msg)?;
    s.push('\n');
    Ok(s)
}

pub fn from_line<'a, T: Deserialize<'a>>(line: &'a str) -> serde_json::Result<T> {
    serde_json::from_str(line.trim_end())
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    #[test]
    fn obs_roundtrip_and_shape() {
        let mut us = BTreeMap::new();
        us.insert("front".to_string(), Some(0.42));
        us.insert("left".to_string(), None);
        let msg = ToHost::Obs {
            seq: 7,
            obs: Observation {
                t_s: 1.5,
                dt_s: 0.02,
                ultrasonic_m: us,
                lidar: Some(LidarScan {
                    angles_rad: vec![0.0, 1.0],
                    ranges_m: vec![Some(1.0), None],
                    t_s: 1.49,
                }),
                heading_rad: Some(0.1),
                ..Default::default()
            },
        };
        let line = to_line(&msg).expect("serialises");
        assert!(line.ends_with('\n') && !line[..line.len() - 1].contains('\n'));
        let v: serde_json::Value = serde_json::from_str(&line).expect("json");
        assert_eq!(v["type"], "obs");
        assert_eq!(v["obs"]["mode"], "test");
        assert_eq!(v["obs"]["ultrasonic_m"]["left"], serde_json::Value::Null);
        assert_eq!(from_line::<ToHost>(&line).expect("parses"), msg);
    }

    #[test]
    fn parses_host_messages() {
        let cmd: FromHost = from_line(
            r#"{"type":"cmd","seq":3,"cmd":{"steering_rad":0.1,"speed_m_s":1.0},"channels":{"err":0.5,"state":"run","n":2,"ok":true}}"#,
        )
        .expect("cmd");
        let FromHost::Cmd {
            seq,
            cmd,
            channels,
            notes,
        } = cmd
        else {
            panic!("not a cmd")
        };
        assert_eq!(seq, 3);
        assert_eq!(
            cmd,
            Command {
                steering_rad: 0.1,
                speed_m_s: 1.0
            }
        );
        assert_eq!(channels["err"], ChannelValue::Float(0.5));
        assert_eq!(channels["n"], ChannelValue::Int(2));
        assert_eq!(channels["ok"], ChannelValue::Bool(true));
        assert_eq!(channels["state"], ChannelValue::Text("run".into()));
        assert!(notes.is_empty());
        assert_eq!(
            from_line::<FromHost>(r#"{"type":"ready"}"#).expect("ready"),
            FromHost::Ready
        );
        assert!(from_line::<FromHost>(r#"{"type":"nope"}"#).is_err());
    }

    #[test]
    fn sanitize_non_finite_is_stop() {
        let c = Command {
            steering_rad: f64::NAN,
            speed_m_s: 1.0,
        }
        .sanitized(0.5, 2.0);
        assert_eq!(c, Command::default());
        let c = Command {
            steering_rad: 0.2,
            speed_m_s: f64::INFINITY,
        }
        .sanitized(0.5, 2.0);
        assert_eq!(c, Command::default());
    }

    proptest! {
        #[test]
        fn sanitized_is_always_within_limits(s: f64, v: f64, ms in 0.0f64..1.0, mv in 0.0f64..10.0) {
            let c = Command { steering_rad: s, speed_m_s: v }.sanitized(ms, mv);
            prop_assert!(c.steering_rad.is_finite() && c.speed_m_s.is_finite());
            prop_assert!(c.steering_rad.abs() <= ms && c.speed_m_s.abs() <= mv);
        }

        #[test]
        fn garbage_lines_never_panic(s in ".{0,200}") {
            let _ = from_line::<FromHost>(&s);
        }
    }
}
