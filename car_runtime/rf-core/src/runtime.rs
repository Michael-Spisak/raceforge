//! The fixed-rate control loop (spec 0005 "Runtime loop").
//!
//! Each tick: sensor snapshot -> `Observation` -> controller host (deadline) -> sanitize ->
//! teleop dead-man -> speed limit -> actuators. Any controller problem (deadline miss, exception,
//! crash, protocol error) or the hardware e-stop stops the motors immediately and latches a
//! fault; the controller is killed and only restarted by a resume/new deploy (not by this loop).
//! A supervisor thread stops the motors if the loop itself stalls.

use crate::hw::{Actuators, DriveOutput, Sensors};
use crate::link::{ControllerLink, LinkError};
use crate::safety::{self, DeadMan, Teleop};
use rf_proto::ipc::{ChannelValue, Command, Mode, Observation, RobotInfo};
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

#[derive(Debug, Clone)]
pub struct RuntimeConfig {
    pub info: RobotInfo,
    pub mode: Mode,
    /// Per-step controller deadline (spec default 15 ms).
    pub deadline: Duration,
    /// Speed limit outside race mode (default: car maximum).
    pub test_speed_limit_m_s: f64,
    pub deadman_timeout: Duration,
    /// Supervisor stops the motors if a tick has not started for this long.
    pub stall_timeout: Duration,
}

impl RuntimeConfig {
    pub fn new(info: RobotInfo, mode: Mode) -> Self {
        let period = Duration::from_secs_f64(1.0 / info.control_rate_hz.clamp(20.0, 100.0));
        let deadline = Duration::from_millis(15).min(period);
        Self {
            test_speed_limit_m_s: info.max_speed_m_s,
            info,
            mode,
            deadline,
            deadman_timeout: Duration::from_millis(300),
            stall_timeout: period + deadline + Duration::from_millis(20),
        }
    }

