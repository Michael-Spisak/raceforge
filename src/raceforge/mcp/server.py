"""`raceforge mcp`: RaceForge for AI assistants via the Model Context Protocol (spec 0028).

Each tool is a method of :class:`raceforge.api.assistant.Assistant`; this module only registers
them, marks the ones that change something (left out with ``--read-only``) and logs every call to
``~/.cache/raceforge/mcp/calls.jsonl``.
"""

import functools
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from raceforge import __version__
from raceforge.api.assistant import Assistant

INSTRUCTIONS = """RaceForge builds, simulates and trains an autonomous LEGO Mindstorms race car.
Units are SI (metres, kilograms, seconds, radians) unless a name says otherwise.
Typical flow: quickstart_options → apply_quickstart(draft) → get_assembly → add_part/move_part →
validate_assembly (rules: wheels and steering must be LEGO, budget ≤ 200 €) → run_simulation →
start_training → job_status. Part paths look like 'chassis/beam-3' (from get_assembly).
Drafts are private to this computer until save_assembly creates a team version."""

READ = [
    "search_parts",
    "get_part",
    "quickstart_options",
    "list_assemblies",
    "get_assembly",
    "validate_assembly",
    "get_bom",
    "list_controllers",
    "job_status",
    "list_jobs",
    "list_tracks",
    "get_track",
    "validate_track",
    "generate_corridor",
    "list_runs",
    "get_run_summary",
    "compare_runs",
]
WRITE = [
    "apply_quickstart",
    "add_part",
    "move_part",
    "rotate_part",
    "remove_part",
    "save_assembly",
    "run_simulation",
    "start_training",
    "stop_job",
]

DESCRIPTIONS = {
    "search_parts": (
        "Find parts in the catalogue (LEGO and the team's 3D-printed parts) by name or id."
    ),
    "get_part": "One catalogue part with mass and connectors.",
    "quickstart_options": (
        "Default parameters and allowed values of the parametric quick-start car."
    ),
    "apply_quickstart": (
        "Create or replace a draft assembly from quick-start parameters (see quickstart_options)."
    ),
    "list_assemblies": "Drafts on this computer and assemblies saved in the team workspace.",
    "get_assembly": (
        "Parts, derived data (mass, centre of gravity, turning radius, gears), rules and budget."
    ),
    "add_part": (
        "Add a catalogue part to a draft: docked onto attach_to (candidate cycles positions) "
        "or at position (m)."
    ),
    "move_part": "Move a part of a draft by delta (m, world axes).",
    "rotate_part": "Turn a part of a draft by quarter turns about a world axis.",
    "remove_part": "Delete a part of a draft.",
    "validate_assembly": (
        "Rule checks (LEGO wheels/steering, EV3 drives, budget, size), overlaps and budget."
    ),
    "get_bom": "Bill of materials as CSV (count, LEGO or not, prices).",
    "save_assembly": "Save a draft as a new version of a team workspace assembly (slug).",
    "list_controllers": "Controller templates and known controller files.",
    "run_simulation": (
        "Race one controller once in the simulator (procedural corridor or a quick track) and"
        " return the result."
    ),
    "start_training": (
        "Start a benchmark, Optuna tuning or PPO job here (run_on='local'), on any team "
        "worker ('team') or a worker id."
    ),
    "job_status": "State, progress and result of a training job.",
    "list_jobs": "Training jobs started here and the team's queued/running jobs.",
    "stop_job": "Cancel a training job (stops after the current race/trial).",
    "list_tracks": "Drawn quick tracks with their validation state.",
    "get_track": (
        "A quick track: centreline points (m), width, loop, laps and its validation report."
    ),
    "validate_track": "Validation report of a quick track (start/finish, width, closed, …).",
    "generate_corridor": "Summary of a procedural training corridor for a seed.",
    "list_runs": "Recorded test drives (run logs) in the team workspace.",
    "get_run_summary": (
        "Duration, distance, speeds, loop rate, battery, faults and time per state of a run log."
    ),
    "compare_runs": "Two run summaries side by side with the differences.",
}


def _logged(fn: Callable[..., Any], log: Path | None) -> Callable[..., Any]:
    @functools.wraps(fn)
    def call(*args: Any, **kwargs: Any) -> Any:
        ok, err = True, ""
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            ok, err = False, f"{type(e).__name__}: {e}"
            raise
        finally:
            if log is not None:
                entry = {"t": round(time.time(), 3), "tool": fn.__name__, "args": kwargs, "ok": ok}
                if err:
                    entry["error"] = err[:500]
                try:
                    log.parent.mkdir(parents=True, exist_ok=True)
                    with log.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(entry, default=str) + "\n")
                except OSError:
                    pass  # logging must never break a tool

    return call


def build_server(
    assistant: Assistant | None = None, read_only: bool = False, log: Path | None = None
) -> MCPServer:
    a = assistant or Assistant()
    server: MCPServer = MCPServer("raceforge", instructions=INSTRUCTIONS, version=__version__)
    for name in READ + ([] if read_only else WRITE):
        write = name in WRITE
        tool = _logged(getattr(a, name), log)
        server.tool(
            name=name,
            description=DESCRIPTIONS[name],
            annotations=ToolAnnotations(
                read_only_hint=not write, destructive_hint=False, open_world_hint=False
            ),
        )(tool)
    return server


def run(http: bool = False, port: int = 8766, read_only: bool = False) -> None:
    a = Assistant()
    server = build_server(a, read_only=read_only, log=a.folder / "calls.jsonl")
    if http:
        server.run("streamable-http", host="127.0.0.1", port=port)
    else:
        server.run("stdio")
