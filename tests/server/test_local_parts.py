"""Spec 0018: search the LDraw library and add parts to the local catalogue."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from raceforge.api.service import Engine
from raceforge.parts.catalogue import Catalogue
from raceforge.parts.ldraw import library_dir
from raceforge.server.app import create_app

pytestmark = pytest.mark.skipif(
    not (library_dir() / "parts" / "18654.dat").is_file(), reason="LDraw library not installed"
)


def test_search_add_and_use_an_ldraw_part(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local = tmp_path / "catalogue.local.yaml"
    monkeypatch.setenv("RACEFORGE_LOCAL_CATALOGUE", str(local))
    client = TestClient(create_app(Engine(Catalogue.load()), frontend_dist=None))
    hits = client.get("/api/v1/parts/ldraw", params={"query": "technic beam 1"}).json()
    assert any(h["ldraw_id"] == "18654" and not h["in_catalogue"] for h in hits)

    r = client.post(
        "/api/v1/parts/local",
        json={"ldraw_id": "18654", "category": "beam", "mass_g": 0.3, "holes": 1, "color": 0},
    )
    assert r.status_code == 200, r.text
    assert r.json()["origin"] == "local" and r.json()["connectors"] == 1
    assert local.is_file() and "18654" in Catalogue.load(local=local).entries

    # usable at once in the editor
    start = client.post("/api/v1/quickstart", json={}).json()["assembly"]
    added = client.post(
        "/api/v1/assembly/edit",
        json={"assembly": start, "op": {"kind": "add", "key": "18654", "position": [0, 0, 0.3]}},
    ).json()
    assert added["selected"] == ["18654-1"]
    assert client.get("/api/v1/parts/ldraw", params={"query": "18654"}).json()[0]["in_catalogue"]
    bad = client.post(
        "/api/v1/parts/local", json={"ldraw_id": "2780", "category": "pin", "mass_g": 1}
    )
    assert bad.status_code == 422  # curated parts stay curated
