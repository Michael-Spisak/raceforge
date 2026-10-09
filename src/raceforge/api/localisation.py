"""Params for the `localised` controller from a quick track: map + racing line (spec 0029)."""

from pathlib import Path
from typing import Any

import yaml

from raceforge.api.tracks import QuickTracks
from raceforge.control.localisation import encode_field
from raceforge.track.localisation import build_map
from raceforge.track.racing_line import LineConfig, racing_line


def localisation_params(
    track: str, quick: QuickTracks | None = None, cfg: LineConfig | None = None
) -> dict[str, Any]:
    corridor = (quick or QuickTracks()).corridor(track)
    world = build_map(corridor)
    line = racing_line(corridor, cfg)
    start = (0.0, 0.0, 0.0)
    if corridor.track.race_setups and corridor.track.race_setups[0].start_grid:
        g = corridor.track.race_setups[0].start_grid[0]
        start = (g.x, g.y, g.theta)
    return {
        "track": track,
        "map_b64": encode_field(world.cells),
        "map_origin": [round(world.origin_x, 4), round(world.origin_y, 4)],
        "map_resolution_m": world.resolution_m,
        "map_width": world.width,
        "map_height": world.height,
        "line": [
            [round(float(x), 3), round(float(y), 3), round(float(v), 3)]
            for (x, y), v in zip(line.xy, line.speed, strict=True)
        ],
        "loop": line.loop,
        "start": [round(v, 4) for v in start],
    }


def write_params(track: str, out: Path, quick: QuickTracks | None = None) -> Path:
    data = localisation_params(track, quick)
    header = (
        f"# localised controller params for track {track!r} (spec 0029): map + racing line.\n"
        "# Regenerate with `raceforge localise TRACK` after changing the track.\n"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(header + yaml.safe_dump(data, sort_keys=False, width=200), encoding="utf-8")
    return out
