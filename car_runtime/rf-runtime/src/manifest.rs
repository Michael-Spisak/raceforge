//! Deploy bundle manifest (`bundle.json`), written by `raceforge.car.bundle` (Python).
//! Field names and units mirror that module; unknown fields are rejected on both sides.

use crate::sha256::sha256_hex;
use rf_ev3::Ev3Config;
use rf_proto::ipc::RobotInfo;
use serde::Deserialize;
use std::collections::BTreeMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::time::Duration;
use thiserror::Error;

pub const MANIFEST: &str = "bundle.json";

#[derive(Debug, Error)]
pub enum BundleError {
    #[error("cannot read {0}: {1}")]
    Read(PathBuf, std::io::Error),
    #[error("invalid manifest: {0}")]
    Invalid(String),
    #[error("bundle file missing: {0}")]
    Missing(String),
    #[error("hash mismatch for {0}: the bundle was changed after it was built")]
    HashMismatch(String),
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FileRef {
    pub file: String,
    pub sha256: String,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Ev3Spec {
    pub addr: String,
    pub local: String,
    pub steer_motor: String,
    pub drive_motor: String,
    pub steer_motor_deg_per_rad: f64,
    pub drive_counts_per_m: f64,
    #[serde(default)]
    pub ultrasonic: BTreeMap<String, String>,
    pub gyro: bool,
    pub estop_touch_port: Option<String>,
    pub link_timeout_ms: u64,
    /// EV3 button that resumes after a fault when held (`rf_ev3::BUTTONS` names).
    #[serde(default = "default_resume_button")]
    pub resume_button: String,
    /// Firmware on the brick (spec 0011): `ev3dev` (Python bridge, UDP) or `ev3rt` (C bridge, serial).
    #[serde(default)]
    pub os: Ev3Os,
    #[serde(default)]
    pub link: Ev3LinkKind,
    /// Serial device for `uart` / `usb_cdc` / `bt_spp` (default per link kind).
    #[serde(default)]
    pub device: Option<String>,
    #[serde(default = "default_baud")]
    pub baud: u32,
    /// Gyro port for the EV3RT app (ev3dev finds the gyro itself).
    #[serde(default)]
    pub gyro_port: Option<String>,
    /// Motor loop gains of the EV3RT bridge; compiled into the EV3 app, not used by the runtime.
    #[serde(default)]
    pub gains: Option<Ev3Gains>,
}

fn default_resume_button() -> String {
    "enter".into()
}

fn default_baud() -> u32 {
    115_200
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Ev3Os {
    #[default]
    Ev3dev,
    Ev3rt,
}

/// Transport between the board and the EV3 (spec 0011).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Ev3LinkKind {
    #[default]
    Udp,
    Uart,
    UsbCdc,
    BtSpp,
}

impl Ev3LinkKind {
    pub fn default_device(self) -> Option<&'static str> {
        match self {
            Ev3LinkKind::Udp => None,
            Ev3LinkKind::Uart => Some("/dev/serial0"),
            Ev3LinkKind::UsbCdc => Some("/dev/ttyACM0"),
            Ev3LinkKind::BtSpp => Some("/dev/rfcomm0"),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Ev3Endpoint {
    Udp { local: SocketAddr, ev3: SocketAddr },
    Serial { device: String, baud: u32 },
}

#[derive(Debug, Clone, Copy, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Ev3Gains {
    pub drive_ff: f64,
    pub drive_kp: f64,
    pub drive_ki: f64,
    pub steer_kp: f64,
    pub steer_min_power: i32,
    pub steer_max_power: i32,
    pub steer_deadband_deg: i32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SensorPolicy {
    /// Missing data stops the car (fault).
    Critical,
    /// Missing data: continue at reduced speed.
    Optional,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LidarSpec {
    pub device: String,
    pub mount_offset_rad: f64,
    pub policy: SensorPolicy,
    pub timeout_ms: u64,
}

/// Live telemetry + teleop server (test mode only). Non-loopback binds need a token.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TelemetrySpec {
    pub bind: String,
    pub rate_hz: f64,
    pub token: Option<String>,
    pub max_clients: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum BundleMode {
    Test,
    Race,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RuntimeSpec {
    pub mode: BundleMode,
    pub deadline_ms: f64,
    pub test_speed_limit_m_s: Option<f64>,
    /// Extra USB `vendor:product` ids treated as radios in race mode (dongles that do not
    /// advertise the wireless USB class).
    #[serde(default)]
    pub radio_usb_ids: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    pub schema: String,
    pub schema_version: u32,
    pub name: String,
    pub created_wall_ns: i64,
    pub raceforge_version: String,
    pub controller: FileRef,
    pub params: Option<FileRef>,
    pub robot: RobotInfo,
    pub ev3: Ev3Spec,
    #[serde(default)]
    pub lidar: Option<LidarSpec>,
    #[serde(default)]
    pub telemetry: Option<TelemetrySpec>,
    pub runtime: RuntimeSpec,
}

fn motor_port(p: &str) -> Result<usize, BundleError> {
    "ABCD"
        .find(p)
        .filter(|_| p.len() == 1)
        .ok_or_else(|| BundleError::Invalid(format!("motor port {p:?}")))
}

fn sensor_port(p: &str) -> Result<usize, BundleError> {
    "1234"
        .find(p)
        .filter(|_| p.len() == 1)
        .ok_or_else(|| BundleError::Invalid(format!("sensor port {p:?}")))
}

impl Manifest {
    /// Read `bundle.json` from `dir` and check every listed file against its SHA-256.
    pub fn load_verified(dir: &Path) -> Result<Self, BundleError> {
        let path = dir.join(MANIFEST);
        let text = std::fs::read_to_string(&path).map_err(|e| BundleError::Read(path, e))?;
        let m: Self =
            serde_json::from_str(&text).map_err(|e| BundleError::Invalid(e.to_string()))?;
        if m.schema != "car_bundle" || m.schema_version != 1 {
            return Err(BundleError::Invalid(format!(
                "unsupported schema {} v{}",
                m.schema, m.schema_version
            )));
        }
        for r in std::iter::once(&m.controller).chain(m.params.iter()) {
            // Files must live inside the bundle directory.
            if r.file.contains('/') || r.file.contains('\\') || r.file.starts_with('.') {
                return Err(BundleError::Invalid(format!("file name {:?}", r.file)));
            }
            let data = std::fs::read(dir.join(&r.file))
                .map_err(|_| BundleError::Missing(r.file.clone()))?;
            if sha256_hex(&data) != r.sha256 {
                return Err(BundleError::HashMismatch(r.file.clone()));
            }
        }
        m.ev3_config()?;
        m.ev3_link()?;
        let usb_id = |s: &String| {
            s.len() == 9
                && s.as_bytes()[4] == b':'
                && s.chars()
                    .filter(|c| *c != ':')
                    .all(|c| c.is_ascii_hexdigit())
        };
        if !m.runtime.radio_usb_ids.iter().all(usb_id) {
            return Err(BundleError::Invalid(
                "radio_usb_ids must look like 0bda:8179".into(),
            ));
        }
        if let Some(t) = &m.telemetry {
            let bind: SocketAddr = t
                .bind
                .parse()
                .map_err(|_| BundleError::Invalid(format!("telemetry bind {:?}", t.bind)))?;
            let token_ok = t.token.as_ref().is_none_or(|k| {
                (16..=128).contains(&k.len())
                    && k.bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
            });
            if !token_ok {
                return Err(BundleError::Invalid(
                    "telemetry token: 16-128 of [A-Za-z0-9_-]".into(),
                ));
            }
            if !bind.ip().is_loopback() && t.token.is_none() {
                return Err(BundleError::Invalid(
                    "telemetry: a token is required for a non-loopback bind".into(),
                ));
            }
            if !(1.0..=50.0).contains(&t.rate_hz) || !(1..=16).contains(&t.max_clients) {
                return Err(BundleError::Invalid(
                    "telemetry rate_hz 1-50, max_clients 1-16".into(),
                ));
            }
        }
        if let Some(l) = &m.lidar {
            if !l.mount_offset_rad.is_finite() || !(150..=2000).contains(&l.timeout_ms) {
                return Err(BundleError::Invalid("lidar mount offset / timeout".into()));
            }
        }
        Ok(m)
    }

    pub fn ev3_config(&self) -> Result<Ev3Config, BundleError> {
        let e = &self.ev3;
        let ultrasonic = e
            .ultrasonic
            .iter()
            .map(|(name, port)| Ok((name.clone(), sensor_port(port)?)))
            .collect::<Result<_, BundleError>>()?;
        Ok(Ev3Config {
            steer_motor: motor_port(&e.steer_motor)?,
            drive_motor: motor_port(&e.drive_motor)?,
            steer_motor_deg_per_rad: e.steer_motor_deg_per_rad,
            drive_counts_per_m: e.drive_counts_per_m,
            ultrasonic,
            gyro: e.gyro,
            estop_touch_port: e.estop_touch_port.as_deref().map(sensor_port).transpose()?,
            link_timeout: Duration::from_millis(e.link_timeout_ms),
            resume_buttons: rf_ev3::button_mask(&e.resume_button).ok_or_else(|| {
                BundleError::Invalid(format!("resume button {:?}", e.resume_button))
            })?,
            ..Ev3Config::default()
        })
    }

    /// How to reach the EV3 (spec 0011): ev3dev uses UDP, EV3RT a serial link. Bluetooth is for
    /// testing only: a race-mode bundle with `bt_spp` is refused (radios must be off in a race).
    pub fn ev3_link(&self) -> Result<Ev3Endpoint, BundleError> {
        let e = &self.ev3;
        match (e.os, e.link) {
            (Ev3Os::Ev3dev, Ev3LinkKind::Udp) => {
                let (local, ev3) = self.ev3_addrs()?;
                Ok(Ev3Endpoint::Udp { local, ev3 })
            }
            (Ev3Os::Ev3dev, _) => Err(BundleError::Invalid(
                "ev3dev talks UDP over the USB gadget: ev3.link must be \"udp\"".into(),
            )),
            (Ev3Os::Ev3rt, Ev3LinkKind::Udp) => Err(BundleError::Invalid(
                "EV3RT has no network link: use ev3.link uart, usb_cdc or bt_spp".into(),
            )),
            (Ev3Os::Ev3rt, Ev3LinkKind::BtSpp) if self.runtime.mode == BundleMode::Race => {
                Err(BundleError::Invalid(
                    "race mode forbids radios: ev3.link bt_spp is for testing only".into(),
                ))
            }
            (Ev3Os::Ev3rt, kind) => {
                if !(9_600..=1_000_000).contains(&e.baud) {
                    return Err(BundleError::Invalid(format!("ev3.baud {}", e.baud)));
                }
                let device = e
                    .device
                    .clone()
                    .or_else(|| kind.default_device().map(str::to_owned))
                    .unwrap_or_default();
                Ok(Ev3Endpoint::Serial {
                    device,
                    baud: e.baud,
                })
            }
        }
    }

    /// (local bind address, EV3 address).
    pub fn ev3_addrs(&self) -> Result<(SocketAddr, SocketAddr), BundleError> {
        let parse = |s: &str| {
            s.parse::<SocketAddr>()
                .map_err(|_| BundleError::Invalid(format!("address {s:?}")))
        };
        Ok((parse(&self.ev3.local)?, parse(&self.ev3.addr)?))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    pub fn sample(controller_hash: &str) -> String {
        format!(
            r#"{{
  "schema": "car_bundle", "schema_version": 1, "name": "wf", "created_wall_ns": 1,
  "raceforge_version": "0.0.1",
  "controller": {{"file": "controller.py", "sha256": "{controller_hash}"}},
  "params": null,
  "robot": {{"car_name": "car", "sensors": ["front"], "max_steer_rad": 0.4, "max_speed_m_s": 1.5,
            "wheelbase_m": 0.2, "track_m": 0.15, "control_rate_hz": 50.0}},
  "ev3": {{"addr": "10.42.0.3:47100", "local": "0.0.0.0:47101", "steer_motor": "A", "drive_motor": "C",
          "steer_motor_deg_per_rad": 171.9, "drive_counts_per_m": 2046.0,
          "ultrasonic": {{"front": "1", "left": "2"}}, "gyro": true, "estop_touch_port": "4",
          "link_timeout_ms": 100}},
  "runtime": {{"mode": "test", "deadline_ms": 15.0, "test_speed_limit_m_s": null}}
}}"#
        )
    }

    fn bundle(name: &str, manifest: &str, controller: &[u8]) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("rf-bundle-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("dir");
        std::fs::write(dir.join(MANIFEST), manifest).expect("manifest");
        std::fs::write(dir.join("controller.py"), controller).expect("controller");
        dir
    }

    #[test]
    fn valid_bundle_maps_ports_and_units() {
        let dir = bundle("ok", &sample(&sha256_hex(b"code")), b"code");
        let m = Manifest::load_verified(&dir).expect("valid");
        let c = m.ev3_config().expect("ev3");
        assert_eq!((c.steer_motor, c.drive_motor), (0, 2));
        assert_eq!(c.ultrasonic["left"], 1);
        assert_eq!(c.estop_touch_port, Some(3));
        assert_eq!(c.link_timeout, Duration::from_millis(100));
        assert_eq!(m.robot.max_steer_rad, 0.4);
        assert_eq!(m.runtime.mode, BundleMode::Test);
    }

    /// Spec 0011 AC2: EV3RT bundles pick a serial link; ev3dev stays on UDP; BT is test-only.
    #[test]
    fn ev3_link_kinds() {
        let h = sha256_hex(b"code");
        let ev3 = |extra: &str, mode: &str| {
            sample(&h)
                .replace(
                    "\"link_timeout_ms\": 100}",
                    &format!("\"link_timeout_ms\": 100{extra}}}"),
                )
                .replace("\"mode\": \"test\"", &format!("\"mode\": \"{mode}\""))
        };
        let load =
            |name: &str, json: String| Manifest::load_verified(&bundle(name, &json, b"code"));
        let m = load("udp", ev3("", "test")).expect("default: ev3dev over UDP");
        assert!(matches!(
            m.ev3_link().expect("link"),
            Ev3Endpoint::Udp { .. }
        ));
        let m = load(
            "uart",
            ev3(", \"os\": \"ev3rt\", \"link\": \"uart\"", "test"),
        )
        .expect("uart");
        assert_eq!(
            m.ev3_link().expect("link"),
            Ev3Endpoint::Serial {
                device: "/dev/serial0".into(),
                baud: 115_200
            }
        );
        let m = load(
            "usb",
            ev3(", \"os\": \"ev3rt\", \"link\": \"usb_cdc\", \"device\": \"/dev/ttyACM1\", \"baud\": 230400", "race"),
        )
        .expect("usb cdc is wired: fine in race mode");
        assert_eq!(
            m.ev3_link().expect("link"),
            Ev3Endpoint::Serial {
                device: "/dev/ttyACM1".into(),
                baud: 230_400
            }
        );
        assert!(load(
            "bt-test",
            ev3(", \"os\": \"ev3rt\", \"link\": \"bt_spp\"", "test")
        )
        .is_ok());
        assert!(load(
            "bt-race",
            ev3(", \"os\": \"ev3rt\", \"link\": \"bt_spp\"", "race")
        )
        .is_err());
        assert!(load("dev-uart", ev3(", \"link\": \"uart\"", "test")).is_err());
        assert!(load("rt-udp", ev3(", \"os\": \"ev3rt\"", "test")).is_err());
        assert!(load(
            "baud",
            ev3(
                ", \"os\": \"ev3rt\", \"link\": \"uart\", \"baud\": 1",
                "test"
            )
        )
        .is_err());
        let gains = ", \"os\": \"ev3rt\", \"link\": \"uart\", \"gains\": {\"drive_ff\": 0.095, \"drive_kp\": 0.05, \"drive_ki\": 0.2, \"steer_kp\": 1.5, \"steer_min_power\": 8, \"steer_max_power\": 60, \"steer_deadband_deg\": 1}";
        assert!(load("gains", ev3(gains, "test"))
            .expect("gains")
            .ev3
            .gains
            .is_some());
    }

    #[test]
    fn lidar_section_is_optional_and_parsed() {
        let h = sha256_hex(b"code");
        let with = sample(&h).replace(
            "\"runtime\":",
            "\"lidar\": {\"device\": \"/dev/ttyUSB0\", \"mount_offset_rad\": 3.14, \"policy\": \"optional\", \"timeout_ms\": 300},\n  \"runtime\":",
        );
        let m = Manifest::load_verified(&bundle("lidar", &with, b"code")).expect("valid");
        let l = m.lidar.expect("lidar");
        assert_eq!((l.policy, l.timeout_ms), (SensorPolicy::Optional, 300));
        let bad = with.replace("\"timeout_ms\": 300}", "\"timeout_ms\": 5}");
        assert!(Manifest::load_verified(&bundle("lidar-bad", &bad, b"code")).is_err());
        assert!(
            Manifest::load_verified(&bundle("nolidar", &sample(&h), b"code"))
                .expect("ok")
                .lidar
                .is_none()
        );
    }

    #[test]
    fn radio_usb_ids_are_validated() {
        let h = sha256_hex(b"code");
        let with = |ids: &str| {
            sample(&h).replace(
                "\"test_speed_limit_m_s\": null",
                &format!("\"test_speed_limit_m_s\": null, \"radio_usb_ids\": {ids}"),
            )
        };
        let ok = Manifest::load_verified(&bundle("usb-ok", &with(r#"["0bda:8179"]"#), b"code"));
        assert_eq!(
            ok.expect("valid").runtime.radio_usb_ids,
            vec!["0bda:8179".to_string()]
        );
        for (i, bad) in [r#"["0bda8179"]"#, r#"["0bda:81"]"#, r#"["xyzw:8179"]"#]
            .iter()
            .enumerate()
        {
            assert!(
                Manifest::load_verified(&bundle(&format!("usb-bad{i}"), &with(bad), b"code"))
                    .is_err(),
                "{bad}"
            );
        }
    }

    #[test]
    fn telemetry_section_is_validated() {
        let h = sha256_hex(b"code");
        let with = |t: &str| {
            sample(&h).replace(
                "\"runtime\":",
                &format!("\"telemetry\": {t},\n  \"runtime\":"),
            )
        };
        let ok = r#"{"bind": "0.0.0.0:8765", "rate_hz": 20.0, "token": "abcdefghijklmnop", "max_clients": 4}"#;
        let m = Manifest::load_verified(&bundle("tel-ok", &with(ok), b"code")).expect("valid");
        assert_eq!(
            m.telemetry.expect("telemetry").token.as_deref(),
            Some("abcdefghijklmnop")
        );
        let loopback =
            r#"{"bind": "127.0.0.1:8765", "rate_hz": 20.0, "token": null, "max_clients": 4}"#;
        assert!(Manifest::load_verified(&bundle("tel-lo", &with(loopback), b"code")).is_ok());
        for (i, bad) in [
            r#"{"bind": "0.0.0.0:8765", "rate_hz": 20.0, "token": null, "max_clients": 4}"#,
            r#"{"bind": "0.0.0.0:8765", "rate_hz": 20.0, "token": "short", "max_clients": 4}"#,
            r#"{"bind": "car:8765", "rate_hz": 20.0, "token": null, "max_clients": 4}"#,
            r#"{"bind": "127.0.0.1:8765", "rate_hz": 500.0, "token": null, "max_clients": 4}"#,
        ]
        .iter()
        .enumerate()
        {
            assert!(
                Manifest::load_verified(&bundle(&format!("tel-bad{i}"), &with(bad), b"code"))
                    .is_err(),
                "{bad}"
            );
        }
    }

    #[test]
    fn tampered_controller_is_refused() {
        let dir = bundle("tampered", &sample(&sha256_hex(b"code")), b"code + edit");
        assert!(
            matches!(Manifest::load_verified(&dir), Err(BundleError::HashMismatch(f)) if f == "controller.py")
        );
    }

    #[test]
    fn bad_manifests_are_refused() {
        let h = sha256_hex(b"code");
        let cases = [
            sample(&h).replace("\"C\"", "\"E\""),
            sample(&h).replace("\"4\"", "\"9\""),
            sample(&h).replace("\"car_bundle\"", "\"other\""),
            sample(&h).replace(
                "\"link_timeout_ms\": 100",
                "\"link_timeout_ms\": 100, \"resume_button\": \"power\"",
            ),
            sample(&h).replace("\"controller.py\"", "\"../etc/passwd\""),
            sample(&h).replace("10.42.0.3:47100", "not-an-address"),
            sample(&h).replace("\"gyro\": true", "\"gyro\": true, \"extra\": 1"),
        ];
        for (i, text) in cases.iter().enumerate() {
            let dir = bundle(&format!("bad{i}"), text, b"code");
            assert!(Manifest::load_verified(&dir).is_err(), "case {i} accepted");
        }
    }
}
