"""Spec 0007 AC3/AC4: the importer reads TrackScout passes — from the Python twin writer always,
and from the real Swift writer when CI provides one (RF_TSCAN_FIXTURE, made by
`swift run tscan-synth`)."""

import os
import shutil
import zipfile
from pathlib import Path

import numpy as np
import pytest

from raceforge.capture.tscan import ARKIT_CLASSES, TscanError, TscanPass, arkit_to_rf
from tests.capture.synth import depth_value, make_pass

SOURCES = ["python"] + (["swift"] if os.environ.get("RF_TSCAN_FIXTURE") else [])


@pytest.fixture(params=SOURCES)
def tscan(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    if request.param == "swift":
        return Path(os.environ["RF_TSCAN_FIXTURE"])
    return make_pass(tmp_path / "pass-1.tscan")


def test_summary_and_frames(tscan: Path) -> None:
    with TscanPass(tscan) as p:
        s = p.summary()
        assert s["frames"] == 20 and s["segments"] == 2 and s["depth_frames"] == 20
        assert s["aligned"] and s["world_map"] and s["meshes"] == 1
        assert s["frames_kept"] == 16  # the last 0.3 s (t = 2.6 … 2.9, inclusive) are discarded
        frames = list(p.frames())
        kept_t = [round(f.t, 3) for f in frames]
        assert 2.5 in kept_t and 2.7 not in kept_t
        assert len(frames) == s["frames_kept"]
        assert [f.segment for f in frames[:10]] == [0] * 10
        f5 = frames[5]
        assert f5.depth is not None and f5.confidence is not None
        assert f5.depth.shape == (12, 16)
        assert np.allclose(f5.depth, depth_value(5), atol=2e-3)
        assert int(f5.confidence[0, 0]) == 2
        assert f5.intrinsics[0, 0] == 210 and f5.intrinsics[0, 2] == 8 and f5.intrinsics[1, 2] == 6
        assert f5.tracking == "normal"
        assert p.world_map() == b"synthetic world map"


def test_poses_are_z_up(tscan: Path) -> None:
    with TscanPass(tscan) as p:
        t, poses = p.poses()
        assert len(t) == 16
        # frame 4: ARKit (0.4, 1.4, -0.2) → RaceForge (0.4, 0.2, 1.4): 1.4 m up, 0.2 m ahead
        assert np.allclose(poses[4][:3, 3], [0.4, 0.2, 1.4], atol=1e-6)
        # the camera's view direction (ARKit -z) points along RaceForge +y
        assert np.allclose(poses[4][:3, :3] @ [0, 0, -1], [0, 1, 0], atol=1e-6)


def test_golden_conversion() -> None:
    ar = np.eye(4)
    ar[:3, 3] = [1, 2, 3]
    assert np.allclose(arkit_to_rf(ar)[:3, 3], [1, -3, 2])


def test_mesh(tscan: Path) -> None:
    with TscanPass(tscan) as p:
        mesh = p.mesh(0)
        assert mesh is not None and p.mesh(1) is None
        assert mesh.faces.shape == (3, 3)
        assert [ARKIT_CLASSES[c] for c in mesh.classification] == ["floor", "floor", "wall"]
        assert np.allclose(mesh.vertices[:, 2][:4], 0)  # floor vertices at z = 0
        assert np.isclose(mesh.vertices[4, 2], 2)  # the wall's top corner 2 m up


def test_checksum_failure(tscan: Path, tmp_path: Path) -> None:
    broken = tmp_path / "broken.tscan"
    shutil.copy(tscan, broken)
    with zipfile.ZipFile(broken) as z:
        info = z.getinfo("frames.bin")
        offset = info.header_offset + 30 + len(info.filename) + len(info.extra)
    data = bytearray(broken.read_bytes())
    data[offset + 100] ^= 0xFF  # flip one byte inside frames.bin
    broken.write_bytes(bytes(data))
    with pytest.raises(TscanError, match=r"frames\.bin: checksum mismatch"):
        TscanPass(broken)


def test_not_a_tscan(tmp_path: Path) -> None:
    junk = tmp_path / "x.tscan"
    junk.write_text("hello")
    with pytest.raises(TscanError, match="not a ZIP"):
        TscanPass(junk)
    with zipfile.ZipFile(tmp_path / "y.tscan", "w") as z:
        z.writestr("manifest.json", '{"schema": "tscan", "schema_version": 9}')
    with pytest.raises(TscanError, match="version 9"):
        TscanPass(tmp_path / "y.tscan")


def test_capture_slug_matches_trackscout() -> None:
    from raceforge.cli import _capture_slug  # pyright: ignore[reportPrivateUsage]

    # Same expectations as TrackScoutKit's captureSlug (RecorderTests.swift) + case folding.
    assert _capture_slug("Gang 2. Stock \u2013 Süd") == "scan-gang-2-stock-sud"
    assert _capture_slug("!!!") == "scan-track"
    assert _capture_slug("Straße") == "scan-strasse"
    assert len(_capture_slug("a" * 100)) <= 63


def test_discarded_time_overlaps_and_clamps() -> None:
    from raceforge.capture.tscan import _covered  # pyright: ignore[reportPrivateUsage]

    assert _covered([(1.0, 3.0), (2.0, 4.0)], 0.0, 10.0) == 3.0
    assert _covered([(-1.0, 1.0), (9.0, 12.0)], 0.0, 10.0) == 2.0
