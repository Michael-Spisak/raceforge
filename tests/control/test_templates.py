"""Spec 0004 AC2 (templates drive corridors), AC7 (CLI), AC8 (guides)."""

import re
from pathlib import Path

import pytest

from raceforge.cli import main
from raceforge.control import Command, Observation
from raceforge.control.controller import load_controller
from raceforge.sim.runner import SimOptions, run_once

TEMPLATES = Path(__file__).parents[2] / "controllers" / "templates"
DOCS = Path(__file__).parents[2] / "docs" / "user"


@pytest.mark.parametrize("name", ["centering", "wall_follow", "state_machine"])
def test_template_loads_with_params_and_reacts(name: str) -> None:
    ctrl = load_controller(TEMPLATES / f"{name}.py")
    assert (TEMPLATES / f"{name}.yaml").is_file()
    cmd = ctrl.step(
        Observation(
            t_s=0.0,
            dt_s=0.02,
            ultrasonic_m={"front": None, "left": 0.8, "right": 0.8},
            heading_rad=0.0,
        )
    )
    assert isinstance(cmd, Command)
    assert abs(cmd.steering_rad) < 0.6


def _run(name: str, seed: int, laps: int, length: float, loop: bool = True) -> bool:
    opts = SimOptions(
        controller=TEMPLATES / f"{name}.py",
        track=f"seed:{seed}",
        laps=laps,
        length_m=length,
        loop=loop,
        seed=seed,
        max_time_s=laps * length / 0.15 + 60,
    )
    return run_once(opts, lambda _: None)


@pytest.mark.parametrize("name", ["centering", "wall_follow", "state_machine"])
def test_template_quick_lap(name: str) -> None:
    assert _run(name, seed=1, laps=1, length=30)


@pytest.mark.slow
@pytest.mark.parametrize(
    "name,required", [("centering", 20), ("state_machine", 20), ("wall_follow", 18)]
)
def test_template_three_laps_on_20_corridors(name: str, required: int) -> None:
    clean = sum(_run(name, seed=s, laps=3, length=40) for s in range(20))
    assert clean >= required, f"{name}: {clean}/20 clean"


def test_cli_sim_prints_lap_times_and_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "sim",
            "--controller",
            str(TEMPLATES / "centering.py"),
            "--laps",
            "1",
            "--length",
            "25",
            "--record",
            str(tmp_path),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "FINISHED" in out and "laps 1 (" in out
    assert (tmp_path / "run.mcap").stat().st_size > 0 and (tmp_path / "runlog.json").is_file()


@pytest.mark.parametrize("lang", ["de", "en"])
def test_guide_snippets_run(lang: str) -> None:
    text = (DOCS / f"controller-guide.{lang}.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```python\n(.*?)```", text, flags=re.S)
    assert len(blocks) >= 3
    for block in blocks:
        exec(compile(block, f"controller-guide.{lang}.md", "exec"), {"__name__": "guide"})
