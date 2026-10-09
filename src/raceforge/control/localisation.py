"""Where am I on the track? Map + particle filter (spec 0029). Runs on the car: numpy only.

The map is a distance field: for every 5 cm cell the distance to the nearest wall in cm (capped at
127) plus a "drivable" bit (inside the corridor). RaceForge builds it from the track
(``raceforge.track.localisation``) and puts it into the controller's params YAML as zlib + base64.

The particle filter is classic Monte Carlo localisation: a motion model from the measured speed and
the gyro's yaw rate, a LiDAR likelihood-field sensor model, low-variance resampling, and random
particles when the car seems lost (after being picked up, or when the map no longer matches).
"""

import base64
import math
import zlib
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

F = npt.NDArray[np.float64]
DRIVABLE = 0x80
DIST_MASK = 0x7F
MAX_DIST_M = DIST_MASK / 100.0


def encode_field(cells: npt.NDArray[np.uint8]) -> str:
    return base64.b64encode(zlib.compress(np.ascontiguousarray(cells).tobytes(), 9)).decode()


def decode_field(data: str, width: int, height: int) -> npt.NDArray[np.uint8]:
    raw = zlib.decompress(base64.b64decode(data))
    if len(raw) != width * height:
        raise ValueError(f"map has {len(raw)} cells, expected {width} x {height}")
    return np.frombuffer(raw, dtype=np.uint8).reshape(height, width)


@dataclass(frozen=True)
class DistanceMap:
    """``cells[row, col]``: row = y, col = x; low 7 bits = distance to a wall in cm."""

    cells: npt.NDArray[np.uint8]
    origin_x: float
    origin_y: float
    resolution_m: float

    @property
    def width(self) -> int:
        return int(self.cells.shape[1])

    @property
    def height(self) -> int:
        return int(self.cells.shape[0])

    def _index(
        self, xs: F, ys: F
    ) -> tuple[npt.NDArray[np.intp], npt.NDArray[np.intp], npt.NDArray[np.bool_]]:
        col = np.floor((xs - self.origin_x) / self.resolution_m).astype(np.intp)
        row = np.floor((ys - self.origin_y) / self.resolution_m).astype(np.intp)
        inside = (col >= 0) & (col < self.width) & (row >= 0) & (row < self.height)
        return np.clip(row, 0, self.height - 1), np.clip(col, 0, self.width - 1), inside

    def distance(self, xs: F, ys: F) -> F:
        """Distance to the nearest wall (m); points outside the map count as far away."""
        row, col, inside = self._index(xs, ys)
        d = (self.cells[row, col] & DIST_MASK).astype(np.float64) / 100.0
        return np.where(inside, d, MAX_DIST_M)

    def drivable(self, xs: F, ys: F) -> npt.NDArray[np.bool_]:
        row, col, inside = self._index(xs, ys)
        return inside & ((self.cells[row, col] & DRIVABLE) != 0)

    def drivable_cells(self) -> tuple[F, F]:
        """Centres of all drivable cells (for global re-initialisation)."""
        rows, cols = np.nonzero(self.cells & DRIVABLE)
        return (
            self.origin_x + (cols + 0.5) * self.resolution_m,
            self.origin_y + (rows + 0.5) * self.resolution_m,
        )


@dataclass(frozen=True)
class Estimate:
    x: float
    y: float
    yaw: float
    confidence: float  # 0..1: how well the LiDAR matches the map at the estimate
    spread_m: float  # positional standard deviation of the particles


