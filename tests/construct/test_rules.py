"""Spec 0016: rule checker, budget and overlap check on edited assemblies."""

from raceforge.construct import editor as ed
from raceforge.construct import rules as r
from raceforge.construct.quickstart import QuickStartParams, generate
from raceforge.core.assembly import SubmodelRole
from raceforge.parts.catalogue import Catalogue

CAT = Catalogue.load()


def _car():
    return generate(QuickStartParams(), CAT).assembly


def test_quickstart_car_passes_the_lego_rules_and_prices_its_extras() -> None:
    a = _car()
    shop = r.budget(a, CAT, {}, 200.0)
    assert shop.total_eur == 0.0 and set(shop.missing) == {"rpi5", "powerbank"}
    checks = {c.id: c for c in r.check_rules(a, CAT, 0.8, shop, r.Limits())}
    assert checks["wheels_steering_lego"].ok and checks["steering_submodel_lego"].ok
    assert checks["ev3_drives"].ok
    assert checks["budget"].ok is False  # prices for the Pi and the power bank are missing
    assert checks["max_mass"].ok is None  # no limit set
    priced = r.budget(a, CAT, {"rpi5": 90.0, "powerbank": 25.0}, 200.0)
    assert priced.total_eur == 115.0
    checks = {c.id: c for c in r.check_rules(a, CAT, 0.8, priced, r.Limits(max_mass_kg=0.5))}
    assert checks["budget"].ok and checks["max_mass"].ok is False


def test_non_lego_part_in_the_steering_submodel_is_flagged() -> None:
    a = _car()
    steering = next(s for s in a.submodels.values() if s.role is SubmodelRole.STEERING)
    a, path = ed.add(a, CAT, "rpi5", (0.5, 0.5, 0.5))
    # move the new part from the root into the steering submodel
    root = a.submodels[a.root]
    inst = next(i for i in root.items if i.id == path[0])
    a = a.model_copy(
        update={
            "submodels": {
                **a.submodels,
                root.id: root.model_copy(
                    update={"items": [i for i in root.items if i.id != inst.id]}
                ),
                steering.id: steering.model_copy(update={"items": [*steering.items, inst]}),
            }
        }
    )
    shop = r.budget(a, CAT, {}, 200.0)
    check = next(
        c for c in r.check_rules(a, CAT, 0.8, shop, r.Limits()) if c.id == "steering_submodel_lego"
    )
    assert check.ok is False and any(p[-1] == inst.id for p in check.paths)


def test_overlap_of_two_unconnected_parts() -> None:
    a = _car()
    a, first = ed.add(a, CAT, "32524", (1.0, 1.0, 1.0))
    before = {frozenset(p) for p in r.overlaps(a, CAT)}
    a, second = ed.add(a, CAT, "32524", (1.0, 1.0, 1.002))
    after = {frozenset(p) for p in r.overlaps(a, CAT)}
    assert after - before == {frozenset((tuple(first), tuple(second)))}
