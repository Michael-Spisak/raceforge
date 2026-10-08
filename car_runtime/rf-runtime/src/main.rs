//! `rf-runtime --bundle DIR [--log-dir DIR] [--python PY] [--pythonpath DIR] [--ticks N]
//!            [--ev3-wait-s S]`
//!
//! Runs until `--ticks` control steps have run, or until the process is stopped. When the
//! process is killed the EV3 failsafe stops the motors within 150 ms and the MCAP log stays
//! readable up to the last complete record.

use rf_runtime::{run, AppError, Options};
use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::atomic::AtomicBool;
use std::time::Duration;

const USAGE: &str =
    "usage: rf-runtime --bundle DIR [--log-dir DIR] [--python PY] [--pythonpath DIR] \
                     [--ticks N] [--ev3-wait-s S]";

// Exit codes (the systemd unit's restart policy depends on them, see deploy/rf-runtime.service).
/// Start-up failed for a reason that may go away (EV3 or LiDAR not connected yet, host crashed).
const EXIT_RETRY: u8 = 1;
/// Bad command line.
const EXIT_USAGE: u8 = 2;
/// The car stopped because of a driving fault: restart only via resume button or new deploy.
const EXIT_FAULT: u8 = 3;
/// The bundle cannot run as deployed (hash mismatch, invalid manifest): restarting would not help.
const EXIT_CONFIG: u8 = 4;
/// Race mode refused to arm because a radio may be active. Not restarted automatically: the car
/// must never arm by itself the moment someone switches Wi-Fi off.
const EXIT_NOT_ARMED: u8 = 5;

fn exit_code(e: &AppError) -> u8 {
    match e {
        AppError::Bundle(_) => EXIT_CONFIG,
        AppError::RadiosActive(_) => EXIT_NOT_ARMED,
        AppError::Ev3NotConnected(_)
        | AppError::Lidar(..)
        | AppError::Controller(_)
        | AppError::Io(_) => EXIT_RETRY,
    }
}

fn parse(args: &[String]) -> Result<Options, String> {
    let mut opts: Option<Options> = None;
    let mut rest: Vec<(String, String)> = Vec::new();
    let mut it = args.iter();
    while let Some(flag) = it.next() {
        let value = it
            .next()
            .ok_or_else(|| format!("{flag} needs a value"))?
            .clone();
        if flag == "--bundle" {
            opts = Some(Options::new(PathBuf::from(value)));
        } else {
            rest.push((flag.clone(), value));
        }
    }
    let mut o = opts.ok_or("--bundle is required")?;
    for (flag, v) in rest {
        match flag.as_str() {
            "--log-dir" => o.log_dir = PathBuf::from(v),
            "--python" => o.python = v,
            "--pythonpath" => o.pythonpath = Some(PathBuf::from(v)),
            "--ticks" => o.max_ticks = Some(v.parse().map_err(|_| format!("bad --ticks {v}"))?),
            "--ev3-wait-s" => {
                o.ev3_wait =
                    Duration::from_secs_f64(v.parse().map_err(|_| format!("bad --ev3-wait-s {v}"))?)
            }
            _ => return Err(format!("unknown option {flag}")),
        }
    }
    Ok(o)
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let opts = match parse(&args) {
        Ok(o) => o,
        Err(e) => {
            eprintln!("{e}\n{USAGE}");
            return ExitCode::from(EXIT_USAGE);
        }
    };
    match run(&opts, &AtomicBool::new(false)) {
        Ok(out) => {
            println!(
                "bundle {} ({}) ticks={} fault={:?} jitter_p99_us={:.0} log={}",
                out.manifest.name,
                out.manifest.controller.sha256.get(..12).unwrap_or_default(),
                out.report.ticks,
                out.report.fault,
                out.report.jitter.p99_us,
                out.log.display()
            );
            if out.report.fault.is_some() {
                ExitCode::from(EXIT_FAULT)
            } else {
                ExitCode::SUCCESS
            }
        }
        Err(e) => {
            eprintln!("rf-runtime: {e}");
            ExitCode::from(exit_code(&e))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn s(v: &[&str]) -> Vec<String> {
        v.iter().map(|x| x.to_string()).collect()
    }

    #[test]
    fn permanent_errors_are_not_retried() {
        assert_eq!(
            exit_code(&AppError::RadiosActive(vec!["wlan0".into()])),
            EXIT_NOT_ARMED
        );
        let bundle = rf_runtime::manifest::BundleError::HashMismatch("controller.py".into());
        assert_eq!(exit_code(&AppError::Bundle(bundle)), EXIT_CONFIG);
        let ev3 = AppError::Ev3NotConnected(Duration::from_secs(10));
        assert_eq!(exit_code(&ev3), EXIT_RETRY);
        assert_eq!(
            exit_code(&AppError::Lidar("/dev/ttyUSB0".into(), "no scan".into())),
            EXIT_RETRY
        );
    }

    #[test]
    fn parses_options() {
        let o = parse(&s(&[
            "--ticks",
            "10",
            "--bundle",
            "b",
            "--python",
            "py",
            "--ev3-wait-s",
            "0.5",
        ]))
        .expect("ok");
        assert_eq!(o.bundle, PathBuf::from("b"));
        assert_eq!(o.max_ticks, Some(10));
        assert_eq!(o.python, "py");
        assert_eq!(o.ev3_wait, Duration::from_millis(500));
        assert!(parse(&s(&["--ticks", "10"])).is_err());
        assert!(parse(&s(&["--bundle"])).is_err());
        assert!(parse(&s(&["--bundle", "b", "--nope", "1"])).is_err());
        assert!(parse(&s(&["--bundle", "b", "--ticks", "x"])).is_err());
    }
}
