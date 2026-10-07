"""Quick-start generator and derived data (spec 0002: AC1-AC4, AC8, AC10)."""

import itertools
import math
import os
import time

import pytest

from raceforge.construct.derive import derive
from raceforge.construct.ldraw_export import export_mpd, parse_mpd_parts
from raceforge.construct.quickstart import (
    Layout,
    QuickStartParams,
    SensorSpec,
    generate,
    vehicle_spec,
)
from raceforge.core.frames import iter_part_placements
from raceforge.parts.catalogue import Catalogue

GRID = list(
    itertools.product(
        Layout,
        (True, False),
        (11, 15, 21),
        (9, 11, 15),
        ("ev3_large", "dc_motor"),
        ("ev3_medium", "servo"),
    )
)


@pytest.mark.parametrize("layout,diff,wb,track,motor,steer", GRID)
def test_grid_generates_valid_assemblies(
    cat: Catalogue, layout: Layout, diff: bool, wb: int, track: int, motor: str, steer: str
) -> None:
    params = QuickStartParams.model_validate(
        {
            "layout": layout,
            "differential": diff,
            "wheelbase_studs": wb,
            "track_studs": track,
            "drive_motor": motor,
            "steering_motor": steer,
        }
    )
    res = generate(params, cat)
    res.assembly.validate_against_parts(cat.parts_by_hash())
    d = derive(res.assembly, cat, vehicle_spec(res, cat))
    assert d.wheelbase_m == pytest.approx(wb * 0.008, abs=1e-4)
    assert d.track_m == pytest.approx(track * 0.008, abs=1e-4)


@pytest.mark.parametrize(
    "field,value,hint",
    [
        ("wheelbase_studs", 25, "nearest valid: [20, 21]"),
        ("track_studs", 7, "nearest valid: [8, 9]"),
        ("wheel", "huge", "valid: ['56908+55976']"),
        ("drive_gears", "12-36", "valid: ['12-28', '20-28']"),
    ],
)
def test_invalid_parameters_list_valid_options(field: str, value: object, hint: str) -> None:
    with pytest.raises(ValueError) as exc:
        QuickStartParams.model_validate({field: value})
    if field == "track_studs":
        assert "nearest valid: [9, 10]" in str(exc.value)
    else:
        assert hint in str(exc.value)


def test_default_car_mass_is_sum_of_catalogue_masses(cat: Catalogue) -> None:
    res = generate(QuickStartParams(), cat)
    expected = (
        sum(
            cat.entry(cat.key_for_hash(inst.part.content_hash)).mass_g
            for _, inst, _ in iter_part_placements(res.assembly)
        )
        / 1000
    )
    d = derive(res.assembly, cat, vehicle_spec(res, cat))
    assert d.mass_kg == pytest.approx(expected)
    assert d.mass_kg == pytest.approx(0.84115, abs=1e-5)  # golden value for the default car
    assert not d.mass_is_measured
    assert d.warnings == []


def test_measured_overrides(cat: Catalogue) -> None:
    res = generate(QuickStartParams(measured_mass_kg=1.0, measured_cog_mm=(60, 0, 50)), cat)
    d = derive(res.assembly, cat, vehicle_spec(res, cat))
    assert d.mass_kg == 1.0
    assert d.cog_m == pytest.approx((0.06, 0.0, 0.05))
    assert d.mass_is_measured


def test_plausibility_checks_fire(cat: Catalogue) -> None:
    import dataclasses

    res = generate(QuickStartParams(max_steer_deg=15, wheelbase_studs=21), cat)
    spec = vehicle_spec(res, cat)
    weak = spec.drives[0].motor.model_copy(update={"stall_torque_nm": 0.001})
    bad = dataclasses.replace(
        spec,
        corridor_min_width_m=0.3,
        measured_cog_m=(0.08, 0.0, 0.6),
        drives=[dataclasses.replace(spec.drives[0], motor=weak, gear_ratio=0.2)],
        steering_motor=spec.steering_motor.model_copy(update={"stall_torque_nm": 0.001}),
    )
    codes = {w.code for w in derive(res.assembly, cat, bad).warnings}
    assert {"turning_radius", "weak_drive", "rollover", "weak_steering"} <= codes


def test_sensor_presets(cat: Catalogue) -> None:
    params = QuickStartParams(
        sensors=[
            SensorSpec(kind="ev3_gyro", preset="center"),
            SensorSpec(kind="ev3_ultrasonic", preset="rear"),
        ]
    )
    res = generate(params, cat)
    keys = parse_mpd_parts(export_mpd(res.assembly, cat))
    assert keys["99380"] == 1 and keys["95652"] == 1


def test_mpd_round_trip_part_list(cat: Catalogue) -> None:
    res = generate(QuickStartParams(), cat)
    text = export_mpd(res.assembly, cat)
    counted = parse_mpd_parts(text)
    expected: dict[str, int] = {}
    for _, inst, _ in iter_part_placements(res.assembly):
        e = cat.entry(cat.key_for_hash(inst.part.content_hash))
        k = e.ldraw_id or e.key
        expected[k] = expected.get(k, 0) + 1
    assert dict(counted) == expected
    assert "0 !RACEFORGE PLACEHOLDER rpi5" in text
    assert text == export_mpd(res.assembly, cat)  # deterministic


def test_turning_radius_formula(cat: Catalogue) -> None:
    res = generate(QuickStartParams(ackermann_pct=100), cat)
    d = derive(res.assembly, cat, vehicle_spec(res, cat))
    # Ideal Ackermann: cot(outer) - cot(inner) = track / wheelbase
    assert 1 / math.tan(d.steer_outer_rad) - 1 / math.tan(d.steer_inner_rad) == pytest.approx(
        d.track_m / d.wheelbase_m
    )


@pytest.mark.skipif(os.environ.get("CI") == "true", reason="timing only on dev machines")
def test_generation_under_one_second(cat: Catalogue) -> None:
    from raceforge.sim.mjcf import build_mjcf

    start = time.perf_counter()
    res = generate(QuickStartParams(), cat)
    spec = vehicle_spec(res, cat)
    derive(res.assembly, cat, spec)
    build_mjcf(res.assembly, cat, spec)
    assert time.perf_counter() - start < 1.0
