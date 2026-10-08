"""The board's systemd unit matches ADR-0016 v1 and rf-runtime's exit codes."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
UNIT = ROOT / "car_runtime" / "deploy" / "rf-runtime.service"
MAIN_RS = ROOT / "car_runtime" / "rf-runtime" / "src" / "main.rs"


def settings() -> dict[str, str]:
    """Key=value pairs of the unit (continuation lines joined, comments dropped)."""
    text = UNIT.read_text(encoding="utf-8").replace("\\\n", " ")
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "[")):
            continue
        key, _, value = line.partition("=")
        out[key] = " ".join(value.split())
    return out


def exit_codes() -> dict[str, int]:
    src = MAIN_RS.read_text(encoding="utf-8")
    return {name: int(v) for name, v in re.findall(r"const (EXIT_\w+): u8 = (\d+);", src)}


def test_realtime_scheduling_per_adr_0016() -> None:
    s = settings()
    assert s["CPUSchedulingPolicy"] == "fifo"
    assert s["CPUSchedulingPriority"] == "50"
    assert s["CPUSchedulingResetOnFork"] == "no"  # controller host inherits FIFO
    assert s["CPUAffinity"] == "2 3"
    # These would break real-time scheduling or the LiDAR UART.
    assert "RestrictRealtime" not in s
    assert "PrivateDevices" not in s


def test_restart_policy_matches_exit_codes() -> None:
    codes = exit_codes()
    assert set(codes) == {"EXIT_RETRY", "EXIT_USAGE", "EXIT_FAULT", "EXIT_CONFIG"}
    s = settings()
    assert s["Restart"] == "on-failure"
    never = {int(c) for c in s["RestartPreventExitStatus"].split()}
    assert never == {codes["EXIT_USAGE"], codes["EXIT_FAULT"], codes["EXIT_CONFIG"]}
    assert codes["EXIT_RETRY"] not in never


def test_runs_unprivileged_with_its_bundle_and_logs() -> None:
    s = settings()
    assert s["User"] == "raceforge" and "dialout" in s["SupplementaryGroups"]
    assert s["NoNewPrivileges"] == "yes" and s["CapabilityBoundingSet"] == ""
    cmd = s["ExecStart"].split()
    assert cmd[0] == "/opt/raceforge/bin/rf-runtime"
    args = dict(zip(cmd[1::2], cmd[2::2], strict=True))
    assert args["--bundle"] == "/opt/raceforge/bundle"
    assert args["--log-dir"].startswith(s["ReadWritePaths"])
