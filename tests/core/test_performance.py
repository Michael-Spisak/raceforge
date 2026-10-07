"""AC8: performance targets (skipped on CI runners, which are slower and noisy)."""

import os
import time

import pytest

from raceforge.core.assembly import Assembly, Item, PartInstance, Submodel
from raceforge.core.io import dump, dump_fast, load
from raceforge.core.primitives import Pose, Vec3
from tests.core import factories

pytestmark = pytest.mark.skipif(
    os.environ.get("CI") == "true", reason="timing only on dev machines"
)


def test_load_2000_part_assembly_under_200ms() -> None:
    r = factories.ref()
    items: list[Item] = [
        PartInstance(id=f"p{n}", part=r, pose=Pose(position=Vec3(x=n * 0.008))) for n in range(2000)
    ]
    asm = Assembly(root="car", submodels={"car": Submodel(id="car", name="Car", items=items)})
    text = dump(asm)
    load(text)  # warm-up
    start = time.perf_counter()
    load(text)
    assert time.perf_counter() - start < 0.2


def test_telemetry_serialisation_under_50us() -> None:
    frame = factories.frame()
    n = 2000
    start = time.perf_counter()
    for _ in range(n):
        dump_fast(frame)
    assert (time.perf_counter() - start) / n < 50e-6
