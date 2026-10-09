//! Hardware boundary of the control loop.
//!
//! Sensor threads (EV3 link, LiDAR) keep a latest-value [`SensorSnapshot`] up to date; the loop
//! reads it once per tick. Outputs go through [`Actuators`], which the EV3 link client implements
//! (SI units here; conversion to motor degrees / tacho counts happens in the EV3 adapter).

use rf_proto::ipc::LidarScan;
use std::collections::BTreeMap;
use std::sync::Mutex;
use std::time::Instant;

/// Latest readings of all sensors, already in SI units and the controller's conventions.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct SensorSnapshot {
    pub ultrasonic_m: BTreeMap<String, Option<f64>>,
    pub lidar: Option<LidarScan>,
    pub yaw_rate_rad_s: Option<f64>,
    pub heading_rad: Option<f64>,
    pub speed_m_s: Option<f64>,
    pub steering_rad: Option<f64>,
    pub bumper: BTreeMap<String, bool>,
    pub battery_v: Option<f64>,
    /// Hardware emergency stop input is active.
    pub estop: bool,
    /// The resume button is pressed (restart after a fault when held long enough).
    pub resume: bool,
    /// The start button is pressed (spec 0031; same button as resume by default).
    pub start_button: bool,
    /// Start cable contact: `Some(true)` plugged in, `Some(false)` pulled, `None` no cable.
    pub start_wire: Option<bool>,
    /// A critical sensor link (e.g. the EV3) delivered no valid data within its timeout.
    pub link_lost: Option<String>,
    /// When the LiDAR revolution in `lidar` finished (the runtime converts it to `t_s`).
    pub lidar_at: Option<Instant>,
    /// Optional sensors that are currently missing: the runtime halves the speed (spec 0005
    /// sensor policy `optional`).
    pub degraded: Vec<String>,
}

pub trait Sensors: Send + Sync {
    fn snapshot(&self) -> SensorSnapshot;
}

/// What the loop sends to the motors.
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct DriveOutput {
    pub steering_rad: f64,
    pub speed_m_s: f64,
    /// Brake now (drive target ignored).
    pub stop: bool,
    /// A fault is latched (shown on the EV3 LCD).
    pub fault: bool,
}

impl DriveOutput {
    pub const STOP: Self = Self {
        steering_rad: 0.0,
        speed_m_s: 0.0,
        stop: true,
        fault: false,
    };
    pub const FAULT_STOP: Self = Self {
        steering_rad: 0.0,
        speed_m_s: 0.0,
        stop: true,
        fault: true,
    };
}

pub trait Actuators: Send + Sync {
    fn send(&self, out: DriveOutput);
}

/// In-process mock sensor store (tests, AC3).
#[derive(Debug, Default)]
pub struct MockSensors(pub Mutex<SensorSnapshot>);

impl MockSensors {
    pub fn new(s: SensorSnapshot) -> Self {
        Self(Mutex::new(s))
    }
    pub fn set(&self, s: SensorSnapshot) {
        if let Ok(mut g) = self.0.lock() {
            *g = s;
        }
    }
}

impl Sensors for MockSensors {
    fn snapshot(&self) -> SensorSnapshot {
        self.0.lock().map(|g| g.clone()).unwrap_or_default()
    }
}

/// Records every output with its time (tests, AC3/AC4/AC6).
#[derive(Debug, Default)]
pub struct RecordingActuators(pub Mutex<Vec<(Instant, DriveOutput)>>);

impl RecordingActuators {
    pub fn outputs(&self) -> Vec<(Instant, DriveOutput)> {
        self.0.lock().map(|g| g.clone()).unwrap_or_default()
    }
}

impl Actuators for RecordingActuators {
    fn send(&self, out: DriveOutput) {
        if let Ok(mut g) = self.0.lock() {
            g.push((Instant::now(), out));
        }
    }
}
