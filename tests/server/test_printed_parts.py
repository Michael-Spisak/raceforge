"""Spec 0019: import a 3D-printed part, use it in the editor, budget and MJCF."""

from pathlib import Path

import pytest
import trimesh
from fastapi.testclient import TestClient

from raceforge.api.service import Engine
from raceforge.parts.catalogue import Catalogue
from raceforge.server.app import create_app


def test_import_printed_part(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RACEFORGE_LOCAL_CATALOGUE", str(tmp_path / "catalogue.local.yaml"))
    monkeypatch.setenv("RACEFORGE_WORKSPACE_DIR", str(tmp_path / "workspace"))
    mesh = tmp_path / "mount.stl"
    trimesh.creation.box(extents=[40, 20, 10]).export(mesh)  # mm
    client = TestClient(create_app(Engine(Catalogue.load()), frontend_dist=None))
    req = {"path": str(mesh), "name": "Sensor mount", "material": "PETG", "infill_pct": 50}

    pv = client.post("/api/v1/parts/printed/preview", json=req).json()
    assert pv["watertight"] and abs(pv["volume_cm3"] - 8.0) < 1e-6
    assert abs(pv["mass_estimate_g"] - 8.0 * 1.27 * (0.25 + 0.75 * 0.5)) < 0.01  # 6.35 g
    assert pv["size_mm"] == [40.0, 20.0, 10.0] and pv["cost_eur"] > 0

    part = client.post("/api/v1/parts/printed", json=req).json()
    assert part["key"] == "printed-sensor-mount" and part["category"] == "printed"
    assert part["mesh_url"] and client.get(part["mesh_url"]).content[:5] != b""
    again = client.post("/api/v1/parts/printed", json=req).json()
    assert again["key"] == part["key"]  # identical re-import reuses the part
    other = client.post("/api/v1/parts/printed", json={**req, "infill_pct": 80}).json()
    assert other["key"] == "printed-sensor-mount-2"  # never replaces a part in use

    start = client.post("/api/v1/quickstart", json={}).json()["assembly"]
    added = client.post(
        "/api/v1/assembly/edit",
        json={
            "assembly": start,
            "op": {"kind": "add", "key": part["key"], "position": [0, 0, 0.2]},
        },
    ).json()
    view = next(p for p in added["parts"] if p["path"] == added["selected"])
    assert view["mesh_url"] == part["mesh_url"] and view["color"] == 25  # printed = orange
    line = next(i for i in added["budget"]["items"] if i["key"] == part["key"])
    assert line["unit_eur"] is not None and line["unit_eur"] > 0  # print cost from the volume
    mjcf = client.post("/api/v1/assembly/export/mjcf", json={"assembly": added["assembly"]})
    assert mjcf.status_code == 200

    bad = client.post(
        "/api/v1/parts/printed/preview", json={**req, "path": str(tmp_path / "x.step")}
    )
    assert bad.status_code == 422
