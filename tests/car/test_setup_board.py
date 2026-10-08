"""Board setup script (spec 0005, ADR-0016 v1) run in test mode against fake board file trees."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "car_runtime" / "deploy" / "setup-board.sh"
UNIT = SCRIPT.parent / "rf-runtime.service"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="the board setup script targets Linux boards (bash)",
)


def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), "--test-root", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def pi_os(root: Path) -> Path:
    (root / "boot/firmware").mkdir(parents=True)
    cmdline = root / "boot/firmware/cmdline.txt"
    cmdline.write_text("console=serial0,115200 root=PARTUUID=abcd-02 rootwait\n")
    (root / "etc/systemd/zram-generator.conf.d").mkdir(parents=True)
    (root / "etc/fstab").write_text(
        "proc /proc proc defaults 0 0\n/var/swap none swap sw 0 0\n# /old none swap sw 0 0\n"
    )
    return cmdline


def test_raspberry_pi_os(tmp_path: Path) -> None:
    cmdline = pi_os(tmp_path)
    r = run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert cmdline.read_text() == (
        "console=serial0,115200 root=PARTUUID=abcd-02 rootwait isolcpus=2,3\n"
    )
    assert "REBOOT REQUIRED" in r.stdout
    # Swap: fstab entry commented out (comments untouched), zram generator disabled.
    fstab = (tmp_path / "etc/fstab").read_text().splitlines()
    assert fstab[1].startswith("# raceforge: swap off") and fstab[2] == "# /old none swap sw 0 0"
    assert (tmp_path / "etc/fstab.raceforge.bak").is_file()
    assert (tmp_path / "etc/systemd/zram-generator.conf").read_text() == ""
    # Services: governor oneshot + the runtime unit (unchanged, default cores, no drop-in).
    units = tmp_path / "etc/systemd/system"
    assert "performance" in (units / "raceforge-cpufreq.service").read_text()
    assert (units / "rf-runtime.service").read_text() == UNIT.read_text()
    assert not (units / "rf-runtime.service.d/cores.conf").exists()
    # Commands that need root are printed in test mode.
    printed = [line for line in r.stdout.splitlines() if line.startswith("RUN ")]
    for cmd in ["useradd --system", "swapoff -a", "systemctl enable rf-runtime.service"]:
        assert any(cmd in line for line in printed), cmd
    assert "systemctl restart" not in r.stdout  # not started without --start


def test_second_run_changes_nothing(tmp_path: Path) -> None:
    cmdline = pi_os(tmp_path)
    assert run(tmp_path).returncode == 0
    first = cmdline.read_text()
    fstab = (tmp_path / "etc/fstab").read_text()
    r = run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert cmdline.read_text() == first
    assert (tmp_path / "etc/fstab").read_text() == fstab
    assert "REBOOT REQUIRED" not in r.stdout
    # The backup keeps the original, not the edited file.
    assert "isolcpus" not in (tmp_path / "boot/firmware/cmdline.txt.raceforge.bak").read_text()


def test_armbian_other_cores_and_nohz_full(tmp_path: Path) -> None:
    (tmp_path / "boot").mkdir()
    env = tmp_path / "boot/armbianEnv.txt"
    env.write_text("verbosity=1\nextraargs=cma=256M isolcpus=1\noverlays=uart1\n")
    (tmp_path / "etc/default").mkdir(parents=True)
    (tmp_path / "etc/default/armbian-zram-config").write_text("# zram\nENABLED=true\n")
    (tmp_path / ".nohz_full").touch()  # test hook: pretend the kernel has NO_HZ_FULL
    binary = tmp_path / "rf-runtime-build"
    binary.write_text("#!/bin/sh\n")
    r = run(tmp_path, "--cores", "6,7", "--binary", str(binary), "--start")
    assert r.returncode == 0, r.stderr
    lines = env.read_text().splitlines()
    assert lines == [
        "verbosity=1",
        "extraargs=cma=256M isolcpus=6,7 nohz_full=6,7",
        "overlays=uart1",
    ]
    assert "ENABLED=false" in (tmp_path / "etc/default/armbian-zram-config").read_text()
    dropin = tmp_path / "etc/systemd/system/rf-runtime.service.d/cores.conf"
    assert dropin.read_text() == "[Service]\nCPUAffinity=\nCPUAffinity=6 7\n"
    installed = tmp_path / "opt/raceforge/bin/rf-runtime"
    assert installed.read_text() == "#!/bin/sh\n" and installed.stat().st_mode & 0o111
    assert "RUN systemctl restart rf-runtime.service" in r.stdout


def test_back_to_default_cores_removes_dropin(tmp_path: Path) -> None:
    pi_os(tmp_path)
    assert run(tmp_path, "--cores", "6,7").returncode == 0
    assert run(tmp_path).returncode == 0
    assert not (tmp_path / "etc/systemd/system/rf-runtime.service.d/cores.conf").exists()
    cmdline = (tmp_path / "boot/firmware/cmdline.txt").read_text()
    assert "isolcpus=2,3" in cmdline and "isolcpus=6,7" not in cmdline


def test_invalid_input_is_refused_before_any_change(tmp_path: Path) -> None:
    cmdline = pi_os(tmp_path)
    original = cmdline.read_text()
    for args in (["--cores", "2;rm -rf /"], ["--binary", str(tmp_path / "missing")], ["--nope"]):
        r = run(tmp_path, *args)
        assert r.returncode != 0, args
        assert "RUN " not in r.stdout, args
    assert cmdline.read_text() == original
    assert not (tmp_path / "etc/systemd/system").exists()


def test_unknown_board_layout_warns(tmp_path: Path) -> None:
    r = run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "add 'isolcpus=2,3' to the kernel arguments by hand" in r.stdout
    assert "REBOOT REQUIRED" not in r.stdout


PI_CONFIG = "dtparam=audio=on\n[pi4]\narm_boost=1\n"


def pi_with_config(root: Path) -> Path:
    pi_os(root)
    config = root / "boot/firmware/config.txt"
    config.write_text(PI_CONFIG)
    return config


def test_race_switches_radios_off_for_good(tmp_path: Path) -> None:
    config = pi_with_config(tmp_path)
    r = run(tmp_path, "--race")
    assert r.returncode == 0, r.stderr
    # Overlays in a marked block under [all], after the model-specific [pi4] section.
    text = config.read_text()
    assert text.startswith(PI_CONFIG)
    block = text[len(PI_CONFIG) :].splitlines()
    assert block[1:4] == ["[all]", "dtoverlay=disable-wifi", "dtoverlay=disable-bt"]
    assert (
        block[0].startswith("# >>> raceforge race mode")
        and block[-1] == "# <<< raceforge race mode"
    )
    unit = (tmp_path / "etc/systemd/system/raceforge-radios-off.service").read_text()
    assert "Before=rf-runtime.service" in unit and "echo 1 >" in unit
    printed = [line for line in r.stdout.splitlines() if line.startswith("RUN ")]
    assert "RUN systemctl enable raceforge-radios-off.service" in printed
    assert "RUN systemctl disable --now hciuart.service bluetooth.service" in printed
    assert any(line.startswith("RUN sh -c") and "echo 1 >" in line for line in printed)
    assert "radio-overlays" in r.stdout and "REBOOT REQUIRED" in r.stdout


def test_race_twice_adds_the_block_once(tmp_path: Path) -> None:
    config = pi_with_config(tmp_path)
    assert run(tmp_path, "--race").returncode == 0
    first = config.read_text()
    r = run(tmp_path, "--race")
    assert r.returncode == 0, r.stderr
    assert config.read_text() == first
    assert "REBOOT REQUIRED" not in r.stdout  # nothing changed the second time


def test_no_race_undoes_race_exactly(tmp_path: Path) -> None:
    config = pi_with_config(tmp_path)
    assert run(tmp_path, "--race").returncode == 0
    r = run(tmp_path, "--no-race")
    assert r.returncode == 0, r.stderr
    assert config.read_text() == PI_CONFIG  # byte for byte, no blank lines left behind
    assert not (tmp_path / "etc/systemd/system/raceforge-radios-off.service").exists()
    printed = [line for line in r.stdout.splitlines() if line.startswith("RUN ")]
    assert "RUN systemctl disable raceforge-radios-off.service" in printed
    assert any(line.startswith("RUN sh -c") and "echo 0 >" in line for line in printed)
    assert "radio-overlays" in r.stdout


def test_race_on_armbian_uses_rfkill_only(tmp_path: Path) -> None:
    (tmp_path / "boot").mkdir()
    (tmp_path / "boot/armbianEnv.txt").write_text("verbosity=1\n")
    r = run(tmp_path, "--race")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "etc/systemd/system/raceforge-radios-off.service").is_file()
    assert "radio-overlays" not in r.stdout  # no Raspberry Pi config.txt to edit


def test_without_race_options_radios_are_left_alone(tmp_path: Path) -> None:
    config = pi_with_config(tmp_path)
    r = run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert config.read_text() == PI_CONFIG
    assert not (tmp_path / "etc/systemd/system/raceforge-radios-off.service").exists()
    assert "rfkill" not in r.stdout


def test_race_on_config_without_final_newline(tmp_path: Path) -> None:
    config = pi_with_config(tmp_path)
    config.write_text("dtparam=audio=on")  # no trailing newline
    assert run(tmp_path, "--race").returncode == 0
    lines = config.read_text().splitlines()
    assert lines[0] == "dtparam=audio=on" and lines[2] == "[all]"
    assert run(tmp_path, "--no-race").returncode == 0
    assert config.read_text() == "dtparam=audio=on\n"
