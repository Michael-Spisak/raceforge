//! `TickRecord` -> `TelemetryFrame` JSON (spec 0001 `telemetry` v1, same as the simulator writes).
//!
//! The Python model is strict (`extra="forbid"`, no NaN/inf, key patterns), so everything that
//! could violate it is filtered here: non-finite numbers become `null` (optional fields) or are
//! dropped (channels), keys that do not match the patterns are dropped.

use rf_core::runtime::TickRecord;
use rf_proto::ipc::{ChannelValue, Mode};
use serde_json::{json, Map, Value};

const MAX_CHANNELS: usize = 64;

/// `^[a-z][a-z0-9_.]{0,63}$` (TelemetryFrame channel keys).
pub fn valid_channel_key(k: &str) -> bool {
    let b = k.as_bytes();
    !b.is_empty()
        && b.len() <= 64
        && b[0].is_ascii_lowercase()
        && b.iter()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'_' || *c == b'.')
}

/// `^[a-z0-9][a-z0-9_-]{0,63}$` (LocalId, used for sensor names).
pub fn valid_local_id(k: &str) -> bool {
    let b = k.as_bytes();
    !b.is_empty()
        && b.len() <= 64
        && (b[0].is_ascii_lowercase() || b[0].is_ascii_digit())
        && b.iter()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'_' || *c == b'-')
}

fn num(x: f64) -> Value {
    if x.is_finite() {
        json!(x)
    } else {
        Value::Null
    }
}

fn opt(x: Option<f64>) -> Value {
    x.map_or(Value::Null, num)
}

fn non_neg(x: Option<f64>) -> Value {
    x.filter(|v| v.is_finite() && *v >= 0.0)
        .map_or(Value::Null, |v| json!(v))
}

fn mode(m: Mode) -> &'static str {
    match m {
        Mode::Test => "test",
        Mode::Race => "race",
        Mode::Sim => "sim",
        Mode::Hil => "hil",
    }
}

pub fn telemetry_frame(r: &TickRecord) -> Value {
    let mut sensors = Map::new();
    let mut meas = json!({ "sensors": {} });
    let mut battery = Value::Null;
    if let Some(o) = &r.obs {
        for (name, d) in &o.ultrasonic_m {
            let key = format!("us_{name}");
            if valid_local_id(&key) {
                sensors.insert(key, json!({ "kind": "range", "distance_m": non_neg(*d) }));
            }
        }
        if let Some(rate) = o.yaw_rate_rad_s.filter(|v| v.is_finite()) {
            sensors.insert(
                "gyro".into(),
                json!({ "kind": "imu", "yaw_rate_rad_s": rate }),
            );
        }
        if !o.bumper.is_empty() {
            sensors.insert(
                "bumper".into(),
                json!({ "kind": "bool", "value": o.bumper.values().any(|b| *b) }),
            );
        }
        // LiDAR revolutions are not uniformly spaced; they go to /lidar_raw, not into the frame.
        meas = json!({
            "steering_rad": opt(o.steering_rad),
            "speed_m_s": opt(o.speed_m_s),
            "yaw_rate_rad_s": opt(o.yaw_rate_rad_s),
            "sensors": sensors,
        });
        battery = non_neg(o.battery_v);
    }
    let mut channels = Map::new();
    for (k, v) in &r.channels {
        if channels.len() >= MAX_CHANNELS || !valid_channel_key(k) {
            continue;
        }
        let v = match v {
            ChannelValue::Bool(b) => json!(b),
            ChannelValue::Int(i) => json!(i),
            ChannelValue::Float(f) if f.is_finite() => json!(f),
            ChannelValue::Float(_) => continue,
            ChannelValue::Text(s) => json!(s),
        };
        channels.insert(k.clone(), v);
    }
    let state: String = if r.state.is_empty() {
        "run".into()
    } else {
        r.state.chars().take(64).collect()
    };
    let mut t = json!({ "mono_ns": r.mono_ns });
    if let Some(off) = r.wall_offset_ns {
        t["wall_offset_ns"] = json!(off);
    }
    json!({
        "schema": "telemetry",
        "schema_version": 1,
        "t": t,
        "seq": r.seq,
        "mode": mode(r.mode),
        "state": state,
        "faults": r.faults,
        "cmd": {
            "steering_rad": if r.out.steering_rad.is_finite() { r.out.steering_rad } else { 0.0 },
            "speed_m_s": if r.out.stop || !r.out.speed_m_s.is_finite() { 0.0 } else { r.out.speed_m_s },
        },
        "meas": meas,
        "power": { "ev3_battery_v": battery },
        "loop": {
            "rate_hz": if r.rate_hz.is_finite() { r.rate_hz.max(0.0) } else { 0.0 },
            "jitter_ms": (r.lateness_us / 1000.0).max(0.0),
            "last_tick_ms": (r.tick_us / 1000.0).max(0.0),
            "deadline_misses": r.deadline_misses,
        },
        "channels": channels,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn key_patterns() {
        assert!(valid_channel_key("err.lateral_1"));
        assert!(!valid_channel_key("Err"));
        assert!(!valid_channel_key("1err"));
        assert!(!valid_channel_key("a-b"));
        assert!(!valid_channel_key(&"a".repeat(65)));
        assert!(valid_local_id("us_front"));
        assert!(valid_local_id("1-lidar"));
        assert!(!valid_local_id("us.front"));
    }
}
