#!/usr/bin/env bash
# Board setup for the RaceForge car runtime (spec 0005 "Board setup", ADR-0016 v1).
#
# Run once as root on Raspberry Pi OS or Armbian (aarch64); safe to run again:
#   sudo ./setup-board.sh [--binary PATH] [--python-pkg SPEC] [--cores 2,3] [--race|--no-race]
#                         [--deploy-key KEYS.pub] [--start]
#
#   --binary PATH      install this rf-runtime binary to /opt/raceforge/bin
#   --python-pkg SPEC  pip-install raceforge into /opt/raceforge/venv (wheel path or package spec)
#   --cores LIST       cores reserved for the runtime (default 2,3; on an RK3588 board such as the
#                      Orange Pi 5 use big cores, e.g. 6,7)
#   --race             switch all radios off for good, so race mode can arm (spec 0005 AC5):
#                      Raspberry Pi config.txt overlays disable-wifi/disable-bt, Bluetooth services
#                      off, rfkill soft-block now and at every boot (raceforge-radios-off.service)
#   --no-race          undo --race (Wi-Fi/Bluetooth usable again, e.g. for test-mode telemetry)
#   --deploy-key FILE  public SSH keys (one per line) allowed to `raceforge deploy --ssh`: user
#                      raceforge-deploy, whose keys and sudo rule run only the bundle installer;
#                      running again with another file replaces the list
#   --start            start the service now (default: enabled for the next boot only)
#   --test-root DIR    test mode: edit files below DIR, print commands instead of running them
#
# What it does:
#   1. system user `raceforge` (group dialout for the LiDAR UART)
#   2. swap off (dphys-swapfile, zram, fstab) - swapping can stall the control loop
#   3. CPU governor `performance` at every boot (oneshot unit raceforge-cpufreq.service)
#   4. kernel arguments isolcpus=<cores> (+ nohz_full if the kernel supports it)
#   5. /opt/raceforge/{bin,bundles,venv}, the rf-runtime binary, the Python package, the bundle
#      installer (`bundle` is its symlink to the current bundle, spec 0005 "Deploy")
#   6. systemd unit rf-runtime.service (+ CPUAffinity drop-in when --cores is not 2,3), USB-stick
#      auto-install (udev rule + raceforge-usb-deploy@.service)
#   7. with --race / --no-race: radios off / back on (without either, radios are left as they are)
#   8. with --deploy-key: the deploy user raceforge-deploy (key only, forced command, one sudo rule)
# Kernel arguments take effect after a reboot; the script says when one is needed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_CORES="2,3"
CORES="$DEFAULT_CORES"
BINARY=""
PYTHON_PKG=""
START=0
ROOT=""   # file-system prefix (test mode)
TEST=0
REBOOT=""  # reasons a reboot is needed (space separated)
RACE=""   # "on" (--race), "off" (--no-race), "" (leave radios as they are)
DEPLOY_KEY=""  # file with the public keys of the deploy user (--deploy-key)
DEPLOY_USER="raceforge-deploy"
DEPLOY_HOME="/var/lib/raceforge-deploy"
INSTALLER="/opt/raceforge/bin/raceforge-install-bundle"
RACE_BEGIN="# >>> raceforge race mode: radios off (setup-board.sh --race)"
RACE_END="# <<< raceforge race mode"

usage() { sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; }
log() { printf '[setup-board] %s\n' "$*"; }
die() { printf '[setup-board] error: %s\n' "$*" >&2; exit 1; }

# Edit a file with sed, portably (BSD/macOS sed has no GNU-style `-i`). Writing back with `cat`
# keeps the file's owner and permissions. Usage: sed_file FILE SED-ARGS...
sed_file() {
    local f="$1"
    shift
    sed "$@" "$f" > "$f.raceforge.tmp"
    cat "$f.raceforge.tmp" > "$f"
    rm -f "$f.raceforge.tmp"
}

# Keep the first original of a file we edit (never overwrite an existing backup).
backup_once() {
    [ -e "$1.raceforge.bak" ] || cp "$1" "$1.raceforge.bak"
}

# Run a command, or print it in test mode.
run() {
    if [ "$TEST" = 1 ]; then
        printf 'RUN %s\n' "$*"
    else
        "$@"
    fi
}

