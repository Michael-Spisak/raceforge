//! The fixed-rate control loop (spec 0005 "Runtime loop").
//!
//! Each tick: sensor snapshot -> `Observation` -> controller host (deadline) -> sanitize ->
//! teleop dead-man -> speed limit -> actuators. Any controller problem (deadline miss, exception,
//! crash, protocol error) or the hardware e-stop stops the motors immediately and latches a
//! fault; the controller is killed and only restarted by a resume/new deploy (not by this loop).
//! A supervisor thread stops the motors if the loop itself stalls.

use crate::hw::{Actuators, DriveOutput, SensorSnapshot, Sensors};
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
    /// How long the resume button must be held to restart after a fault.
    pub resume_hold: Duration,
    /// How long a restarted controller host may take until it is ready.
    pub restart_timeout: Duration,
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
            resume_hold: Duration::from_secs(1),
            restart_timeout: Duration::from_secs(20),
        }
    }

    pub fn period(&self) -> Duration {
        Duration::from_secs_f64(1.0 / self.info.control_rate_hz.clamp(20.0, 100.0))
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum Fault {
    Deadline {
        seq: u64,
    },
    Controller {
        seq: u64,
        detail: String,
    },
    LinkClosed {
        seq: u64,
    },
    Protocol {
        seq: u64,
        detail: String,
    },
    EStop,
    LinkLost(String),
    LoopStall,
    /// Stop requested by an operator (telemetry/teleop client), latched like any fault.
    OperatorStop(String),
}

/// Requests from outside the control thread (telemetry server), picked up at the next tick.
#[derive(Debug, Default)]
pub struct Remote {
    stop: std::sync::atomic::AtomicBool,
    stop_reason: Mutex<String>,
    notes: Mutex<Vec<String>>,
}

impl Remote {
    /// Stop the car now (next tick, at most one control period away) and latch a fault.
    pub fn request_stop(&self, reason: &str) {
        if let Ok(mut r) = self.stop_reason.lock() {
            reason.clone_into(&mut r);
        }
        self.stop.store(true, Ordering::Release);
    }

    /// A stop was requested and the runtime has not picked it up yet.
    pub fn stop_pending(&self) -> bool {
        self.stop.load(Ordering::Acquire)
    }

    /// Add a note to the run log (`/events`, kind `note`).
    pub fn note(&self, text: &str) {
        if let Ok(mut n) = self.notes.lock() {
            n.push(text.to_string());
        }
    }
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

/// Starts a fresh controller host (used to restart the controller after a fault).
pub type ControllerFactory = Box<dyn FnMut() -> Result<ControllerLink, LinkError> + Send>;

/// Resume button handling: hold time, one trigger per press, restart in the background.
#[derive(Default)]
struct Resume {
    factory: Option<Arc<Mutex<ControllerFactory>>>,
    held_since: Option<Instant>,
    fired: bool,
    pending: Option<std::thread::JoinHandle<Result<ControllerLink, LinkError>>>,
}

struct Outcome {
    obs: Option<Observation>,
    out: DriveOutput,
    state: &'static str,
}

/// Speed factor while an optional sensor is missing (spec 0005 sensor policy).
pub const DEGRADED_SPEED_FACTOR: f64 = 0.5;

pub struct Runtime<S: Sensors, A: Actuators + 'static> {
    cfg: RuntimeConfig,
    sensors: Arc<S>,
    act: Arc<A>,
    link: ControllerLink,
    /// Teleop input (fed by the telemetry server in test mode).
    pub teleop: Arc<Mutex<DeadMan>>,
    /// Operator stop and notes (fed by the telemetry server in test mode).
    pub remote: Arc<Remote>,
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
    degraded: Vec<String>,
    stalled: Arc<AtomicBool>,
    resume: Resume,
    start_gate: Option<crate::start::StartGate>,
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
            remote: Arc::new(Remote::default()),
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
            degraded: Vec::new(),
            stalled: Arc::new(AtomicBool::new(false)),
            resume: Resume::default(),
            start_gate: None,
        })
    }

    /// Origin of `mono_ns` in tick records and events (to timestamp other log sources).
    pub fn clock_start(&self) -> Instant {
        self.start
    }

    /// Allow the resume button to restart the controller after a fault (spec 0005: the
    /// controller is restarted only by the resume button or a new deploy).
    pub fn set_restart(&mut self, factory: ControllerFactory) {
        self.resume.factory = Some(Arc::new(Mutex::new(factory)));
    }

    /// Wait for a start signal before the controller drives (spec 0031). Without this the car
    /// drives from the first tick.
    pub fn set_start(&mut self, cfg: crate::start::StartConfig) {
        self.start_gate = Some(crate::start::StartGate::new(cfg));
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

    /// While a fault is latched: watch the resume button, restart the controller host in the
    /// background (the loop keeps ticking and commanding stop), clear the fault once it is ready.
    fn handle_resume(&mut self, snap: &SensorSnapshot) {
        if let Some(h) = self.resume.pending.take() {
            if !h.is_finished() {
                self.resume.pending = Some(h);
                return;
            }
            match h.join() {
                Ok(Ok(link)) => {
                    let old = std::mem::replace(&mut self.link, link);
                    drop(old); // ends the previous host process, if any
                    let prev = self
                        .fault
                        .take()
                        .map(|f| format!("{f:?}"))
                        .unwrap_or_default();
                    self.stalled.store(false, Ordering::Release);
                    if let Ok(mut d) = self.teleop.lock() {
                        d.release(); // a stale teleop session must not take over
                    }
                    self.event("resumed", prev);
                }
                Ok(Err(e)) => self.event("resume_failed", e.to_string()),
                Err(_) => self.event("resume_failed", "restart thread panicked".into()),
            }
            return;
        }
        if !snap.resume {
            self.resume.held_since = None;
            self.resume.fired = false;
            return;
        }
        let since = *self.resume.held_since.get_or_insert_with(Instant::now);
        if self.resume.fired || since.elapsed() < self.cfg.resume_hold {
            return;
        }
        self.resume.fired = true; // one trigger per press: release before trying again
        let refused = if snap.estop {
            Some("e-stop is pressed".to_string())
        } else if let Some(what) = &snap.link_lost {
            Some(format!("sensor link lost: {what}"))
        } else if self.resume.factory.is_none() {
            Some("no controller restart available (redeploy)".to_string())
        } else {
            None
        };
        if let Some(why) = refused {
            self.event("resume_refused", why);
            return;
        }
        let Some(factory) = self.resume.factory.clone() else {
            return;
        };
        self.link.kill();
        let (info, timeout) = (self.cfg.info.clone(), self.cfg.restart_timeout);
        self.resume.pending = Some(std::thread::spawn(move || {
            let mut link = {
                let mut f = factory
                    .lock()
                    .map_err(|_| LinkError::Protocol("factory poisoned".into()))?;
                (*f)()?
            };
            link.hello(&info, timeout)?;
            Ok(link)
        }));
        self.event("resuming", "resume button held".into());
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
        let stalled = self.stalled.clone();
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
        let notes: Vec<String> = self
            .remote
            .notes
            .lock()
            .map(|mut n| std::mem::take(&mut *n))
            .unwrap_or_default();
        for n in notes {
            self.event("note", n);
        }
        if self.fault.is_none() && self.remote.stop.swap(false, Ordering::AcqRel) {
            let reason = self
                .remote
                .stop_reason
                .lock()
                .map(|r| r.clone())
                .unwrap_or_default();
            self.trip(Fault::OperatorStop(reason), true);
        }
        if self.fault.is_some() {
            // Keep commanding stop so the EV3 failsafe never sees a gap.
            self.act.send(DriveOutput::FAULT_STOP);
            let snap = self.sensors.snapshot();
            self.handle_resume(&snap);
            if self.fault.is_some() {
                return Outcome {
                    obs: None,
                    out: DriveOutput::FAULT_STOP,
                    state: if self.resume.pending.is_some() {
                        "resuming"
                    } else {
                        "fault"
                    },
                };
            }
            // Resumed: the fresh controller drives from the next tick on.
            return Outcome {
                obs: None,
                out: DriveOutput::STOP,
                state: "resumed",
            };
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
        if let Some(gate) = self.start_gate.as_mut() {
            use crate::start::Gate;
            match gate.update(Instant::now(), snap.start_button, snap.start_wire) {
                Gate::Ready { wire_ready } => {
                    self.act.send(DriveOutput::STOP);
                    let state = if wire_ready { "ready_wire" } else { "ready" };
                    return Outcome {
                        obs: None,
                        out: DriveOutput::STOP,
                        state,
                    };
                }
                Gate::Countdown(_) => {
                    self.act.send(DriveOutput::STOP);
                    return Outcome {
                        obs: None,
                        out: DriveOutput::STOP,
                        state: "countdown",
                    };
                }
                Gate::Go(Some(method)) => {
                    self.event("start", format!("{method:?}").to_lowercase());
                }
                Gate::Go(None) => {}
            }
        }
        if snap.degraded != self.degraded {
            let detail = if snap.degraded.is_empty() {
                "all sensors back".to_string()
            } else {
                snap.degraded.join(", ")
            };
            self.event("degraded", detail);
            self.degraded = snap.degraded.clone();
        }
        // Sensors report when a scan finished; the controller gets it in the runtime's time base.
        let lidar = snap.lidar.map(|mut l| {
            if let Some(at) = snap.lidar_at {
                l.t_s = if at >= self.start {
                    at.duration_since(self.start).as_secs_f64()
                } else {
                    -self.start.duration_since(at).as_secs_f64()
                };
            }
            l
        });
        let obs = Observation {
            t_s,
            dt_s,
            ultrasonic_m: snap.ultrasonic_m,
            lidar,
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
        let degraded = !self.degraded.is_empty();
        let out = DriveOutput {
            steering_rad: c.steering_rad,
            speed_m_s: if degraded {
                c.speed_m_s * DEGRADED_SPEED_FACTOR
            } else {
                c.speed_m_s
            },
            stop: teleop == Teleop::Expired,
            fault: false,
        };
        self.act.send(out);
        Outcome {
            obs: logged_obs,
            out,
            state: match teleop {
                Teleop::Off if degraded => "degraded",
                Teleop::Off => "run",
                Teleop::Drive(_) => "teleop",
                Teleop::Expired => "deadman_stop",
            },
        }
    }
}
