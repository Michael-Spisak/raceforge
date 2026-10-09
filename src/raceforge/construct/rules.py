"""Live rule checker, budget and overlap check for the Construct editor (spec 0016).

Rules come from docs/PLAN.md §1 "Rule checker"; results are ids + parameters so the UI can show
them in German or English. LEGO parts are catalogue parts with an LDraw id (EV3 devices included);
school-kit LEGO parts cost 0 € unless a price is set.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

from raceforge.construct.derive import PlacedPart, placed_parts
from raceforge.core.assembly import Assembly, SubmodelRole
from raceforge.parts.catalogue import Catalogue, Category

EV3_MOTORS = {"95658", "99455"}
LEGO_ONLY = {Category.TYRE, Category.WHEEL_RIM, Category.STEERING_ARM, Category.STEERING_LINK}
OVERLAP_SHRINK_M = 0.0008  # tolerance: boxes are shrunk by this before testing


@dataclass(frozen=True)
class Limits:
    max_length_m: float | None = None
    max_width_m: float | None = None
    max_height_m: float | None = None
    max_mass_kg: float | None = None


@dataclass(frozen=True)
class RuleResult:
    id: str
    ok: bool | None  # None: not checked (no limit set)
    params: dict[str, str | float] = field(default_factory=dict[str, str | float])
    paths: list[tuple[str, ...]] = field(default_factory=list[tuple[str, ...]])


@dataclass(frozen=True)
class BudgetItem:
    key: str
    name: str
    count: int
    unit_eur: float | None  # None: no price known


@dataclass(frozen=True)
class Budget:
    items: list[BudgetItem]
    limit_eur: float

    @property
    def total_eur(self) -> float:
        return sum(i.count * (i.unit_eur or 0.0) for i in self.items)

    @property
    def missing(self) -> list[str]:
        return [i.key for i in self.items if i.unit_eur is None]


def is_lego(cat: Catalogue, key: str) -> bool:
    return cat.entry(key).ldraw_id is not None


def default_price(cat: Catalogue, key: str, filament_eur_per_kg: float) -> float | None:
    """LEGO (school kit): 0 €; 3D-printed: estimated print cost; anything else: unknown."""
    entry = cat.entry(key)
    if entry.printed is not None:
        from raceforge.parts.printed import print_cost_eur

        p = entry.printed
        return round(print_cost_eur(p.volume_cm3, p.material, p.infill_pct, filament_eur_per_kg), 2)
    return 0.0 if is_lego(cat, key) else None


def budget(
    assembly: Assembly,
    cat: Catalogue,
    prices: Mapping[str, float],
    limit_eur: float,
    filament_eur_per_kg: float = 25.0,
) -> Budget:
    counts = Counter(p.key for p in placed_parts(assembly, cat))
    items = [
        BudgetItem(
            key=key,
            name=cat.part(key).name,
            count=n,
            unit_eur=prices.get(key, default_price(cat, key, filament_eur_per_kg)),
        )
        for key, n in sorted(counts.items())
    ]
    return Budget(items, limit_eur)


def _steering_paths(assembly: Assembly) -> list[tuple[str, ...]]:
    """Instance chains that lie inside a submodel with role ``steering``."""
    out: list[tuple[str, ...]] = []

    def walk(sub_id: str, chain: tuple[str, ...], inside: bool) -> None:
        sub = assembly.submodels[sub_id]
        inside = inside or sub.role is SubmodelRole.STEERING
        for item in sub.items:
            if item.kind == "part":
                if inside:
                    out.append((*chain, item.id))
            else:
                walk(item.submodel, (*chain, item.id), inside)

    walk(assembly.root, (), False)
    return out


def check_rules(
    assembly: Assembly,
    cat: Catalogue,
    mass_kg: float,
    shop: Budget,
    limits: Limits,
) -> list[RuleResult]:
    parts = placed_parts(assembly, cat)
    by_path = {p.path: p for p in parts}
    results: list[RuleResult] = []

    bad = [p.path for p in parts if p.category in LEGO_ONLY and not is_lego(cat, p.key)]
    results.append(RuleResult("wheels_steering_lego", not bad, paths=bad))

    steering = _steering_paths(assembly)
    bad = [
        path
        for path in steering
        if not is_lego(cat, by_path[path].key) and by_path[path].category is not Category.MOTOR
    ]
    results.append(
        RuleResult("steering_submodel_lego", not bad, {"parts": len(steering)}, paths=bad)
    )

    keys = {p.key for p in parts}
    has_brick = any(p.category is Category.EV3_BRICK for p in parts)
    results.append(RuleResult("ev3_drives", has_brick and bool(keys & EV3_MOTORS)))

    total = shop.total_eur
    missing = shop.missing
    results.append(
        RuleResult(
            "budget",
            total <= shop.limit_eur and not missing,
            {"total": round(total, 2), "limit": shop.limit_eur, "missing": ", ".join(missing)},
        )
    )

    if parts:
        lo = np.min([p.world_bbox()[0] for p in parts], axis=0)
        hi = np.max([p.world_bbox()[1] for p in parts], axis=0)
        size = hi - lo
    else:
        size = np.zeros(3)
    for rid, value, limit in (
        ("max_length", float(size[0]), limits.max_length_m),
        ("max_width", float(size[1]), limits.max_width_m),
        ("max_height", float(size[2]), limits.max_height_m),
        ("max_mass", mass_kg, limits.max_mass_kg),
    ):
        ok = None if limit is None else value <= limit + 1e-9
        params: dict[str, str | float] = {"value": round(value, 4)}
        if limit is not None:
            params["limit"] = limit
        results.append(RuleResult(rid, ok, params))
    return results


def _obb(p: PlacedPart) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    half = np.maximum(p.size / 2 - OVERLAP_SHRINK_M, 0.0)
    return p.center, p.rotation, half


def _obb_overlap(a: PlacedPart, b: PlacedPart) -> bool:
    """Separating-axis test for two oriented boxes."""
    ca, ra, ha = _obb(a)
    cb, rb, hb = _obb(b)
    if not (ha > 0).all() or not (hb > 0).all():
        return False
    d = cb - ca
    axes = [ra[:, i] for i in range(3)] + [rb[:, i] for i in range(3)]
    axes += [np.cross(ra[:, i], rb[:, j]) for i in range(3) for j in range(3)]
    for ax in axes:
        n = float(np.linalg.norm(ax))
        if n < 1e-9:
            continue
        ax = ax / n
        ra_proj = float(np.sum(ha * np.abs(ra.T @ ax)))
        rb_proj = float(np.sum(hb * np.abs(rb.T @ ax)))
        if abs(float(d @ ax)) > ra_proj + rb_proj:
            return False
    return True


def overlaps(assembly: Assembly, cat: Catalogue) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """Pairs of parts whose boxes interpenetrate although they are not connected to each other."""
    parts = placed_parts(assembly, cat)
    connected = {
        frozenset((tuple(c.a.instances), tuple(c.b.instances))) for c in assembly.connections
    }
    boxes = [p.world_bbox() for p in parts]
    out: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for i, a in enumerate(parts):
        lo_a, hi_a = boxes[i]
        for j in range(i + 1, len(parts)):
            lo_b, hi_b = boxes[j]
            if (hi_a < lo_b + 2 * OVERLAP_SHRINK_M).any() or (
                hi_b < lo_a + 2 * OVERLAP_SHRINK_M
            ).any():
                continue  # broad phase: world AABBs apart
            b = parts[j]
            if frozenset((a.path, b.path)) in connected:
                continue
            if _obb_overlap(a, b):
                out.append((a.path, b.path))
    return out