while [ $# -gt 0 ]; do
    case "$1" in
        --binary) BINARY="${2:?--binary needs a path}"; shift 2 ;;
        --python-pkg) PYTHON_PKG="${2:?--python-pkg needs a spec}"; shift 2 ;;
        --cores) CORES="${2:?--cores needs a list}"; shift 2 ;;
        --race) RACE=on; shift ;;
        --no-race) RACE=off; shift ;;
        --deploy-key) DEPLOY_KEY="${2:?--deploy-key needs a file}"; shift 2 ;;
        --start) START=1; shift ;;
        --test-root) ROOT="${2:?--test-root needs a directory}"; TEST=1; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option $1 (see --help)" ;;
    esac
done

# Validate everything before changing anything, so a bad option never leaves a half-set-up board.
[[ "$CORES" =~ ^[0-9]+(,[0-9]+)*$ ]] || die "--cores must look like 2,3"
[ -z "$BINARY" ] || [ -f "$BINARY" ] || die "binary $BINARY not found"
# raceforge (controller host, bundle installer) needs Python >= 3.12. An existing venv decides;
# otherwise the system python3 it will be created from. Test hook: $ROOT/.python3_version.
board_python_version() {
    if [ "$TEST" = 1 ]; then
        cat "$ROOT/.python3_version" 2>/dev/null || echo 3.13
        return
    fi
    local py=python3
    [ -x /opt/raceforge/venv/bin/python ] && py=/opt/raceforge/venv/bin/python
    "$py" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo none
}
PY_VERSION="$(board_python_version)"
[ "$PY_VERSION" != none ] || die "python3 not found (apt install python3 python3-venv)"
IFS=. read -r py_major py_minor _ <<< "$PY_VERSION"
if [ "$py_major" -lt 3 ] || { [ "$py_major" -eq 3 ] && [ "$py_minor" -lt 12 ]; }; then
    die "raceforge needs Python >= 3.12, this board has $PY_VERSION (Debian 12 Bookworm): use Raspberry Pi OS Trixie (Debian 13), or Armbian based on Debian 13 or Ubuntu 24.04"
fi
for f in rf-runtime.service raceforge-install-bundle.sh raceforge-usb-deploy.sh \
    raceforge-usb-deploy@.service 90-raceforge-usb-deploy.rules; do
    [ -f "$SCRIPT_DIR/$f" ] || die "$f missing next to this script"
done
# Deploy keys: plain public keys only (no options: the forced command is added here).
KEY_RE='^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com) [A-Za-z0-9+/]+=*( [^"]*)?$'
DEPLOY_KEYS=""
if [ -n "$DEPLOY_KEY" ]; then
    [ -f "$DEPLOY_KEY" ] || die "deploy key file $DEPLOY_KEY not found"
    while IFS= read -r line || [ -n "$line" ]; do
        [ -n "$line" ] || continue
        [[ "$line" =~ $KEY_RE ]] || die "not a public SSH key in $DEPLOY_KEY: ${line:0:40}"
        DEPLOY_KEYS="$DEPLOY_KEYS$line"$'\n'
    done < "$DEPLOY_KEY"
    [ -n "$DEPLOY_KEYS" ] || die "no public key in $DEPLOY_KEY"
