//! The car's sensor set: EV3 (critical) plus an optional LiDAR with its bundle policy.

use crate::manifest::SensorPolicy;
use rf_core::hw::{SensorSnapshot, Sensors};
use rf_lidar::Lidar;
use std::sync::Arc;
use std::time::Duration;

pub struct LidarSource {
    pub lidar: Arc<Lidar>,
    pub policy: SensorPolicy,
    /// No new revolution for this long counts as missing.
    pub timeout: Duration,
}

pub struct CarSensors<E: Sensors> {
    pub ev3: Arc<E>,
    pub lidar: Option<LidarSource>,
}

impl<E: Sensors> Sensors for CarSensors<E> {
    fn snapshot(&self) -> SensorSnapshot {
        let mut snap = self.ev3.snapshot();
        let Some(src) = &self.lidar else { return snap };
        match src.lidar.latest() {
            Some((at, scan)) if at.elapsed() <= src.timeout => {
                snap.lidar = Some(scan);
                snap.lidar_at = Some(at);
            }
            latest => {
                let why = match latest {
                    Some((at, _)) => format!("lidar: no scan for {} ms", at.elapsed().as_millis()),
                    None => "lidar: no scan yet".to_string(),
                };
                match src.policy {
                    SensorPolicy::Critical => {
                        snap.link_lost.get_or_insert(why);
                    }
                    SensorPolicy::Optional => snap.degraded.push("lidar".into()),
                }
            }
        }
        snap
    }
}
