"""Spec 0020 part C AC7: when a worker may take jobs (fake probes, schedule across midnight)."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from raceforge.api.models import ScheduleWindow, WorkerPolicy
from raceforge.api.worker_policy import SystemProbe, decide


@dataclass
class Fake:
    idle: float | None = 3600.0
    ac: bool = True

    def idle_seconds(self) -> float | None:
        return self.idle

    def on_ac_power(self) -> bool:
        return self.ac


MON_NOON = datetime(2026, 10, 5, 12, 0)  # a Monday


def test_modes() -> None:
    assert decide(WorkerPolicy(mode="always"), MON_NOON, Fake(idle=0, ac=False)).available
    assert decide(WorkerPolicy(mode="paused"), MON_NOON, Fake()).reason == "paused"
    idle = WorkerPolicy(mode="idle", idle_minutes=10)
    assert decide(idle, MON_NOON, Fake(idle=601)).available
    assert decide(idle, MON_NOON, Fake(idle=30)).reason == "user_active"
    assert decide(idle, MON_NOON, Fake(ac=False)).reason == "on_battery"
    assert decide(idle, MON_NOON, Fake(idle=None)).reason == "idle_unknown"
    assert WorkerPolicy().mode == "idle"  # the plan's default: opt-in, idle only


def test_schedule_window_across_midnight() -> None:
    night = WorkerPolicy(
        mode="schedule", schedule=[ScheduleWindow(days=["fri"], start="22:00", end="07:00")]
    )
    fri_late = datetime(2026, 10, 9, 23, 30)
    sat_early = datetime(2026, 10, 10, 6, 59)
    sat_late = datetime(2026, 10, 10, 23, 0)
    thu_late = datetime(2026, 10, 8, 23, 0)
    assert decide(night, fri_late, Fake()).available
    assert decide(night, sat_early, Fake()).available
    assert decide(night, sat_late, Fake()).reason == "outside_schedule"
    assert decide(night, thu_late, Fake()).reason == "outside_schedule"
    day = WorkerPolicy(
        mode="schedule", schedule=[ScheduleWindow(days=["mon", "tue"], start="08:00", end="13:00")]
    )
    assert decide(day, MON_NOON, Fake()).available
    assert not decide(day, datetime(2026, 10, 5, 13, 0), Fake()).available


def test_linux_power_supply_and_system_probe(tmp_path: Path) -> None:
    def dev(name: str, kind: str, online: str | None = None) -> None:
        (tmp_path / name).mkdir()
        (tmp_path / name / "type").write_text(kind + "\n")
        if online is not None:
            (tmp_path / name / "online").write_text(online + "\n")

    assert SystemProbe._linux_ac(tmp_path)  # no battery: a desktop counts as AC
    dev("BAT0", "Battery")
    dev("AC", "Mains", "0")
    assert not SystemProbe._linux_ac(tmp_path)
    (tmp_path / "AC" / "online").write_text("1\n")
    assert SystemProbe._linux_ac(tmp_path)
    probe = SystemProbe()  # must never raise on the machine running the tests
    idle = probe.idle_seconds()
    assert idle is None or idle >= 0
    assert isinstance(probe.on_ac_power(), bool)
