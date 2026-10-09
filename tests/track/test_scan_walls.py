"""Spec 0032: quick-track walls from a scan grid."""

import numpy as np
import pytest

from raceforge.track.procedural import (  # pyright: ignore[reportPrivateUsage]
    GenerationError,
    _segments_intersect,
)
from raceforge.track.quick import QuickTrack, build_quick_track
from raceforge.track.scan_walls import ScanGrid, encode_grid, unfold

RES = 0.05


def _ring() -> ScanGrid:
    """Rectangular ring corridor 1.8 m wide: outer walls at 0 / 20 x 0 / 12, inner block."""
    w, h = int(20 / RES), int(12 / RES)
    occ = np.zeros((h, w), dtype=bool)
    occ[:2, :] = occ[-2:, :] = True  # 0.1 m walls
    occ[:, :2] = occ[:, -2:] = True
    occ[int(1.9 / RES) : int(10.1 / RES), int(1.9 / RES) : int(18.1 / RES)] = True
    return encode_grid(occ, (0.0, 0.0), RES)


def test_grid_round_trip_and_broken_payload() -> None:
    g = _ring()
    again = ScanGrid.model_validate_json(g.model_dump_json())
    assert (again.cells() == g.cells()).all() and again.cells()[0, 0]
    with pytest.raises(ValueError, match="bits_b64"):
        ScanGrid(origin=(0, 0), resolution=RES, width=10, height=10, bits_b64="AAAA")


def test_walls_follow_the_scan_and_centre_the_line() -> None:
    # Drawn 0.3 m off the corridor middle (y = 1.0 on the bottom straight) with a wrong width.
    pts = [(1.3, 1.3), (18.7, 1.3), (18.7, 10.7), (1.3, 10.7)]
    q = QuickTrack(points=pts, width_m=1.0, scan_walls=_ring())
    c = build_quick_track(q)
    bottom = (c.centreline[:, 1] < 3) & (c.centreline[:, 0] > 5) & (c.centreline[:, 0] < 15)
    assert bottom.sum() > 20
    assert np.median(c.centreline[bottom, 1]) == pytest.approx(1.0, abs=0.05)
    assert np.median(c.widths[bottom]) == pytest.approx(1.8, abs=0.06)
    # Without the scan the drawn width is used.
    plain = build_quick_track(q.model_copy(update={"scan_walls": None}))
    assert np.median(plain.widths) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("blocks", "reason"),
    [([(0.3, 1.9)], "through a scanned obstacle"), ([(0.1, 0.9), (1.1, 1.9)], "narrower than 0.3")],
)
def test_obstacles_on_the_line_and_pinches_are_reported(
    blocks: list[tuple[float, float]], reason: str
) -> None:
    occ = _ring().cells().copy()
    # 0.6 m long blocks (y ranges) on the bottom straight: across the line / a 0.2 m gap.
    for y0, y1 in blocks:
        occ[int(y0 / RES) : int(y1 / RES), int(9.7 / RES) : int(10.3 / RES)] = True
    q = QuickTrack(
        points=[(1.0, 1.0), (19.0, 1.0), (19.0, 11.0), (1.0, 11.0)],
        width_m=1.0,
        scan_walls=encode_grid(occ, (0.0, 0.0), RES),
    )
    with pytest.raises(GenerationError, match=reason):
        build_quick_track(q)


def test_unfold_removes_small_loops() -> None:
    wall = np.array([[0, 0], [2, 0], [2, 1], [1, 1], [1, -1], [3, -1], [5, -1]], dtype=float)
    out = unfold(wall, loop=False)
    assert len(out) == len(wall)
    assert not _segments_intersect(out, out, True)
