"""Quick-track walls taken from a scan (spec 0032).

The car-height occupancy grid of a TrackScout scan (spec 0024) is embedded in the quick track, so
the track builds anywhere (engine, workers) without the scan file. Walls are found by rays
perpendicular to the drawn centreline; the centreline is then moved to the middle of the real
corridor and the rays are cast again from there.
"""

import base64
import math
import warnings
import zlib

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field, model_validator

from raceforge.track.procedural import GenerationError, normals_of

Arr = npt.NDArray[np.float64]
MAX_CELLS = 16_000_000
MAX_HALF_M = 3.0  # rays longer than this count as "open" (no wall seen)
SHIFT_WINDOW_M = 2.0  # centreline shift is smoothed over this length
WALL_WINDOW = 3  # median filter (samples) on the wall distances
MIN_FREE_M = 0.3  # narrower spots cannot be driven by any car; the validator warns earlier


class ScanGrid(BaseModel):
    """Occupancy grid in world metres: bit ``row * width + col`` set = blocked (row = y)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: tuple[float, float]
    resolution: float = Field(ge=0.01, le=0.5)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    bits_b64: str  # zlib(numpy.packbits(row-major cells))
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _check(self) -> "ScanGrid":
        if self.width * self.height > MAX_CELLS:
            raise ValueError("scan grid too large")
        self.cells()  # raises on a broken payload
        return self

    def cells(self) -> npt.NDArray[np.bool_]:
        try:
            raw = zlib.decompress(base64.b64decode(self.bits_b64, validate=True))
        except (ValueError, zlib.error) as exc:
            raise ValueError("scan grid: broken bits_b64") from exc
        n = self.width * self.height
        bits = np.unpackbits(np.frombuffer(raw, dtype=np.uint8))
        if len(bits) < n:
            raise ValueError("scan grid: bits_b64 shorter than width * height")
        return bits[:n].astype(bool).reshape(self.height, self.width)


def encode_grid(
    occupied: npt.NDArray[np.bool_],
    origin: tuple[float, float],
    resolution: float,
    source_sha256: str | None = None,
) -> ScanGrid:
    h, w = occupied.shape
    bits = base64.b64encode(zlib.compress(np.packbits(occupied.reshape(-1)).tobytes(), 9))
    return ScanGrid(
        origin=origin,
        resolution=resolution,
        width=int(w),
        height=int(h),
        bits_b64=bits.decode(),
        source_sha256=source_sha256,
    )


def _blocked(cells: npt.NDArray[np.bool_], g: ScanGrid, xy: Arr) -> npt.NDArray[np.bool_]:
    """Blocked flags for points ``(..., 2)``; outside the grid counts as free."""
    c = np.floor((xy[..., 0] - g.origin[0]) / g.resolution).astype(np.int64)
    r = np.floor((xy[..., 1] - g.origin[1]) / g.resolution).astype(np.int64)
    inside = (c >= 0) & (c < g.width) & (r >= 0) & (r < g.height)
    out = np.zeros(c.shape, dtype=bool)
    out[inside] = cells[r[inside], c[inside]]
    return out


def wall_distances(g: ScanGrid, centre: Arr, normals: Arr) -> tuple[Arr, Arr]:
    """Distance to the first blocked cell to the left (+normal) and right of every sample; NaN when
    the side is open within :data:`MAX_HALF_M` or the sample itself is blocked."""
    cells = g.cells()
    d = np.arange(g.resolution / 2, MAX_HALF_M, g.resolution / 2)
    centre_blocked = _blocked(cells, g, centre)
    out: list[Arr] = []
    for sign in (1.0, -1.0):
        pts = centre[:, None, :] + sign * normals[:, None, :] * d[None, :, None]
        hit = _blocked(cells, g, pts)
        first = np.argmax(hit, axis=1)
        dist = np.where(hit.any(axis=1), d[first], np.nan)
        dist[centre_blocked] = np.nan
        out.append(dist)
    return out[0], out[1]


def _median3(v: Arr, loop: bool) -> Arr:
    pad = WALL_WINDOW // 2
    ext = np.concatenate([v[-pad - 1 : -1], v, v[1 : pad + 1]]) if loop else np.pad(v, pad, "edge")
    win = np.lib.stride_tricks.sliding_window_view(ext, WALL_WINDOW)
    with warnings.catch_warnings():  # all-NaN windows stay NaN
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(win, axis=1)


def _fill(v: Arr, s: Arr, fallback: float) -> Arr:
    ok = np.isfinite(v)
    if not ok.any():
        return np.full_like(v, fallback)
    return np.interp(s, s[ok], v[ok])


def _smooth(v: Arr, window: int, loop: bool) -> Arr:
    if window < 2:
        return v
    k = np.ones(window) / window
    ext = np.concatenate([v[-window:-1], v, v[1:window]]) if loop else np.pad(v, window - 1, "edge")
    return np.convolve(ext, k, mode="same")[window - 1 : window - 1 + len(v)]


def _cross(a: Arr, b: Arr) -> Arr:
    return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]


def unfold(wall: Arr, loop: bool, window: int = 40) -> Arr:
    """Remove small loops of an offset polyline: when segment ``i`` crosses segment ``i + k``
    (``k <= window``), points ``i + 1 … i + k`` move onto the crossing (the length stays)."""
    w = wall.copy()
    n = len(w) - 1  # segments
    for _ in range(4 * n):
        p, r = w[:-1], np.diff(w, axis=0)
        found = False
        for k in range(2, min(window, n - 2) + 1):
            i = np.arange(n) if loop else np.arange(n - k)
            j = (i + k) % n  # k <= n - 2: never a neighbour, also across the closing point
            rxs = _cross(r[i], r[j])
            qp = p[j] - p[i]
            with np.errstate(divide="ignore", invalid="ignore"):
                t = _cross(qp, r[j]) / rxs
                u = _cross(qp, r[i]) / rxs
            hit = (np.abs(rxs) > 1e-12) & (t > 1e-9) & (t < 1 - 1e-9) & (u > 1e-9) & (u < 1 - 1e-9)
            if hit.any():
                a = int(i[hit][0])
                x = p[a] + r[a] * t[hit][0]
                for m in range(1, k + 1):
                    w[(a + m) % n if loop else a + m] = x
                if loop:
                    w[-1] = w[0]
                found = True
                break
        if not found:
            break
    return w


def fit_to_scan(
    g: ScanGrid,
    centre: Arr,
    normals: Arr,
    s: Arr,
    sample_m: float,
    fallback_half_m: float,
    loop: bool,
) -> tuple[Arr, Arr, Arr, Arr, Arr, Arr]:
    """Centred samples, their normals, symmetric widths, extra depth per side (left, right) and
    the two wall polylines (left, right) with folds removed.

    Gaps in the scan (open doors, glass, unscanned parts) continue the neighbouring wall; a side
    without any wall gets one at ``fallback_half_m``. Inner walls of bends are kept inside the bend
    radius so the offset polylines do not fold. Raises :class:`GenerationError` when the centred
    line runs through a scanned obstacle or the free width drops below :data:`MIN_FREE_M`.
    """
    with np.errstate(all="ignore"):
        left, right = wall_distances(g, centre, normals)
        shift = (_fill(left, s, fallback_half_m) - _fill(right, s, fallback_half_m)) / 2
        shift = _smooth(shift, max(1, round(SHIFT_WINDOW_M / sample_m)), loop)
        centre2 = centre + normals * shift[:, None]
        if loop:
            centre2[-1] = centre2[0]
        normals2 = normals_of(centre2)
        if loop:
            normals2[-1] = normals2[0]
        left, right = wall_distances(g, centre2, normals2)
        inside = _blocked(g.cells(), g, centre2)
        run = np.convolve(inside.astype(float), np.ones(2), mode="valid") >= 2
        if run.any():
            x, y = centre2[int(np.argmax(run))]
            raise GenerationError(
                f"the line runs through a scanned obstacle near ({x:.1f}, {y:.1f}) m"
            )
        left = _fill(_median3(left, loop), s, fallback_half_m)
        right = _fill(_median3(right, loop), s, fallback_half_m)
    # Signed curvature (+ = turning left): the inner wall must stay inside the radius.
    heading = np.unwrap(np.arctan2(normals2[:, 1], normals2[:, 0]))
    k = np.gradient(heading) / sample_m
    with np.errstate(divide="ignore"):
        radius = np.where(np.abs(k) > 1e-6, 1 / np.abs(k), math.inf)
    left = np.where(k > 0, np.minimum(left, 0.9 * radius), left)
    right = np.where(k < 0, np.minimum(right, 0.9 * radius), right)
    narrow = left + right < MIN_FREE_M
    if narrow.any():
        x, y = centre2[int(np.argmax(narrow))]
        raise GenerationError(
            f"scanned corridor narrower than {MIN_FREE_M} m near ({x:.1f}, {y:.1f}) m"
        )
    widths = 2 * np.minimum(left, right)
    depth = np.vstack([left - widths / 2, right - widths / 2])
    wall_l = unfold(centre2 + normals2 * left[:, None], loop)
    wall_r = unfold(centre2 - normals2 * right[:, None], loop)
    return centre2, normals2, widths, depth, wall_l, wall_r
