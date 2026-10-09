"""Race Control results (spec 0031): saved as a `run` object (race.json) in the team workspace."""

import json
import tempfile
from datetime import datetime
from pathlib import Path

from raceforge.api.models import RaceResult, RaceSaved
from raceforge.workspace.sync import Workspace


def save_result(ws: Workspace, result: RaceResult) -> RaceSaved:
    started = datetime.fromtimestamp(result.started_at_ms / 1000)
    slug = f"race-{started:%Y%m%d-%H%M%S}"
    winner = next((s["name"] for s in result.standings if s.get("status") == "finished"), None)
    message = f"race, {len(result.cars)} cars, {result.laps} laps" + (
        f", winner {winner}" if winner else ""
    )
    with tempfile.TemporaryDirectory(prefix="raceforge-race-") as tmp:
        path = Path(tmp) / "race.json"
        path.write_text(json.dumps(result.model_dump(mode="json"), indent=1), encoding="utf-8")
        ws.save_files("run", slug, [path], message, entry="race.json")
    return RaceSaved(run=slug)