    pub fn period(&self) -> Duration {
        Duration::from_secs_f64(1.0 / self.info.control_rate_hz.clamp(20.0, 100.0))
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum Fault {
    Deadline { seq: u64 },
    Controller { seq: u64, detail: String },
    LinkClosed { seq: u64 },
    Protocol { seq: u64, detail: String },
    EStop,
    LinkLost(String),
    LoopStall,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Event {
    pub t_s: f64,
    pub kind: String,
    pub detail: String,
}

/// Percentiles in microseconds.
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct Percentiles {
    pub p50_us: f64,
    pub p99_us: f64,
    pub max_us: f64,
}

impl Percentiles {
    pub fn of(samples: &[f64]) -> Self {
        if samples.is_empty() {
            return Self::default();
        }
        let mut s = samples.to_vec();
        s.sort_by(f64::total_cmp);
        let at = |q: f64| s[((s.len() - 1) as f64 * q).round() as usize];
        Self {
            p50_us: at(0.5),
            p99_us: at(0.99),
            max_us: s[s.len() - 1],
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct RunReport {
    pub ticks: u64,
    pub fault: Option<Fault>,
    pub events: Vec<Event>,
    /// Tick start lateness against the fixed schedule.
    pub jitter: Percentiles,
    /// Controller round trip (observation sent -> command received).
    pub step: Percentiles,
    pub last_channels: BTreeMap<String, ChannelValue>,
}

/// Everything that happened in one tick, for logging (rf-log) and live telemetry.
#[derive(Debug, Clone, PartialEq)]
pub struct TickRecord {
    /// Monotonic time since runtime start.
    pub mono_ns: u64,
    /// Wall clock (UTC epoch ns) = `mono_ns + wall_offset_ns`, if the clock was set.
    pub wall_offset_ns: Option<i64>,
    pub seq: u64,
    pub mode: Mode,
    /// `fault`, `teleop`, `deadman_stop` or the controller's `state` channel (default `run`).
    pub state: String,
    pub faults: Vec<String>,
    /// What was sent to the motors.
    pub out: DriveOutput,
    /// What the controller saw (`None` when sensors were not read, e.g. after a fault).
    pub obs: Option<Observation>,
    pub rate_hz: f64,
    pub lateness_us: f64,
    pub tick_us: f64,
    pub deadline_misses: u64,
    pub channels: BTreeMap<String, ChannelValue>,
}

/// Receives tick records and events. Called on the control thread: must never block
/// (hand the data to another thread and drop it when that thread falls behind).
pub trait TickSink: Send + Sync {
    fn tick(&self, rec: &TickRecord);
    fn event(&self, mono_ns: u64, e: &Event);
}

struct Outcome {
    obs: Option<Observation>,
    out: DriveOutput,
    state: &'static str,
}

pub struct Runtime<S: Sensors, A: Actuators + 'static> {
    cfg: RuntimeConfig,
    sensors: Arc<S>,
    act: Arc<A>,
    link: ControllerLink,
    /// Teleop input (fed by the telemetry server in test mode).
    pub teleop: Arc<Mutex<DeadMan>>,
    fault: Option<Fault>,
    events: Vec<Event>,
    start: Instant,
    seq: u64,
    lateness_us: Vec<f64>,
    step_us: Vec<f64>,
    last_channels: BTreeMap<String, ChannelValue>,
    sinks: Vec<Arc<dyn TickSink>>,
    wall_offset_ns: Option<i64>,
    deadline_misses: u64,
}

impl<S: Sensors, A: Actuators + 'static> Runtime<S, A> {
    /// Send `hello` to the controller host and wait for its `ready`.
    pub fn new(
        cfg: RuntimeConfig,
        sensors: Arc<S>,
        act: Arc<A>,
        mut link: ControllerLink,
        setup_timeout: Duration,
    ) -> Result<Self, LinkError> {
        act.send(DriveOutput::STOP);
        link.hello(&cfg.info, setup_timeout)?;
        Ok(Self {
            teleop: Arc::new(Mutex::new(DeadMan::new(cfg.deadman_timeout))),
            cfg,
            sensors,
            act,
            link,
            fault: None,
            events: Vec::new(),
            start: Instant::now(),
            seq: 0,
            lateness_us: Vec::new(),
            step_us: Vec::new(),
            last_channels: BTreeMap::new(),
            sinks: Vec::new(),
            wall_offset_ns: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .ok()
                .and_then(|d| i64::try_from(d.as_nanos()).ok()),
            deadline_misses: 0,
        })
    }

    /// Add a consumer of tick records and events (MCAP logger, telemetry server).
    pub fn add_sink(&mut self, sink: Arc<dyn TickSink>) {
        self.sinks.push(sink);
    }

    pub fn fault(&self) -> Option<&Fault> {
        self.fault.as_ref()
    }

    fn t_s(&self) -> f64 {
        self.start.elapsed().as_secs_f64()
    }

    fn event(&mut self, kind: &str, detail: String) {
        let mono_ns = self.start.elapsed().as_nanos() as u64;
        let e = Event {
            t_s: mono_ns as f64 * 1e-9,
            kind: kind.to_string(),
            detail,
        };
        for s in &self.sinks {
            s.event(mono_ns, &e);
        }
        self.events.push(e);
    }

    /// Stop the motors right now and latch `fault`.
    fn trip(&mut self, fault: Fault, kill_controller: bool) {
        self.act.send(DriveOutput::FAULT_STOP);
        if kill_controller {
            self.link.kill();
        }
        self.event("fault", format!("{fault:?}"));
        self.fault = Some(fault);
    }

    /// Run until `stop` is set or `max_ticks` ticks have run. Motors are stopped on return.
    pub fn run(&mut self, stop: &AtomicBool, max_ticks: Option<u64>) -> RunReport {
        let period = self.cfg.period();
        let heartbeat = Arc::new(AtomicU64::new(0));
        let stalled = Arc::new(AtomicBool::new(false));
        let done = Arc::new(AtomicBool::new(false));
        let supervisor = {
            let (heartbeat, stalled, done) = (heartbeat.clone(), stalled.clone(), done.clone());
            let act = self.act.clone();
            let (start, timeout) = (self.start, self.cfg.stall_timeout);
            std::thread::spawn(move || {
                while !done.load(Ordering::Acquire) {
                    let last = Duration::from_nanos(heartbeat.load(Ordering::Acquire));
                    if start.elapsed().saturating_sub(last) > timeout
                        && !stalled.swap(true, Ordering::AcqRel)
                    {
                        act.send(DriveOutput::FAULT_STOP);
                    }
                    std::thread::sleep(Duration::from_millis(2));
                }
            })
        };

        let t0 = Instant::now();
        let mut ticks = 0u64;
        let mut last_t = self.t_s();
        while !stop.load(Ordering::Acquire) && max_ticks.is_none_or(|m| ticks < m) {
            let scheduled = t0 + period * u32::try_from(ticks).unwrap_or(u32::MAX);
            let now = Instant::now();
            if scheduled > now {
                std::thread::sleep(scheduled - now);
            }
            let started = Instant::now();
            heartbeat.store(self.start.elapsed().as_nanos() as u64, Ordering::Release);
            self.lateness_us
                .push(started.saturating_duration_since(scheduled).as_secs_f64() * 1e6);
            if stalled.load(Ordering::Acquire) && self.fault.is_none() {
                self.trip(Fault::LoopStall, true);
            }
            let t = self.t_s();
            let outcome = self.tick(t, t - last_t);
            last_t = t;
            if !self.sinks.is_empty() {
                self.publish(ticks, t, outcome, started, scheduled);
            }
            ticks += 1;
        }

        done.store(true, Ordering::Release);
        let _ = supervisor.join();
        self.act.send(if self.fault.is_some() {
            DriveOutput::FAULT_STOP
        } else {
            DriveOutput::STOP
        });
        RunReport {
            ticks,
            fault: self.fault.clone(),
            events: self.events.clone(),
            jitter: Percentiles::of(&self.lateness_us),
            step: Percentiles::of(&self.step_us),
            last_channels: self.last_channels.clone(),
        }
    }

    fn publish(&self, tick: u64, t_s: f64, o: Outcome, started: Instant, scheduled: Instant) {
        let state = match (o.state, self.last_channels.get("state")) {
            ("run", Some(ChannelValue::Text(s))) if !s.is_empty() && s.len() <= 64 => s.clone(),
            (s, _) => s.to_string(),
        };
        let rec = TickRecord {
            mono_ns: (t_s * 1e9) as u64,
            wall_offset_ns: self.wall_offset_ns,
            seq: tick,
            mode: self.cfg.mode,
            state,
            faults: self.fault.iter().map(|f| format!("{f:?}")).collect(),
            out: o.out,
            obs: o.obs,
            rate_hz: self.cfg.info.control_rate_hz,
            lateness_us: started.saturating_duration_since(scheduled).as_secs_f64() * 1e6,
            tick_us: started.elapsed().as_secs_f64() * 1e6,
            deadline_misses: self.deadline_misses,
            channels: self.last_channels.clone(),
        };
        for s in &self.sinks {
            s.tick(&rec);
        }
    }

    fn tick(&mut self, t_s: f64, dt_s: f64) -> Outcome {
        let fault_stop = |obs| Outcome {
            obs,
            out: DriveOutput::FAULT_STOP,
            state: "fault",
        };
        if self.fault.is_some() {
            // Keep commanding stop so the EV3 failsafe never sees a gap.
            self.act.send(DriveOutput::FAULT_STOP);
            return fault_stop(None);
        }
        let snap = self.sensors.snapshot();
        if snap.estop {
            self.trip(Fault::EStop, false);
            return fault_stop(None);
        }
        if let Some(what) = snap.link_lost {
            self.trip(Fault::LinkLost(what), false);
            return fault_stop(None);
        }
        let obs = Observation {
            t_s,
            dt_s,
            ultrasonic_m: snap.ultrasonic_m,
            lidar: snap.lidar,
            yaw_rate_rad_s: snap.yaw_rate_rad_s,
            heading_rad: snap.heading_rad,
            speed_m_s: snap.speed_m_s,
            steering_rad: snap.steering_rad,
            bumper: snap.bumper,
            battery_v: snap.battery_v,
            pose_estimate: None,
            mode: self.cfg.mode,
        };
        let seq = self.seq;
        self.seq += 1;
        let sent = Instant::now();
        let logged_obs = (!self.sinks.is_empty()).then(|| obs.clone());
        let cmd = match self.link.step(seq, obs, sent + self.cfg.deadline) {
            Ok(reply) => {
                self.step_us.push(sent.elapsed().as_secs_f64() * 1e6);
                for n in &reply.notes {
                    self.event("note", n.text.clone());
                }
                self.last_channels = reply.channels;
                reply.cmd
            }
            Err(e) => {
                let fault = match e {
                    LinkError::Timeout => {
                        self.deadline_misses += 1;
                        Fault::Deadline { seq }
                    }
                    LinkError::Controller(detail) => Fault::Controller { seq, detail },
                    LinkError::Closed => Fault::LinkClosed { seq },
                    LinkError::Protocol(detail) => Fault::Protocol { seq, detail },
                    LinkError::Io(err) => Fault::Protocol {
                        seq,
                        detail: err.to_string(),
                    },
                };
                self.trip(fault, true);
                return fault_stop(logged_obs);
            }
        };
        let teleop = self
            .teleop
            .lock()
            .map(|d| d.state(Instant::now()))
            .unwrap_or(Teleop::Expired);
        let cmd = match teleop {
            Teleop::Off => cmd,
            Teleop::Drive(t) => t,
            Teleop::Expired => Command {
                steering_rad: cmd.steering_rad,
                speed_m_s: 0.0,
            },
        };
        let info = &self.cfg.info;
        let c = safety::limit(
            cmd,
            self.cfg.mode,
            info.max_steer_rad,
            info.max_speed_m_s,
            self.cfg.test_speed_limit_m_s,
        );
        let out = DriveOutput {
            steering_rad: c.steering_rad,
            speed_m_s: c.speed_m_s,
            stop: teleop == Teleop::Expired,
            fault: false,
        };
        self.act.send(out);
        Outcome {
            obs: logged_obs,
            out,
            state: match teleop {
                Teleop::Off => "run",
                Teleop::Drive(_) => "teleop",
                Teleop::Expired => "deadman_stop",
            },
        }
    }
}
