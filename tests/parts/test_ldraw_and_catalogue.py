"""LDraw reader on a synthetic mini library; catalogue loading (spec 0002, AC9)."""

from pathlib import Path

import pytest

from raceforge.core.connectors import ConnectorType
from raceforge.parts.catalogue import Catalogue, deterministic_object_id
from raceforge.parts.ldraw import LDrawLibrary


@pytest.fixture
def mini_lib(tmp_path: Path) -> Path:
    (tmp_path / "parts" / "s").mkdir(parents=True)
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "box.dat").write_text(
        "0 Box\n4 16 -1 -1 0 1 -1 0 1 1 0 -1 1 0\n4 16 -1 -1 2 1 -1 2 1 1 2 -1 1 2\n"
    )
    (tmp_path / "parts" / "s" / "sub.dat").write_text(
        "0 ~Sub\n1 16 0 0 0 10 0 0 0 10 0 0 0 10 box.dat\n"
    )
    (tmp_path / "parts" / "test.dat").write_text(
        "0 Test Part\n1 16 100 0 0 1 0 0 0 1 0 0 0 1 s\\sub.dat\n3 16 0 0 0 1 0 0 0 1 0\n"
    )
    return tmp_path


def test_bbox_resolves_subfiles_and_transforms(mini_lib: Path) -> None:
    lib = LDrawLibrary(mini_lib)
    assert lib.title("test.dat") == "Test Part"
    box = lib.bbox("TEST.DAT")
    assert box is not None
    assert box.lo == (0.0, -10.0, 0.0)
    assert box.hi == (110.0, 10.0, 20.0)
    assert lib.bbox("missing.dat") is None


def test_catalogue_loads_without_ldraw_library(cat: Catalogue) -> None:
    beam = cat.part("32524")
    assert beam.ldraw_id == "32524"
    assert [c.id for c in beam.connectors][:2] == ["h1", "h2"]
    assert all(c.type is ConnectorType.PIN_HOLE for c in beam.connectors)
    lo, hi = cat.bbox("32524")
    assert abs((hi[1] - lo[1]) - 0.0552) < 1e-6  # 7 holes = 56 mm minus 0.8 mm play
    assert cat.part("95652").device is not None


def test_catalogue_refs_are_deterministic(cat: Catalogue) -> None:
    assert cat.ref("32524") == Catalogue.load().ref("32524")
    assert deterministic_object_id("x") == deterministic_object_id("x")
    assert deterministic_object_id("x")[14] == "7"
    assert cat.key_for_hash(cat.ref("2780").content_hash) == "2780"


def test_unknown_part_raises(cat: Catalogue) -> None:
    with pytest.raises(KeyError, match="not in the catalogue"):
        cat.entry("nope")
