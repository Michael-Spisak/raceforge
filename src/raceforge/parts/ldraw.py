"""Minimal LDraw reader: locate files, resolve sub-files, compute bounding boxes (spec 0002).

LDraw coordinates: -Y up, units LDU (1 LDU = 0.4 mm). Bounding boxes are returned in LDU in the
part's own LDraw frame; conversion to the core frame happens in the catalogue.
"""

import os
import shutil
import urllib.request
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

LDRAW_URL = "https://library.ldraw.org/library/updates/complete.zip"
USER_AGENT = "RaceForge/0.0 (+https://github.com/Michael-Spisak/raceforge)"
_SEARCH_DIRS = ("parts", "p", "models", "parts/s", "p/48")

type Vec = tuple[float, float, float]
type Mat = tuple[float, float, float, float, float, float, float, float, float]


def library_dir() -> Path:
    """LDraw root: $RACEFORGE_LDRAW_DIR or ~/.cache/raceforge/ldraw."""
    env = os.environ.get("RACEFORGE_LDRAW_DIR")
    return Path(env) if env else Path.home() / ".cache" / "raceforge" / "ldraw"


def fetch_library(target: Path | None = None, url: str = LDRAW_URL) -> Path:
    """Download and unpack the official LDraw library (~146 MB) into ``target``'s parent."""
    root = target or library_dir()
    root.parent.mkdir(parents=True, exist_ok=True)
    archive = root.parent / "ldraw-complete.zip"
    with urllib.request.urlopen(url) as response, archive.open("wb") as out:
        shutil.copyfileobj(response, out)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(root.parent)  # archive contains the top-level "ldraw/" folder
    return root


@dataclass(frozen=True)
class BBox:
    lo: Vec
    hi: Vec

    @property
    def size(self) -> Vec:
        return (self.hi[0] - self.lo[0], self.hi[1] - self.lo[1], self.hi[2] - self.lo[2])

    @property
    def center(self) -> Vec:
        return tuple((a + b) / 2 for a, b in zip(self.lo, self.hi, strict=True))  # type: ignore[return-value]


class LDrawLibrary:
    """Reads LDraw files from a library root (case-insensitive lookup, backslash paths)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._index: dict[str, Path] = {}
        for sub in _SEARCH_DIRS:
            folder = root / sub
            if folder.is_dir():
                prefix = "s\\" if sub.endswith("/s") else ("48\\" if sub.endswith("/48") else "")
                for f in folder.iterdir():
                    if f.suffix.lower() in (".dat", ".ldr", ".mpd"):
                        self._index.setdefault((prefix + f.name).lower(), f)

    def path(self, name: str) -> Path | None:
        return self._index.get(name.replace("/", "\\").lower())

    def title(self, name: str) -> str | None:
        p = self.path(name)
        if p is None:
            return None
        first = p.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
        return first[0][2:].strip() if first and first[0].startswith("0 ") else None

    def bbox(self, name: str) -> BBox | None:
        points = self._points(name.replace("/", "\\").lower())
        if not points:
            return None
        xs, ys, zs = zip(*points, strict=True)
        return BBox((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))

    @lru_cache(maxsize=4096)  # noqa: B019 - library objects live for the whole process
    def _points(self, key: str) -> tuple[Vec, ...]:
        path = self._index.get(key)
        if path is None:
            return ()
        out: list[Vec] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if not parts:
                continue
            kind = parts[0]
            if kind == "1" and len(parts) >= 15:
                x, y, z, a, b, c, d, e, f, g, h, i = (float(v) for v in parts[2:14])
                sub = " ".join(parts[14:]).replace("/", "\\").lower()
                for px, py, pz in self._points(sub):
                    out.append(
                        (
                            a * px + b * py + c * pz + x,
                            d * px + e * py + f * pz + y,
                            g * px + h * py + i * pz + z,
                        )
                    )
            elif kind in ("3", "4") and len(parts) >= (11 if kind == "3" else 14):
                n = 3 if kind == "3" else 4
                coords = [float(v) for v in parts[2 : 2 + 3 * n]]
                out.extend((coords[k], coords[k + 1], coords[k + 2]) for k in range(0, 3 * n, 3))
        return tuple(out)
