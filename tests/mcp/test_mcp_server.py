"""Spec 0028: MCP tools through an in-memory client (AC1 to AC4)."""

import asyncio
import gzip
import json
from pathlib import Path
from typing import Any

from mcp import Client
from raceforge.api.assistant import Assistant
from raceforge.api.runs import summarize_file
from raceforge.api.service import Engine
from raceforge.mcp.server import READ, WRITE, build_server


def _call(assistant: Assistant, tool: str, args: dict[str, Any], read_only: bool = False) -> Any:
    async def go() -> Any:
        async with Client(build_server(assistant, read_only=read_only)) as c:
            res = await c.call_tool(tool, args)
            assert not res.is_error, res.content
            return res.structured_content

    return asyncio.run(go())


def test_build_check_and_edit_a_draft(tmp_path: Path) -> None:
    """AC1 without the workspace save (needs a logged-in backend; manual AC5 covers it)."""
    a = Assistant(Engine(), folder=tmp_path)
    car = _call(a, "apply_quickstart", {"draft": "car-a"})
    car = car.get("result", car)
    assert car["part_count"] > 10 and car["derived"]
    assert any(r["id"] == "budget" for r in car["rules"])
    n = car["part_count"]
    added = a.add_part("car-a", "3713", attach_to=None)  # a bush, placed freely
    assert added["part_count"] == n + 1
    assert a.remove_part("car-a", added["selected"])["part_count"] == n
    assert "count" in a.get_bom("car-a").splitlines()[0]
    assert a.validate_assembly("car-a")["rules"]


def test_run_simulation_finishes_a_short_corridor(tmp_path: Path) -> None:
    """AC2."""
    a = Assistant(Engine(), folder=tmp_path)
    res = a.run_simulation("centering", length_m=20, max_time_s=120)
    assert res["finished"] and res["time_s"] > 0


def test_read_only_lists_no_write_tools(tmp_path: Path) -> None:
    """AC3."""

    async def names(read_only: bool) -> set[str]:
        server = build_server(Assistant(Engine(), folder=tmp_path), read_only=read_only)
        async with Client(server) as c:
            return {t.name for t in (await c.list_tools()).tools}

    assert asyncio.run(names(True)) == set(READ)
    assert asyncio.run(names(False)) == set(READ) | set(WRITE)


def test_run_log_summary(tmp_path: Path) -> None:
    """AC4: frames, distance, max speed, states from a recorded run log."""
    path = tmp_path / "telemetry.jsonl.gz"
    rows = [{"rx_t": 0.0, "msg": {"type": "hello", "car": "car-1"}}]
    for i in range(11):  # 1 s at 10 Hz, 1 m/s, state changes halfway
        frame = {
            "state": "run" if i < 5 else "stop",
            "meas": {"speed_m_s": 1.0 if i < 10 else 2.0},
            "loop": {"rate_hz": 50.0, "deadline_misses": i // 5},
            "power": {"ev3_battery_v": 8.0 - i * 0.01},
            "faults": ["lidar"] if i == 7 else [],
        }
        rows.append({"rx_t": i * 0.1, "msg": {"type": "telemetry", "frame": frame}})
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)
    s = summarize_file(path)
    assert s["car"] == "car-1" and s["frames"] == 11
    assert abs(s["distance_m"] - 1.0) < 1e-6 and s["speed_max_m_s"] == 2.0
    assert s["faults"] == ["lidar"] and s["deadline_misses"] == 2
    assert set(s["state_seconds"]) == {"run", "stop"}
