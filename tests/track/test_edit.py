"""Spec 0020: track editor layer and validator."""

import numpy as np

from raceforge.sim.world import _track_xml
from raceforge.track.edit import EditObject, EditRandom, TrackEdit, validate
from raceforge.track.quick import QuickTrack, build_quick_track

RECT = {"points": [[0, 0], [10, 0], [10, 5], [0, 5]], "width_m": 1.6, "laps": 1}


def q(edit: dict | None = None) -> QuickTrack:
    return QuickTrack.model_validate({**RECT, "edit": edit})


def codes(edit: dict | None) -> set[str]:
    cor = build_quick_track(q(edit))
    return {i.code for i in validate(cor, cor_edit(edit)).items}


def cor_edit(edit: dict | None) -> TrackEdit | None:
    return None if edit is None else TrackEdit.model_validate(edit)


def test_no_edit_is_unchanged_and_valid() -> None:
    a = build_quick_track(q())
    assert a.track == build_quick_track(QuickTrack.model_validate(RECT)).track
    assert validate(a).ok


def test_edited_setup_and_grid() -> None:
    base = build_quick_track(q()).track.race_setups[0]
    mid = (
        (base.start_line.a.x + base.start_line.b.x) / 2,
        (base.start_line.a.y + base.start_line.b.y) / 2,
    )
    edit = {"race_setup": {"grid_cars": 4, "laps": 2}}
    s = build_quick_track(q(edit)).track.race_setups[0]
    assert len(s.start_grid) == 4 and s.laps == 2
    # grid is behind the start line
    d = base.direction
    for g in s.start_grid:
        assert (g.x - mid[0]) * d.x + (g.y - mid[1]) * d.y < 0
    # separate finish line → point to point, one lap
    fin = ((5.0, -0.7), (5.0, 0.7))
    s2 = build_quick_track(q({"race_setup": {"finish_line": fin, "laps": 3}})).track.race_setups[0]
    assert s2.start_line != s2.finish_line and s2.laps == 1


def test_validator_findings() -> None:
    assert "grid_slot_in_wall" in codes({"race_setup": {"grid_poses": [[5, -3, 0]]}})
    blocker = {"id": "b", "kind": "bench", "x": 5, "y": 0, "yaw_deg": 90}
    assert "object_blocks" in codes({"objects": [blocker], "max_car_width_m": 0.45})
    assert "too_narrow" in codes({"max_car_width_m": 1.5})
    assert "check_off" in codes({"checks": [{"a": [0, 0], "b": [10, 0], "measured_m": 9.94}]})
    far = {"race_setup": {"start_line": [[50, 50], [50, 52]]}}
    assert "start_line_outside" in codes(far)


def test_randomisation_in_sim() -> None:
    obj = EditObject(
        id="c",
        kind="cone",
        x=5,
        y=0,
        randomisation=EditRandom(position_xy_m=0.3, presence_prob=0.0),
    )
    track = build_quick_track(q({"objects": [obj.model_dump()]})).track
    assert track.objects[0].class_id == "cone" and any(c.id == "cone" for c in track.classes)
    xml, _, _ = _track_xml(track, "main", np.random.default_rng(1), True)
    assert "obj_c" not in xml  # presence probability 0
    xml, _, _ = _track_xml(track, "main", np.random.default_rng(1), False)
    assert "obj_c" in xml
