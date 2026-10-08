#!/bin/sh
# Install a bundle from a USB stick (spec 0005 "Deploy"), started by udev through
# raceforge-usb-deploy@<device>.service for every FAT/exFAT partition on a USB stick.
#
# A stick prepared with `raceforge deploy BUNDLE --usb STICK` holds raceforge/bundle/; the
# installer checks it like an SSH deploy and writes raceforge/result.json back to the stick
# (`raceforge deploy --usb-result STICK` shows it). A stick without that folder is left alone.
# Only FAT/exFAT (no symlinks on the stick) and nosuid/nodev/noexec.
set -eu

dev="${1:?usage: raceforge-usb-deploy /dev/DEVICE}"
mnt=/run/raceforge-usb
mkdir -p "$mnt"
if ! mount -t vfat,exfat -o rw,nosuid,nodev,noexec "$dev" "$mnt"; then
    logger -t raceforge-usb-deploy "$dev: not mountable as FAT/exFAT, ignored"
    exit 0
fi
trap 'sync; umount "$mnt" 2>/dev/null || true' EXIT
if [ -f "$mnt/raceforge/bundle/bundle.json" ]; then
    logger -t raceforge-usb-deploy "$dev: installing raceforge/bundle"
    /opt/raceforge/bin/raceforge-install-bundle --from "$mnt/raceforge/bundle" \
        --result "$mnt/raceforge/result.json" | logger -t raceforge-usb-deploy || true
fi
