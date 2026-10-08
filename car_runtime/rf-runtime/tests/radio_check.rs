//! Spec 0005 AC5: race mode refuses to arm while a (mocked) radio may be active. Each test builds a
//! fake sysfs tree like the one on a Raspberry Pi 5 with the radios in a given state.

#![allow(clippy::expect_used)] // test helpers: a failed setup should abort the test

use rf_runtime::radio::check;
use std::fs;
use std::os::unix::fs::symlink;
use std::path::{Path, PathBuf};

fn root(name: &str) -> PathBuf {
    let r = std::env::temp_dir().join(format!("rf-radio-{}-{name}", std::process::id()));
    let _ = fs::remove_dir_all(&r);
    fs::create_dir_all(r.join("sys/class/net")).expect("tree");
    r
}

fn write(path: PathBuf, content: &str) {
    fs::create_dir_all(path.parent().expect("parent")).expect("dir");
    fs::write(path, content).expect("write");
}

/// Network interface; `usb` puts its device under a USB controller (a dongle).
fn netdev(r: &Path, ifname: &str, wireless: bool, up: bool, usb: bool) {
    let bus = if usb {
        "usb1/1-1/1-1:1.0"
    } else {
        "mmc1/mmc1:0001/mmc1:0001:1"
    };
    let dev = r.join("sys/devices/platform/soc").join(bus);
    fs::create_dir_all(&dev).expect("device");
    let d = r.join("sys/class/net").join(ifname);
    write(d.join("flags"), if up { "0x1003\n" } else { "0x1002\n" });
    if wireless {
        fs::create_dir_all(d.join("wireless")).expect("wireless");
    }
    symlink(&dev, d.join("device")).expect("symlink");
}

fn rfkill(r: &Path, idx: u32, kind: &str, soft: bool, hard: bool) {
    let d = r.join(format!("sys/class/rfkill/rfkill{idx}"));
    write(d.join("type"), &format!("{kind}\n"));
    write(d.join("name"), &format!("phy{idx}\n"));
    write(d.join("soft"), if soft { "1\n" } else { "0\n" });
    write(d.join("hard"), if hard { "1\n" } else { "0\n" });
}

/// A Pi 5 prepared for racing: onboard Wi-Fi down and blocked, Bluetooth blocked, wired eth0 up,
/// EV3 link over USB Ethernet (class 02 = communications) up.
fn race_ready(r: &Path) {
    netdev(r, "eth0", false, true, false);
    netdev(r, "usb0", false, true, true); // EV3 USB gadget: wired, allowed
    netdev(r, "wlan0", true, false, false);
    rfkill(r, 0, "wlan", true, false);
    rfkill(r, 1, "bluetooth", true, false);
    let hci = r.join("sys/class/bluetooth/hci0/rfkill1");
    write(hci.join("soft"), "1\n");
    write(hci.join("hard"), "0\n");
    let ev3 = r.join("sys/bus/usb/devices/1-1");
    write(ev3.join("idVendor"), "0694\n");
    write(ev3.join("idProduct"), "0005\n");
    write(
        r.join("sys/bus/usb/devices/1-1:1.0/bInterfaceClass"),
        "02\n",
    );
}

#[test]
fn race_ready_board_passes() {
    let r = root("ready");
    race_ready(&r);
    assert_eq!(check(&r, &[]), Vec::<String>::new());
}

#[test]
fn wifi_interface_up_is_refused() {
    let r = root("wifi-up");
    race_ready(&r);
    write(r.join("sys/class/net/wlan0/flags"), "0x1003\n");
    let v = check(&r, &[]);
    assert_eq!(v, vec!["Wi-Fi interface wlan0 is up".to_string()]);
}

#[test]
fn unblocked_radios_are_refused() {
    let r = root("unblocked");
    race_ready(&r);
    rfkill(&r, 0, "wlan", false, false); // Wi-Fi soft-unblocked (interface still down)
    write(r.join("sys/class/bluetooth/hci0/rfkill1/soft"), "0\n");
    rfkill(&r, 1, "bluetooth", false, false);
    let v = check(&r, &[]);
    assert!(
        v.iter()
            .any(|m| m.contains("rfkill0") && m.contains("wlan")),
        "{v:?}"
    );
    assert!(
        v.iter()
            .any(|m| m.contains("rfkill1") && m.contains("bluetooth")),
        "{v:?}"
    );
    assert!(
        v.iter()
            .any(|m| m == "Bluetooth adapter hci0 is not blocked"),
        "{v:?}"
    );
}

#[test]
fn hard_blocked_counts_as_blocked() {
    let r = root("hard");
    race_ready(&r);
    rfkill(&r, 0, "wlan", false, true);
    assert!(check(&r, &[]).is_empty());
}

#[test]
fn usb_wifi_dongle_is_refused_even_when_down() {
    let r = root("dongle");
    race_ready(&r);
    netdev(&r, "wlan1", true, false, true);
    assert_eq!(
        check(&r, &[]),
        vec!["Wi-Fi interface wlan1 is a USB radio (unplug it)".to_string()]
    );
}

#[test]
fn usb_bluetooth_dongle_is_refused_by_class() {
    let r = root("bt-dongle");
    race_ready(&r);
    write(
        r.join("sys/bus/usb/devices/1-2:1.0/bInterfaceClass"),
        "e0\n",
    );
    assert_eq!(
        check(&r, &[]),
        vec!["USB wireless controller interface 1-2:1.0".to_string()]
    );
}

#[test]
fn denied_usb_id_is_refused() {
    let r = root("deny");
    race_ready(&r);
    let d = r.join("sys/bus/usb/devices/1-3");
    write(d.join("idVendor"), "0BDA\n");
    write(d.join("idProduct"), "8179\n");
    write(d.join("product"), "802.11n NIC\n");
    write(
        r.join("sys/bus/usb/devices/1-3:1.0/bInterfaceClass"),
        "ff\n",
    ); // vendor class
    assert!(
        check(&r, &[]).is_empty(),
        "not on the deny list -> not detected by class"
    );
    assert_eq!(
        check(&r, &["0bda:8179".to_string()]),
        vec!["USB radio 0bda:8179 (802.11n NIC) at 1-3".to_string()]
    );
}

#[test]
fn bluetooth_adapter_without_rfkill_is_refused() {
    let r = root("hci-no-rfkill");
    race_ready(&r);
    fs::create_dir_all(r.join("sys/class/bluetooth/hci1")).expect("hci1");
    assert_eq!(
        check(&r, &[]),
        vec!["Bluetooth adapter hci1 is not blocked".to_string()]
    );
}

#[test]
fn missing_sysfs_fails_closed() {
    let r = std::env::temp_dir().join(format!("rf-radio-{}-empty", std::process::id()));
    let _ = fs::remove_dir_all(&r);
    fs::create_dir_all(&r).expect("dir");
    let v = check(&r, &[]);
    assert_eq!(v.len(), 1);
    assert!(
        v[0].starts_with("cannot inspect network interfaces"),
        "{v:?}"
    );
}