class ParticleFilter:
    def __init__(
        self,
        world: DistanceMap,
        particles: int = 300,
        beams: int = 30,
        sigma_hit_m: float = 0.06,
        max_range_m: float = 8.0,
        seed: int = 0,
    ) -> None:
        self.map = world
        self.n = particles
        self.beams = beams
        self.sigma = sigma_hit_m
        self.max_range = max_range_m
        self.rng = np.random.default_rng(seed)
        self.x = np.zeros(particles)
        self.y = np.zeros(particles)
        self.yaw = np.zeros(particles)
        self.w = np.full(particles, 1.0 / particles)
        self.confidence = 0.0
        self._low_updates = 0
        self._free = world.drivable_cells()

    # ------------------------------------------------------------------ initialisation
    def init_around(
        self, x: float, y: float, yaw: float, sxy: float = 0.1, syaw: float = 0.1
    ) -> None:
        self.x = x + self.rng.normal(0.0, sxy, self.n)
        self.y = y + self.rng.normal(0.0, sxy, self.n)
        self.yaw = yaw + self.rng.normal(0.0, syaw, self.n)
        self.w = np.full(self.n, 1.0 / self.n)
        self._low_updates = 0

    def _random(self, k: int) -> tuple[F, F, F]:
        fx, fy = self._free
        if len(fx) == 0:
            raise ValueError("the map has no drivable cells")
        idx = self.rng.integers(0, len(fx), k)
        jitter = self.map.resolution_m / 2
        return (
            fx[idx] + self.rng.uniform(-jitter, jitter, k),
            fy[idx] + self.rng.uniform(-jitter, jitter, k),
            self.rng.uniform(-math.pi, math.pi, k),
        )

    def init_global(self) -> None:
        """Lost (picked up, kidnapped): spread the particles over the whole drivable area."""
        self.x, self.y, self.yaw = self._random(self.n)
        self.w = np.full(self.n, 1.0 / self.n)
        self._low_updates = 0

    # ------------------------------------------------------------------ filter
    def predict(self, speed_m_s: float, yaw_rate_rad_s: float, dt_s: float) -> None:
        if dt_s <= 0:
            return
        v = speed_m_s * (1.0 + self.rng.normal(0.0, 0.1, self.n)) + self.rng.normal(
            0.0, 0.02, self.n
        )
        w = yaw_rate_rad_s + self.rng.normal(0.0, 0.05 + 0.1 * abs(yaw_rate_rad_s), self.n)
        mid = self.yaw + 0.5 * w * dt_s
        self.x = self.x + v * dt_s * np.cos(mid)
        self.y = self.y + v * dt_s * np.sin(mid)
        self.yaw = self.yaw + w * dt_s

    def update(self, angles_rad: tuple[float, ...], ranges_m: tuple[float | None, ...]) -> None:
        """Weigh the particles by how well the scan's end points land on walls of the map."""
        valid = [
            (a, r)
            for a, r in zip(angles_rad, ranges_m, strict=True)
            if r is not None and 0.05 < r < self.max_range
        ]
        if len(valid) < 5:
            return  # nothing to match (LiDAR covered or in an open hall): keep predicting
        step = max(1, len(valid) // self.beams)
        a = np.array([v[0] for v in valid[::step]])
        r = np.array([v[1] for v in valid[::step]])
        ang = self.yaw[:, None] + a[None, :]
        ex = self.x[:, None] + r[None, :] * np.cos(ang)
        ey = self.y[:, None] + r[None, :] * np.sin(ang)
        d = self.map.distance(ex.ravel(), ey.ravel()).reshape(ex.shape)
        hit = np.exp(-0.5 * (d / self.sigma) ** 2)
        log_lik = np.log(0.9 * hit + 0.1).sum(axis=1)
        log_lik = np.where(self.map.drivable(self.x, self.y), log_lik, log_lik - 10.0)
        w = self.w * np.exp(log_lik - log_lik.max())
        total = w.sum()
        self.w = w / total if total > 0 and np.isfinite(total) else np.full(self.n, 1.0 / self.n)
        self.confidence = float((self.w * hit.mean(axis=1)).sum())
        if 1.0 / float((self.w**2).sum()) < self.n / 2:
            self._resample()
        self._low_updates = self._low_updates + 1 if self.confidence < 0.25 else 0
        if self._low_updates > 30:  # the map stopped matching for ~1.5 s: start over
            self.init_global()

    def _resample(self) -> None:
        positions = (self.rng.random() + np.arange(self.n)) / self.n
        idx = np.minimum(np.searchsorted(np.cumsum(self.w), positions), self.n - 1)
        self.x, self.y, self.yaw = self.x[idx], self.y[idx], self.yaw[idx]
        if self.confidence < 0.4:  # unsure: a few random particles may find the true place
            k = max(1, self.n // 20)
            rx, ry, ryaw = self._random(k)
            self.x[:k], self.y[:k], self.yaw[:k] = rx, ry, ryaw
        self.w = np.full(self.n, 1.0 / self.n)

    def estimate(self) -> Estimate:
        x = float((self.w * self.x).sum())
        y = float((self.w * self.y).sum())
        yaw = math.atan2(
            float((self.w * np.sin(self.yaw)).sum()), float((self.w * np.cos(self.yaw)).sum())
        )
        spread = math.sqrt(float((self.w * ((self.x - x) ** 2 + (self.y - y) ** 2)).sum()))
        conf = self.confidence * (1.0 if spread < 0.3 else 0.3 / spread)
        return Estimate(x, y, yaw, max(0.0, min(1.0, conf)), spread)
