"""When may this computer run team jobs? (spec 0020 part C)

``idle`` means no keyboard/mouse input for ``idle_minutes`` **and** on AC power (a computer without
a battery counts as AC). The probes only use the standard library: ``ioreg``/``pmset`` on macOS,
``GetLastInputInfo``/``GetSystemPowerStatus`` on Windows, logind and ``/sys/class/power_supply`` on
Linux. If the idle time cannot be read, ``idle`` mode is unavailable with reason ``idle_unknown``.
"""

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from raceforge.api.models import WorkerPolicy

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class Probe(Protocol):
    def idle_seconds(self) -> float | None: ...
    def on_ac_power(self) -> bool: ...


@dataclass(frozen=True)
class Availability:
    available: bool
    reason: str = ""  # paused | outside_schedule | on_battery | user_active | idle_unknown


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_schedule(policy: WorkerPolicy, now: datetime) -> bool:
    """``now`` in local time. A window whose end is not after its start runs past midnight."""
    minute = now.hour * 60 + now.minute
    today = DAYS[now.weekday()]
    yesterday = DAYS[(now - timedelta(days=1)).weekday()]
    for w in policy.schedule:
        start, end = _minutes(w.start), _minutes(w.end)
        if start < end:
            if today in w.days and start <= minute < end:
                return True
        elif (today in w.days and minute >= start) or (yesterday in w.days and minute < end):
            return True
    return False


def decide(policy: WorkerPolicy, now: datetime, probe: Probe) -> Availability:
    if policy.mode == "paused":
        return Availability(False, "paused")
    if policy.mode == "always":
        return Availability(True)
    if policy.mode == "schedule":
        return (
            Availability(True)
            if in_schedule(policy, now)
            else Availability(False, "outside_schedule")
        )
    if not probe.on_ac_power():
        return Availability(False, "on_battery")
    idle = probe.idle_seconds()
    if idle is None:
        return Availability(False, "idle_unknown")
    if idle < policy.idle_minutes * 60:
        return Availability(False, "user_active")
    return Availability(True)


def _run(*cmd: str) -> str | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None


class SystemProbe:
    """Idle time and power source of this computer."""

    def idle_seconds(self) -> float | None:
        if sys.platform == "darwin":
            out = _run("ioreg", "-c", "IOHIDSystem", "-d", "4")
            m = re.search(r'"HIDIdleTime" = (\d+)', out or "")
            return int(m.group(1)) / 1e9 if m else None
        if sys.platform == "win32":
            import ctypes

            class LastInput(ctypes.Structure):
                _fields_ = (("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint))

            info = LastInput()
            info.cbSize = ctypes.sizeof(LastInput)
            if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
                return None
            ticks = int(ctypes.windll.kernel32.GetTickCount())
            return ((ticks - int(info.dwTime)) & 0xFFFFFFFF) / 1000.0
        return self._logind_idle()

    @staticmethod
    def _logind_idle() -> float | None:
        session = os.environ.get("XDG_SESSION_ID")
        if not session:
            return None
        out = _run(
            "loginctl", "show-session", session, "-p", "IdleHint", "-p", "IdleSinceHintMonotonic"
        )
        if out is None:
            return None
        props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        if props.get("IdleHint") != "yes":
            return 0.0
        since_us = int(props.get("IdleSinceHintMonotonic", "0") or 0)
        if since_us <= 0:
            return None
        return max(0.0, time.monotonic() - since_us / 1e6)

    def on_ac_power(self) -> bool:
        if sys.platform == "darwin":
            out = _run("pmset", "-g", "batt") or ""
            return "Battery Power" not in out
        if sys.platform == "win32":
            import ctypes

            class PowerStatus(ctypes.Structure):
                _fields_ = (
                    ("ACLineStatus", ctypes.c_ubyte),
                    ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte),
                    ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", ctypes.c_ulong),
                    ("BatteryFullLifeTime", ctypes.c_ulong),
                )

            status = PowerStatus()
            if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
                return True
            return int(status.ACLineStatus) != 0  # 1 = AC, 255 = unknown
        return self._linux_ac(Path("/sys/class/power_supply"))

    @staticmethod
    def _linux_ac(root: Path) -> bool:
        mains, battery = False, False
        for dev in root.glob("*"):
            try:
                kind = (dev / "type").read_text().strip()
                if kind == "Mains" and (dev / "online").read_text().strip() == "1":
                    mains = True
                elif kind == "Battery":
                    battery = True
            except OSError:
                continue
        return mains or not battery
