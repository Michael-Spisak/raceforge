//! `rf-runtime`: runs a deploy bundle on the car (spec 0005).
//!
//! Start-up order: verify bundle hashes -> race mode: radio check (AC5), refuse to arm if any
//! radio may be active -> EV3 link up -> LiDAR delivering scans (if configured) -> controller host process ready
//! -> MCAP log open -> control loop. The motors
//! are commanded to stop at every step until the loop runs; any failure before that exits with
//! an error and the EV3 failsafe keeps the car stopped.

pub mod journal;
pub mod manifest;
pub mod radio;
pub mod sensors;
pub mod sha256;

use manifest::{BundleError, BundleMode, Manifest};
use rf_core::hw::Sensors;
use rf_core::link::{ControllerLink, LinkError};
use rf_core::runtime::{RunReport, Runtime, RuntimeConfig};
use rf_ev3::{Ev3Link, UdpTransport};
use rf_lidar::{Lidar, LidarConfig};
use rf_log::Logger;
use rf_proto::ipc::Mode;
use rf_telemetry::{TelemetryConfig, TelemetryServer};
use sensors::{CarSensors, LidarSource};
use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;
use std::time::{Duration, Instant};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum AppError {
    #[error(transparent)]
    Bundle(#[from] BundleError),
    #[error("race mode not armed, radios may be active: {}", .0.join("; "))]
    RadiosActive(Vec<String>),
    #[error(
        "EV3 not connected after {0:?} (is the EV3 program running and the USB cable plugged in?)"
    )]
    Ev3NotConnected(Duration),
    #[error("LiDAR {0}: {1}")]
    Lidar(String, String),
    #[error("controller host: {0}")]
    Controller(#[from] LinkError),
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
}

#[derive(Debug, Clone)]
pub struct Options {
    pub bundle: PathBuf,
    pub log_dir: PathBuf,
    /// Python interpreter with raceforge installed.
    pub python: String,
    /// Extra PYTHONPATH for the controller host (e.g. a source checkout's `src`).
    pub pythonpath: Option<PathBuf>,
    pub max_ticks: Option<u64>,
    pub ev3_wait: Duration,
    pub setup_timeout: Duration,
    /// File-system root for the race-mode radio check (`/` on the car; tests use a fake tree).
    /// Deliberately not a command-line option, so it cannot be pointed elsewhere on the board.
    pub sys_root: PathBuf,
}

impl Options {
    pub fn new(bundle: PathBuf) -> Self {
        Self {
            bundle,
            log_dir: PathBuf::from("logs"),
            python: "python3".into(),
            pythonpath: None,
            max_ticks: None,
            ev3_wait: Duration::from_secs(10),
            sys_root: PathBuf::from("/"),
            setup_timeout: Duration::from_secs(20),
        }
    }
}

pub struct Outcome {
    pub report: RunReport,
    pub log: PathBuf,
    pub manifest: Manifest,
}