fi
if [ "$TEST" = 0 ]; then
    [ "$(id -u)" = 0 ] || die "run as root (sudo)"
    [ "$(uname -m)" = aarch64 ] || log "warning: expected aarch64, this is $(uname -m)"
    ncpu="$(nproc)"
    for c in ${CORES//,/ }; do
        [ "$c" -lt "$ncpu" ] || die "core $c does not exist (this board has $ncpu)"
    done
fi

# --- 1. user -------------------------------------------------------------------------------
if [ "$TEST" = 1 ] || ! id raceforge >/dev/null 2>&1; then
    log "creating system user raceforge"
    run useradd --system --home-dir /var/lib/raceforge --no-create-home \
        --shell /usr/sbin/nologin --groups dialout raceforge
else
    run usermod -a -G dialout raceforge
fi

# --- 2. swap off ---------------------------------------------------------------------------
log "disabling swap"
run swapoff -a
for unit in dphys-swapfile.service rpi-swap.service; do
    run systemctl disable --now "$unit" || true
done
# zram swap (Raspberry Pi OS trixie: systemd-zram-generator; Armbian: armbian-zram-config).
mkdir -p "$ROOT/etc/systemd"
if [ -d "$ROOT/etc/systemd/zram-generator.conf.d" ] || [ -f "$ROOT/etc/systemd/zram-generator.conf" ] \
    || [ -f "$ROOT/usr/lib/systemd/zram-generator.conf" ]; then
    # An empty config file disables the generator.
    : > "$ROOT/etc/systemd/zram-generator.conf"
    log "zram-generator disabled"
fi
if [ -f "$ROOT/etc/default/armbian-zram-config" ]; then
    sed_file "$ROOT/etc/default/armbian-zram-config" 's/^#\{0,1\}ENABLED=.*/ENABLED=false/'
    log "armbian zram disabled"
fi
if [ -f "$ROOT/etc/fstab" ] && grep -Eq '^[^#].*[[:space:]]swap[[:space:]]' "$ROOT/etc/fstab"; then
    backup_once "$ROOT/etc/fstab"
    sed_file "$ROOT/etc/fstab" -E 's/^([^#].*[[:space:]]swap[[:space:]].*)$/# raceforge: swap off (ADR-0016) # \1/'
    log "swap entries in /etc/fstab commented out"
fi

# --- 3. CPU governor -------------------------------------------------------------------------
log "CPU governor performance at boot"
mkdir -p "$ROOT/etc/systemd/system"
cat > "$ROOT/etc/systemd/system/raceforge-cpufreq.service" <<'EOF'
[Unit]
Description=RaceForge: CPU governor performance (ADR-0016)
Before=rf-runtime.service

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do [ -w "$g" ] && echo performance > "$g"; done; true'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
run systemctl daemon-reload
run systemctl enable raceforge-cpufreq.service

# --- 4. kernel arguments -------------------------------------------------------------------
kernel_has_nohz_full() {
    [ "$TEST" = 1 ] && [ -f "$ROOT/.nohz_full" ] && return 0
    [ "$TEST" = 1 ] && return 1
    if [ -f "/boot/config-$(uname -r)" ]; then
        grep -q '^CONFIG_NO_HZ_FULL=y' "/boot/config-$(uname -r)"
    elif [ -r /proc/config.gz ]; then
        zgrep -q '^CONFIG_NO_HZ_FULL=y' /proc/config.gz
    else
        return 1
    fi
}

ARGS="isolcpus=$CORES"
if kernel_has_nohz_full; then
    ARGS="$ARGS nohz_full=$CORES"
fi

# Replace any earlier isolcpus=/nohz_full= value, then append ours.
set_args_line() {  # $1 = current argument line; prints the new line
    local line="$1" a out=""
    for a in $line; do
        case "$a" in isolcpus=*|nohz_full=*) ;; *) out="$out $a" ;; esac
    done
    printf '%s %s' "${out# }" "$ARGS"
}

if [ -f "$ROOT/boot/firmware/cmdline.txt" ] || [ -f "$ROOT/boot/cmdline.txt" ]; then
    # Raspberry Pi OS: everything on one line.
    f="$ROOT/boot/firmware/cmdline.txt"
    [ -f "$f" ] || f="$ROOT/boot/cmdline.txt"
    old="$(head -n1 "$f")"
    new="$(set_args_line "$old")"
    if [ "$old" != "$new" ]; then
        backup_once "$f"
        printf '%s\n' "$new" > "$f"
        REBOOT="$REBOOT kernel-arguments($ARGS)"
        log "kernel arguments in ${f#"$ROOT"}: $ARGS"
    fi
elif [ -f "$ROOT/boot/armbianEnv.txt" ]; then
    # Armbian: extraargs= line.
    f="$ROOT/boot/armbianEnv.txt"
    old="$(sed -n 's/^extraargs=//p' "$f" | head -n1)"
    new="$(set_args_line "$old")"
    if [ "$old" != "$new" ]; then
        backup_once "$f"
        if grep -q '^extraargs=' "$f"; then
            sed_file "$f" "s|^extraargs=.*|extraargs=$new|"
        else
            printf 'extraargs=%s\n' "$new" >> "$f"
        fi
        REBOOT="$REBOOT kernel-arguments($ARGS)"
        log "kernel arguments in ${f#"$ROOT"}: $ARGS"
    fi
