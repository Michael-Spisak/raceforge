"""Spec 0015: assembly editor operations through the engine API."""

from typing import Any

from fastapi.testclient import TestClient

from raceforge.api.service import Engine
from raceforge.server.app import create_app


def _edit(client: TestClient, assembly: dict[str, Any], **op: Any) -> dict[str, Any]:
    r = client.post("/api/v1/assembly/edit", json={"assembly": assembly, "op": op})
    assert r.status_code == 200, r.text
    return r.json()


def test_move_snap_rotate_delete_add() -> None:
    client = TestClient(create_app(Engine(), frontend_dist=None))
    start = client.post("/api/v1/quickstart", json={}).json()["assembly"]
    base = _edit(client, start)
    assert base["problems"] == [] and len(base["parts"]) > 20
    pin = next(p for p in base["parts"] if p["category"] == "pin")
    mass = base["derived"]["mass_kg"]

    # Snap: a pin dropped 3 mm beside a free beam's hole (across the hole axis) seats in the hole.
    beam = _edit(client, base["assembly"], kind="add", key="32524", position=[0, 0, 0.3])
    hole = next(p for p in beam["parts"] if p["path"] == beam["selected"])["connectors"][3]
    off = [h + (0.003 if i == 2 else 0.0) for i, h in enumerate(hole["pos"])]
    seated = _edit(client, beam["assembly"], kind="add", key=pin["key"], position=off)
    assert seated["snapped"] is not None and seated["snapped"]["target"] == beam["selected"]
    new_pin = next(p for p in seated["parts"] if p["path"] == seated["selected"])
    end = new_pin["connectors"][0]
    side = [e - h for e, h in zip(end["pos"], hole["pos"], strict=True)]
    along = sum(s * a for s, a in zip(side, hole["axis"], strict=True))
    assert sum((s - along * a) ** 2 for s, a in zip(side, hole["axis"], strict=True)) < 1e-10
    assert len(seated["assembly"]["connections"]) == len(beam["assembly"]["connections"]) + 1

    # Without snapping a move of one stud stays where it was put.
    r = client.post(
        "/api/v1/assembly/edit",
        json={
            "assembly": base["assembly"],
            "op": {"kind": "move", "path": pin["path"], "delta": [0, 0, 0.1]},
            "snap": False,
        },
    ).json()
    lifted = next(p for p in r["parts"] if p["path"] == pin["path"])
    assert abs(lifted["pos"][2] - pin["pos"][2] - 0.1) < 1e-9

    turned = _edit(client, base["assembly"], kind="rotate", path=pin["path"], axis="z", turns=1)
    assert next(p for p in turned["parts"] if p["path"] == pin["path"])["quat"] != pin["quat"]

    gone = _edit(client, base["assembly"], kind="delete", path=pin["path"])
    assert len(gone["parts"]) == len(base["parts"]) - 1 and gone["derived"]["mass_kg"] < mass
    assert gone["problems"] == []  # its connections were removed with it

    added = _edit(client, base["assembly"], kind="add", key=pin["key"], position=[0, 0, 0.3])
    assert added["selected"] and len(added["parts"]) == len(base["parts"]) + 1


def test_edited_car_drives_in_the_sim() -> None:
    from raceforge.api.service import TEMPLATES_DIR
    from raceforge.sim.runner import SIM_SENSORS

    client = TestClient(create_app(Engine(), frontend_dist=None))
    params = {"drive_gears": "20-28", "sensors": [s.model_dump(mode="json") for s in SIM_SENSORS]}
    start = client.post("/api/v1/quickstart", json=params).json()["assembly"]
    base = _edit(client, start)
    pin = next(p for p in base["parts"] if p["category"] == "pin")
    edited = _edit(client, base["assembly"], kind="delete", path=pin["path"])["assembly"]
    req = {
        "controller": str(TEMPLATES_DIR / "centering.py"),
        "corridor": {"seed": 0, "length_m": 20},
        "quickstart": params,
        "assembly": edited,
        "speed": 1000,
    }
    with client.websocket_connect("/api/v1/sim") as ws:
        ws.send_json(req)
        scene = ws.receive_json()
        assert scene["type"] == "scene", scene
        ego = next(c for c in scene["cars"] if c["name"] == "ego")
        assert sum(len(b["parts"]) for b in ego["bodies"]) == len(base["parts"]) - 1
        msg = ws.receive_json()
        while msg["type"] != "result":
            msg = ws.receive_json()
    assert msg["finished"], msg