pub fn run(opts: &Options, stop: &AtomicBool) -> Result<Outcome, AppError> {
    let manifest = Manifest::load_verified(&opts.bundle)?;
    // Race mode arms only when no radio can be active (spec 0005 AC5). Checked before anything
    // else starts, so a refused race run never opens the EV3 link or the controller.
    let mode = match manifest.runtime.mode {
        BundleMode::Test => Mode::Test,
        BundleMode::Race => {
            let violations = radio::check(&opts.sys_root, &manifest.runtime.radio_usb_ids);
            if !violations.is_empty() {
                return Err(AppError::RadiosActive(violations));
            }
            Mode::Race
        }
    };

    let (local, ev3_addr) = manifest.ev3_addrs()?;
    let ev3 = Arc::new(Ev3Link::start(
        manifest.ev3_config()?,
        Box::new(UdpTransport::new(local, ev3_addr)?),
    ));
    let t = Instant::now();
    while ev3.snapshot().link_lost.is_some() {
        if t.elapsed() > opts.ev3_wait {
            return Err(AppError::Ev3NotConnected(opts.ev3_wait));
        }
        std::thread::sleep(Duration::from_millis(20));
    }

    let lidar = match &manifest.lidar {
        None => None,
        Some(spec) => {
            let cfg = LidarConfig {
                mount_offset_rad: spec.mount_offset_rad,
                ..LidarConfig::default()
            };
            let lidar = Lidar::open_serial(std::path::Path::new(&spec.device), cfg)
                .map_err(|e| AppError::Lidar(spec.device.clone(), e.to_string()))?;
            let t = Instant::now();
            while lidar.latest().is_none() {
                if t.elapsed() > opts.ev3_wait {
                    let why = format!("no scan after {:?} ({:?})", opts.ev3_wait, lidar.stats());
                    return Err(AppError::Lidar(spec.device.clone(), why));
                }
                std::thread::sleep(Duration::from_millis(20));
            }
            Some(LidarSource {
                lidar: Arc::new(lidar),
                policy: spec.policy,
                timeout: Duration::from_millis(spec.timeout_ms),
            })
        }
    };
    let sensors = Arc::new(CarSensors {
        ev3: ev3.clone(),
        lidar,
    });

    // Starts a controller host process; also used by the resume button after a fault.
    let mut spawn_host = {
        let (python, pythonpath) = (opts.python.clone(), opts.pythonpath.clone());
        let controller = opts.bundle.join(&manifest.controller.file);
        let params = manifest.params.as_ref().map(|p| opts.bundle.join(&p.file));
        let timeout = opts.setup_timeout;
        let mut n = 0u32;
        move || -> Result<ControllerLink, LinkError> {
            n += 1;
            let mut host = Command::new(&python);
            host.args(["-m", "raceforge.car.host", "--controller"])
                .arg(&controller);
            if let Some(p) = &params {
                host.arg("--params").arg(p);
            }
            if let Some(pp) = &pythonpath {
                host.env("PYTHONPATH", pp);
            }
            let sock =
                std::env::temp_dir().join(format!("rf-runtime-{}-{n}.sock", std::process::id()));
            ControllerLink::spawn(host, &sock, timeout)
        }
    };
    let link = spawn_host()?;

    std::fs::create_dir_all(&opts.log_dir)?;
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or_default();
    let name: String = manifest
        .name
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
        .collect();
    let log_path = opts.log_dir.join(format!("run-{stamp}-{name}.mcap"));
    let logger = Arc::new(Logger::start(&log_path, 1024, Duration::from_millis(500))?);

    let mut cfg = RuntimeConfig::new(manifest.robot.clone(), mode);
    cfg.deadline = Duration::from_secs_f64(manifest.runtime.deadline_ms / 1000.0).min(cfg.period());
    if let Some(v) = manifest.runtime.test_speed_limit_m_s {
        cfg.test_speed_limit_m_s = v;
    }
    let mut rt = Runtime::new(cfg, sensors, ev3.clone(), link, opts.setup_timeout)?;
    rt.set_restart(Box::new(spawn_host));
    rt.add_sink(logger.clone());
    // Faults, resume and notes also go to stderr (the journal under systemd).
    let printer = Arc::new(journal::EventPrinter::start(Box::new(std::io::stderr())));
    rt.add_sink(printer.clone());
    // Every EV3 frame from here on goes to /ev3_raw, on the tick records' clock.
    let (start, raw_log) = (rt.clock_start(), logger.clone());
    ev3.set_raw_tap(Box::new(move |at, f| {
        let ns = at.saturating_duration_since(start).as_nanos();
        raw_log.ev3_raw(u64::try_from(ns).unwrap_or(u64::MAX), f);
    }));
    // Live telemetry + teleop: test mode only. In race mode nothing listens (spec 0005 AC5).
    if let (Mode::Test, Some(t)) = (mode, &manifest.telemetry) {
        let bind = t
            .bind
            .parse()
            .map_err(|_| BundleError::Invalid(format!("telemetry bind {:?}", t.bind)))?;
        let cfg = TelemetryConfig {
            bind,
            rate_hz: t.rate_hz,
            token: t.token.clone(),
            max_clients: t.max_clients,
        };
        let server = TelemetryServer::start(
            cfg,
            &manifest.robot.car_name,
            Mode::Test,
            rt.teleop.clone(),
            rt.remote.clone(),
        )?;
        let (root, deny) = (
            opts.sys_root.clone(),
            manifest.runtime.radio_usb_ids.clone(),
        );
        server.set_radio_check(Arc::new(move || radio::check(&root, &deny)));
        rt.add_sink(Arc::new(server));
    }
    let report = rt.run(stop, opts.max_ticks);
    drop(rt); // controller host shut down
    printer.close();
    logger.close()?;
    Ok(Outcome {
        report,
        log: log_path,
        manifest,
    })
}