else
    log "warning: no cmdline.txt or armbianEnv.txt found - add '$ARGS' to the kernel arguments by hand"
fi

# --- 5. files ------------------------------------------------------------------------------
log "installing to /opt/raceforge"
# Deployed bundles live in bundles/; `bundle` is the installer's symlink to the current one (an
# older plain bundle/ directory is moved into bundles/ by the next deploy).
mkdir -p "$ROOT/opt/raceforge/bin" "$ROOT/opt/raceforge/bundles"
if [ -n "$BINARY" ]; then
    install -m 0755 "$BINARY" "$ROOT/opt/raceforge/bin/rf-runtime"
fi
install -m 0755 "$SCRIPT_DIR/raceforge-install-bundle.sh" "$ROOT$INSTALLER"
install -m 0755 "$SCRIPT_DIR/raceforge-usb-deploy.sh" "$ROOT/opt/raceforge/bin/raceforge-usb-deploy"
if [ ! -x "$ROOT/opt/raceforge/venv/bin/python" ]; then
    run python3 -m venv /opt/raceforge/venv
fi
if [ -n "$PYTHON_PKG" ]; then
    run /opt/raceforge/venv/bin/pip install --upgrade "$PYTHON_PKG"
fi

# --- 6. service ----------------------------------------------------------------------------
install -m 0644 "$SCRIPT_DIR/rf-runtime.service" "$ROOT/etc/systemd/system/rf-runtime.service"
dropin="$ROOT/etc/systemd/system/rf-runtime.service.d"
if [ "$CORES" != "$DEFAULT_CORES" ]; then
    mkdir -p "$dropin"
    printf '[Service]\nCPUAffinity=\nCPUAffinity=%s\n' "${CORES//,/ }" > "$dropin/cores.conf"
    log "rf-runtime pinned to cores $CORES (drop-in)"
else
    rm -f "$dropin/cores.conf"
fi
# USB-stick deploy: udev starts the installer for FAT/exFAT partitions on USB sticks.
install -m 0644 "$SCRIPT_DIR/raceforge-usb-deploy@.service" "$ROOT/etc/systemd/system/raceforge-usb-deploy@.service"
mkdir -p "$ROOT/etc/udev/rules.d"
install -m 0644 "$SCRIPT_DIR/90-raceforge-usb-deploy.rules" "$ROOT/etc/udev/rules.d/90-raceforge-usb-deploy.rules"
run udevadm control --reload
run systemctl daemon-reload
run systemctl enable rf-runtime.service
if [ "$START" = 1 ]; then
    run systemctl restart rf-runtime.service
fi

# --- 7. radios (race mode) ------------------------------------------------------------------
# Raspberry Pi firmware config: the overlays remove the onboard Wi-Fi/Bluetooth at boot.
pi_config=""
for c in "$ROOT/boot/firmware/config.txt" "$ROOT/boot/config.txt"; do
    if [ -f "$c" ]; then pi_config="$c"; break; fi
done
radios_unit="$ROOT/etc/systemd/system/raceforge-radios-off.service"
# Soft-blocks every rfkill radio by writing sysfs directly (no rfkill tool needed).
# shellcheck disable=SC2016  # literal scripts for `sh -c` and the unit: $r must not expand here
BLOCK_ALL='for r in /sys/class/rfkill/rfkill*/soft; do [ -w "$r" ] && echo 1 > "$r"; done; true'
# shellcheck disable=SC2016  # same as above
UNBLOCK_ALL='for r in /sys/class/rfkill/rfkill*/soft; do [ -w "$r" ] && echo 0 > "$r"; done; true'

