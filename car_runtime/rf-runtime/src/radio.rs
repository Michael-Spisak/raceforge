//! Race-mode radio check (spec 0005 safety, AC5): the car refuses to arm while any radio could be
//! active. It reads Linux sysfs only (no tools, no privileges) and fails closed: if the network
//! interfaces cannot be inspected at all, that is a violation too.
//!
//! Violations:
//! 1. an rfkill radio (Wi-Fi, Bluetooth, WWAN, ...) that is neither soft- nor hard-blocked;
//! 2. a Wi-Fi interface that is administratively up;
//! 3. a Wi-Fi interface on USB (a dongle is plugged in, even if it is down);
//! 4. a Bluetooth adapter without a blocked rfkill switch;
//! 5. a USB interface of class 0xE0 "Wireless Controller" (Bluetooth dongles);
//! 6. a USB device whose `vendor:product` id is on the bundle's deny list (vendor-class dongles).
//!
//! The wired USB link to the EV3 (CDC-ECM/RNDIS, class 0x02/0x0A) is not a radio and passes.

use std::fs;
use std::path::{Path, PathBuf};

/// rfkill types that are radios (everything rfkill knows except "all").
const RADIO_TYPES: &[&str] = &[
    "wlan",
    "bluetooth",
    "uwb",
    "wimax",
    "wwan",
    "gps",
    "fm",
    "nfc",
];
/// USB interface class "Wireless Controller" (Bluetooth, UWB, ...).
const USB_CLASS_WIRELESS: &str = "e0";
/// Net device flag IFF_UP.
const IFF_UP: u32 = 0x1;

fn read(p: &Path) -> Option<String> {
    fs::read_to_string(p).ok().map(|s| s.trim().to_string())
}

fn entries(dir: &Path) -> Vec<PathBuf> {
    let mut v: Vec<PathBuf> = match fs::read_dir(dir) {
        Ok(rd) => rd.filter_map(|e| e.ok().map(|e| e.path())).collect(),
        Err(_) => Vec::new(),
    };
    v.sort();
    v
}

fn name(p: &Path) -> String {
    p.file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default()
}

fn blocked(rfkill: &Path) -> bool {
    read(&rfkill.join("soft")).as_deref() == Some("1")
        || read(&rfkill.join("hard")).as_deref() == Some("1")
}

/// Check the board below `root` (normally `/`). Returns one human-readable line per violation;
/// empty means the car may arm in race mode. `deny_usb` holds lowercase `vvvv:pppp` ids.
pub fn check(root: &Path, deny_usb: &[String]) -> Vec<String> {
    let sys = root.join("sys");
    let mut out = Vec::new();

    // 1. rfkill switches.
    for rf in entries(&sys.join("class/rfkill")) {
        let kind = read(&rf.join("type")).unwrap_or_default();
        if RADIO_TYPES.contains(&kind.as_str()) && !blocked(&rf) {
            let label = read(&rf.join("name")).unwrap_or_default();
            out.push(format!(
                "radio {} ({kind}, {label}) is not blocked (rfkill)",
                name(&rf)
            ));
        }
    }

    // 2./3. Wi-Fi network interfaces. Fail closed if interfaces cannot be listed.
    let net = sys.join("class/net");
    if !net.is_dir() {
        out.push(format!(
            "cannot inspect network interfaces ({} missing)",
            net.display()
        ));
    }
    for dev in entries(&net) {
        if !(dev.join("wireless").exists() || dev.join("phy80211").exists()) {
            continue;
        }
        let flags = read(&dev.join("flags"))
            .and_then(|f| u32::from_str_radix(f.trim_start_matches("0x"), 16).ok())
            .unwrap_or(IFF_UP); // unreadable flags: assume up
        if flags & IFF_UP != 0 {
            out.push(format!("Wi-Fi interface {} is up", name(&dev)));
        }
        let on_usb = fs::canonicalize(dev.join("device"))
            .map(|p| p.to_string_lossy().contains("/usb"))
            .unwrap_or(false);
        if on_usb {
            out.push(format!(
                "Wi-Fi interface {} is a USB radio (unplug it)",
                name(&dev)
            ));
        }
    }

    // 4. Bluetooth adapters must have a blocked rfkill switch.
    for hci in entries(&sys.join("class/bluetooth")) {
        if !name(&hci).starts_with("hci") {
            continue;
        }
        let switches: Vec<PathBuf> = entries(&hci)
            .into_iter()
            .filter(|p| name(p).starts_with("rfkill"))
            .collect();
        if switches.is_empty() || !switches.iter().all(|s| blocked(s)) {
            out.push(format!("Bluetooth adapter {} is not blocked", name(&hci)));
        }
    }

    // 5./6. USB devices and interfaces.
    for usb in entries(&sys.join("bus/usb/devices")) {
        let n = name(&usb);
        if n.contains(':') {
            if read(&usb.join("bInterfaceClass")).as_deref() == Some(USB_CLASS_WIRELESS) {
                out.push(format!("USB wireless controller interface {n}"));
            }
            continue;
        }
        if let (Some(v), Some(p)) = (read(&usb.join("idVendor")), read(&usb.join("idProduct"))) {
            let id = format!("{}:{}", v.to_lowercase(), p.to_lowercase());
            if deny_usb.iter().any(|d| d.eq_ignore_ascii_case(&id)) {
                let product = read(&usb.join("product")).unwrap_or_default();
                out.push(format!("USB radio {id} ({product}) at {n}"));
            }
        }
    }
    out
}
