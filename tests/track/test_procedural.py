"""Spec 0003 AC5 (+ determinism part of AC1)."""

import numpy as np
import pytest

from raceforge.core.io import content_hash
from raceforge.track.procedural import (
    CorridorParams,
    GenerationError,
    _segments_intersect,
    generate_corridor,
)


@pytest.mark.parametrize("loop", [True, False])
def test_200_seeds_are_valid(loop: bool) -> None:
    for seed in range(100):
        params = CorridorParams(seed=seed, loop=loop)
        c = generate_corridor(params)
        assert c.widths.min() >= params.width_min_m - 1e-9
        left = np.array([[p.x, p.y] for p in c.track.walls[0].points])
        right = np.array([[p.x, p.y] for p in c.track.walls[1].points])
        assert not _segments_intersect(left, right, False)
        setup = c.track.race_setups[0]
        assert len(setup.start_grid) == 6
        assert len(setup.checkpoints) > 20


def test_deterministic_per_seed() -> None:
    a = generate_corridor(CorridorParams(seed=11))
    b = generate_corridor(CorridorParams(seed=11))
    c = generate_corridor(CorridorParams(seed=12))
    assert content_hash(a.track) == content_hash(b.track)
    assert content_hash(a.track) != content_hash(c.track)


def test_length_roughly_respected() -> None:
    c = generate_corridor(CorridorParams(seed=1, length_m=80))
    assert 60 < c.s[-1] < 110


def test_invalid_params() -> None:
    with pytest.raises(ValueError):
        CorridorParams(width_min_m=2.0, width_max_m=1.5)
    with pytest.raises(GenerationError):
        generate_corridor(
            CorridorParams(seed=0, width_min_m=3.9, width_max_m=4.0, length_m=20), max_attempts=1
        )