if [ "$RACE" = on ]; then
    log "race mode: switching all radios off"
    if [ -n "$pi_config" ] && ! grep -qF "$RACE_BEGIN" "$pi_config"; then
        backup_once "$pi_config"
        # Make sure the file ends with a newline, then append the block (no blank lines, so
        # --no-race restores the file byte for byte). [all] so the overlays apply to every Pi
        # model, whatever filter section comes before.
        if [ -s "$pi_config" ] && [ -n "$(tail -c 1 "$pi_config")" ]; then
            printf '\n' >> "$pi_config"
        fi
        printf '%s\n[all]\ndtoverlay=disable-wifi\ndtoverlay=disable-bt\n%s\n' \
            "$RACE_BEGIN" "$RACE_END" >> "$pi_config"
        REBOOT="$REBOOT radio-overlays"
        log "Wi-Fi/Bluetooth overlays added to ${pi_config#"$ROOT"}"
    fi
    {
        printf '[Unit]\n'
        printf 'Description=RaceForge: all radios rfkill-blocked (race mode, spec 0005 AC5)\n'
        printf 'Before=rf-runtime.service\nAfter=systemd-rfkill.service\n\n'
        printf '[Service]\nType=oneshot\n'
        printf "ExecStart=/bin/sh -c '%s'\n" "$BLOCK_ALL"
        printf 'RemainAfterExit=yes\n\n[Install]\nWantedBy=multi-user.target\n'
    } > "$radios_unit"
    run systemctl daemon-reload
    run systemctl enable raceforge-radios-off.service
    run systemctl disable --now hciuart.service bluetooth.service || true
    # Block now, too, so race mode can arm before the next reboot.
    run sh -c "$BLOCK_ALL"
elif [ "$RACE" = off ]; then
    log "test mode: radios usable again"
    if [ -n "$pi_config" ] && grep -qF "$RACE_BEGIN" "$pi_config"; then
        sed_file "$pi_config" "/^# >>> raceforge race mode/,/^# <<< raceforge race mode/d"
        REBOOT="$REBOOT radio-overlays"
        log "Wi-Fi/Bluetooth overlays removed from ${pi_config#"$ROOT"}"
    fi
    if [ -f "$radios_unit" ]; then
        run systemctl disable raceforge-radios-off.service
        rm -f "$radios_unit"
        run systemctl daemon-reload
    fi
    run systemctl enable bluetooth.service hciuart.service || true
    run sh -c "$UNBLOCK_ALL"
fi

# --- 8. deploy user -------------------------------------------------------------------------
if [ -n "$DEPLOY_KEYS" ]; then
    log "deploy user $DEPLOY_USER: key login, runs only the bundle installer"
    if [ "$TEST" = 1 ] || ! id "$DEPLOY_USER" >/dev/null 2>&1; then
        # A shell is needed to run the forced command; the keys allow nothing else.
        run useradd --system --home-dir "$DEPLOY_HOME" --create-home --shell /bin/sh "$DEPLOY_USER"
    fi
    run usermod -p '*' "$DEPLOY_USER"  # no password, but not "locked" (sshd would refuse keys)
    # Root-owned: the deploy user cannot change its keys or their forced command.
    mkdir -p "$ROOT$DEPLOY_HOME/.ssh"
    chmod 0755 "$ROOT$DEPLOY_HOME/.ssh"
    auth="$ROOT$DEPLOY_HOME/.ssh/authorized_keys"
    : > "$auth"
    while IFS= read -r key; do
        [ -n "$key" ] || continue
        printf 'restrict,command="sudo -n %s --stdin" %s\n' "$INSTALLER" "$key" >> "$auth"
    done <<< "$DEPLOY_KEYS"
    chmod 0644 "$auth"
    mkdir -p "$ROOT/etc/sudoers.d"
    sudoers="$ROOT/etc/sudoers.d/$DEPLOY_USER"
    printf '# raceforge deploy (spec 0005 "Deploy"): the installer, and nothing else.\n%s ALL=(root) NOPASSWD: %s --stdin\n' \
        "$DEPLOY_USER" "$INSTALLER" > "$sudoers.tmp"
    chmod 0440 "$sudoers.tmp"
    run visudo -cf "$sudoers.tmp"
    mv "$sudoers.tmp" "$sudoers"
fi

# --- summary -------------------------------------------------------------------------------
[ -x "$ROOT/opt/raceforge/bin/rf-runtime" ] || log "note: no rf-runtime binary yet (use --binary)"
[ -e "$ROOT/opt/raceforge/bundle" ] || log "note: no bundle deployed yet (raceforge deploy --ssh/--usb)"
if [ -n "$REBOOT" ]; then
    log "done - REBOOT REQUIRED for:$REBOOT"
else
    log "done"
fi
