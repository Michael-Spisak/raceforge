"""Spec 0024: car-height floor plan of a scan and the corridor width along a drawn line."""

import struct

import numpy as np
import trimesh

from raceforge.capture.floorplan import corridor_widths, floorplan, png_rgba
from raceforge.capture.tscan import Mesh


def _mesh(m: trimesh.Trimesh | trimesh.parent.Geometry, cls: int) -> Mesh:
    assert isinstance(m, trimesh.Trimesh)
    return Mesh(
        np.asarray(m.vertices, dtype=np.float64),
        np.asarray(m.faces, dtype=np.uint32),
        np.full(len(m.faces), cls, dtype=np.uint8),
    )


def _box(
    centre: tuple[float, float, float], extents: tuple[float, float, float]
) -> trimesh.Trimesh:
    b = trimesh.creation.box(extents=extents)
    b.apply_translation(centre)
    return b


def test_corridor_floorplan_and_width() -> None:
    # 10 m corridor, inner walls at y = ±0.8 (1.6 m free), floor at z = 0.3 (scan origin above it)
    walls = trimesh.util.concatenate(
        [_box((5, 0.85, 1.3), (10, 0.1, 2)), _box((5, -0.85, 1.3), (10, 0.1, 2))]
    )
    floor = _box((5, 0, 0.29), (10, 1.8, 0.02))
    ceiling = _box((5, 0, 2.3), (10, 1.8, 0.02))
    fp = floorplan([_mesh(walls, 1), _mesh(floor, 2), _mesh(ceiling, 3)])
    assert abs(fp.floor_z - 0.3) < 0.02
    assert fp.blocked(5.0, 0.85) and not fp.blocked(5.0, 0.0)  # floor/ceiling do not block
    widths = corridor_widths(fp, [(1.0, 0.0), (9.0, 0.0)])
    assert len(widths) > 20 and abs(float(np.median(widths)) - 1.6) <= 0.1
    png = png_rgba(fp)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", png[16:24]) == (fp.width, fp.height)


def test_band_ignores_things_above_the_car() -> None:
    shelf = _box((2, 0, 1.5), (1, 1, 0.1))  # 1.5 m high: not an obstacle for the car
    floor = _box((2, 0, -0.01), (4, 4, 0.02))
    fp = floorplan([_mesh(shelf, 4), _mesh(floor, 2)])
    assert not fp.occupied.any()
