"""Quick tracks in the engine (spec 0014): save drawn tracks locally and preview them in 2D."""

import json
import math
import re
from pathlib import Path

from pydantic import ValidationError

from raceforge.api.models import QuickTrackInfo, QuickTrackObject, QuickTrackPreview
from raceforge.track.procedural import Corridor, GenerationError
from raceforge.track.quick import QuickTrack, build_quick_track


def tracks_dir() -> Path:
    """Next to the workspace cache, like the deploy bundles (tests use a temp folder)."""
    from raceforge.api.workspace import default_root

    return default_root().parent / "tracks"


def _slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip(".-").lower()
    if not slug:
        raise ValueError("name: use letters or digits")
    return slug


def _r(v: float) -> float:
    return round(float(v), 3)


def preview_of(corridor: Corridor) -> QuickTrackPreview:
    track = corridor.track
    setup = track.race_setups[0]
    return QuickTrackPreview(
        ok=True,
        length_m=_r(corridor.s[-1]),
        centreline=[(_r(x), _r(y)) for x, y in corridor.centreline[::2]],
        walls=[
            [(_r(p.x), _r(p.y)) for p in w.points[::2]] + [(_r(w.points[-1].x), _r(w.points[-1].y))]
            for w in track.walls
        ],
        start_line=(
            (_r(setup.start_line.a.x), _r(setup.start_line.a.y)),
            (_r(setup.start_line.b.x), _r(setup.start_line.b.y)),
        ),
        direction=(_r(setup.direction.x), _r(setup.direction.y)),
        objects=[
            QuickTrackObject(
                kind=o.class_id,
                x=_r(o.pose.position.x),
                y=_r(o.pose.position.y),
                yaw=_r(2 * math.atan2(o.pose.orientation.z, o.pose.orientation.w)),
                size_x=o.size.x,
                size_y=o.size.y,
            )
            for o in track.objects
        ],
    )


def preview(q: QuickTrack) -> QuickTrackPreview:
    try:
        return preview_of(build_quick_track(q))
    except GenerationError as e:
        return QuickTrackPreview(ok=False, error=str(e))


class QuickTracks:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root

    @property
    def dir(self) -> Path:
        return self.root or tracks_dir()

    def path(self, name: str) -> Path:
        return self.dir / f"{_slug(name)}.quicktrack.json"

    def list(self) -> list[QuickTrackInfo]:
        out: list[QuickTrackInfo] = []
        for p in sorted(self.dir.glob("*.quicktrack.json")):
            try:
                q = QuickTrack.model_validate_json(p.read_text(encoding="utf-8"))
            except (OSError, ValidationError) as e:
                out.append(
                    QuickTrackInfo(name=p.name.split(".")[0], loop=False, error=str(e)[:200])
                )
                continue
            pv = preview(q)
            out.append(
                QuickTrackInfo(
                    name=q.name, length_m=pv.length_m or None, loop=q.loop, error=pv.error
                )
            )
        return out

    def get(self, name: str) -> QuickTrack:
        p = self.path(name)
        if not p.is_file():
            raise KeyError(name)
        return QuickTrack.model_validate_json(p.read_text(encoding="utf-8"))

    def save(self, name: str, q: QuickTrack) -> QuickTrackPreview:
        q = q.model_copy(update={"name": name})
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path(name).with_suffix(".tmp")
        tmp.write_text(json.dumps(q.model_dump(mode="json"), indent=1), encoding="utf-8")
        tmp.replace(self.path(name))
        return preview(q)

    def delete(self, name: str) -> None:
        p = self.path(name)
        if not p.is_file():
            raise KeyError(name)
        p.unlink()

    def corridor(self, name: str, laps: int | None = None) -> Corridor:
        """The saved track for the simulator; ValueError if it is missing or invalid."""
        try:
            q = self.get(name)
        except KeyError as e:
            raise ValueError(f"no quick track {name!r}") from e
        if laps is not None and q.loop:
            q = q.model_copy(update={"laps": laps})
        return build_quick_track(q)
