"""Engine services used by the UI server, CLI and (later) MCP (spec 0008)."""

import dataclasses
import math
from pathlib import Path
from typing import Any, Literal

import numpy as np

from raceforge import __version__
from raceforge.api.models import (
    AssemblyEditRequest,
    AssemblyEditResponse,
    BudgetLine,
    BudgetView,
    CarScene,
    ControllerInfo,
    CorridorResponse,
    EditorConnector,
    EditorPartView,
    Health,
    LDrawPart,
    LocalPartRequest,
    PartSummary,
    Primitive,
    PrintedImportRequest,
    PrintedPreview,
    QuickstartResponse,
    QuickstartSchema,
    ReplaySummary,
    RuleCheck,
    SceneBody,
    ScenePart,
    SnapInfo,
    Warning,
)
from raceforge.construct.derive import derive
from raceforge.construct.ldraw_export import COLOURS, export_mpd
from raceforge.construct.quickstart import (
    DIFF_GEARS,
    LOCKED_GEARS,
    WHEELS,
    QuickStartParams,
    QuickStartResult,
    generate,
    vehicle_spec,
)
from raceforge.core.assembly import Assembly, AssemblyError
from raceforge.core.frames import iter_part_placements
from raceforge.core.io import dump, load_as, to_jsonable
from raceforge.parts.catalogue import Catalogue
from raceforge.parts.ldraw import library_dir
from raceforge.sim.mjcf import build_mjcf
from raceforge.sim.record import read_frames, read_truth
from raceforge.track.procedural import CorridorParams, generate_corridor

TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "controllers" / "templates"
# Function colours (plan §1): steering, drive, sensors, electronics, chassis; printed parts.
ROLE_COLOURS = {"steering": 1, "drive": 4, "sensor_mast": 14, "electronics": 2, "chassis": 71}
PRINTED_COLOUR = 25
_GEOM_KIND: dict[int, Literal["box", "cylinder", "plane"]] = {0: "plane", 5: "cylinder", 6: "box"}


class Engine:
    """Stateless-ish facade; caches the catalogue."""

    def __init__(self, catalogue: Catalogue | None = None) -> None:
        self.cat = catalogue or Catalogue.load()

    # ---- health & parts
    def health(self) -> Health:
        root = library_dir()
        return Health(
            version=__version__, ldraw_available=(root / "parts").is_dir(), ldraw_dir=str(root)
        )

    def parts(self, query: str = "", category: str = "") -> list[PartSummary]:
        q = query.lower().strip()
        out: list[PartSummary] = []
        for e in self.cat.entries.values():
            if category and e.category.value != category:
                continue
            if q and q not in e.name.lower() and q not in (e.ldraw_id or "") and q not in e.key:
                continue
            out.append(
                PartSummary(
                    key=e.key,
                    ldraw_id=e.ldraw_id,
                    name=e.name,
                    category=e.category.value,
                    mass_g=e.mass_g,
                    connectors=len(e.all_connectors()),
                    device=e.device.type if e.device else None,
                    verified=False,
                    color=e.color,
                    origin=e.origin,
                    mesh_url=self.mesh_url(e.key),
                )
            )
        return out

    def mesh_url(self, key: str) -> str | None:
        return f"/api/v1/parts/printed/{key}/mesh" if self.cat.entry(key).printed else None

    def mesh_path(self, key: str) -> Path:
        from raceforge.parts.catalogue import printed_dir

        printed = self.cat.entry(key).printed
        if printed is None:
            raise KeyError(key)
        return printed_dir() / printed.mesh

    def printed_preview(self, req: PrintedImportRequest) -> PrintedPreview:
        from raceforge.api.construct_settings import load_settings
        from raceforge.parts.printed import estimate_mass_g, load_mesh, print_cost_eur

        m = load_mesh(Path(req.path).expanduser(), req.units, req.up)
        mass = estimate_mass_g(m.volume_cm3, req.material, req.infill_pct)
        eur_kg = load_settings().filament_eur_per_kg
        return PrintedPreview(
            volume_cm3=round(m.volume_cm3, 2),
            watertight=m.watertight,
            size_mm=(round(m.size_mm[0], 2), round(m.size_mm[1], 2), round(m.size_mm[2], 2)),
            faces=len(m.mesh.faces),
            mass_estimate_g=round(req.measured_mass_g or mass, 2),
            cost_eur=round(print_cost_eur(m.volume_cm3, req.material, req.infill_pct, eur_kg), 2),
        )

    def import_printed(self, req: PrintedImportRequest) -> PartSummary:
        """Add a 3D-printed part to the local catalogue (mesh stored next to it, spec 0019)."""
        from raceforge.parts.catalogue import (
            CatalogueEntry,
            Category,
            PrintedSpec,
            printed_dir,
            save_local_entry,
        )
        from raceforge.parts.printed import estimate_mass_g, load_mesh, slug_key

        m = load_mesh(Path(req.path).expanduser(), req.units, req.up)
        key, n = slug_key(req.name), 1
        while key in self.cat.entries:  # never replace: saved assemblies keep their part
            n += 1
            key = f"{slug_key(req.name)}-{n}"
        folder = printed_dir()
        folder.mkdir(parents=True, exist_ok=True)
        m.mesh.export(folder / f"{key}.stl")  # pyright: ignore[reportUnknownMemberType]
        lo, hi = m.mesh.bounds * 1000
        estimate = estimate_mass_g(m.volume_cm3, req.material, req.infill_pct)
        entry = CatalogueEntry(
            key=key,
            name=req.name,
            category=Category.PRINTED,
            mass_g=req.measured_mass_g or round(max(estimate, 0.01), 2),
            mass_source="measured" if req.measured_mass_g else "volume x density x fill (estimate)",
            color=25,
            bbox_mm=(
                (round(float(lo[0]), 3), round(float(lo[1]), 3), round(float(lo[2]), 3)),
                (round(float(hi[0]), 3), round(float(hi[1]), 3), round(float(hi[2]), 3)),
            ),
            origin="local",
            printed=PrintedSpec(
                mesh=f"{key}.stl",
                material=req.material,
                infill_pct=req.infill_pct,
                volume_cm3=round(m.volume_cm3, 3),
            ),
        )
        same = self.cat.key_for_entry(entry)
        if same is not None:  # identical part already there: reuse it
            (folder / f"{key}.stl").unlink(missing_ok=True)
            return next(p for p in self.parts(same) if p.key == same)
        save_local_entry(entry)
        self.cat.extend(entry)
        return next(p for p in self.parts(key) if p.key == key)

    def ldraw_search(self, query: str, limit: int = 50) -> list[LDrawPart]:
        """Parts of the whole LDraw library (spec 0018); every word must match."""
        from raceforge.parts.ldraw import part_index

        words = query.lower().split()
        known = {e.ldraw_id for e in self.cat.entries.values() if e.ldraw_id}
        out: list[LDrawPart] = []
        for p in part_index(library_dir()):
            text = f"{p.ldraw_id} {p.title}".lower()
            if all(w in text for w in words):
                out.append(
                    LDrawPart(
                        ldraw_id=p.ldraw_id,
                        title=p.title,
                        category=p.category,
                        in_catalogue=p.ldraw_id in known,
                    )
                )
                if len(out) >= limit:
                    break
        return out

    def add_local_part(self, req: LocalPartRequest) -> PartSummary:
        """Add an LDraw part to the local catalogue (bbox from the geometry) and use it at once."""
        from raceforge.parts.catalogue import (
            CatalogueEntry,
            Category,
            core_bbox_mm,
            save_local_entry,
        )
        from raceforge.parts.ldraw import LDrawLibrary

        try:
            category = Category(req.category)
        except ValueError as e:
            raise ValueError(f"category: unknown {req.category!r}") from e
        existing = self.cat.entries.get(req.ldraw_id)
        if existing is not None and existing.origin == "curated":
            raise ValueError(f"{req.ldraw_id} is already in the curated catalogue")
        lib = LDrawLibrary(library_dir())
        box = lib.bbox(f"{req.ldraw_id}.dat")
        if box is None:
            raise ValueError(f"{req.ldraw_id}.dat not found in the LDraw library")
        title = lib.title(f"{req.ldraw_id}.dat") or req.ldraw_id
        entry = CatalogueEntry(
            key=req.ldraw_id,
            ldraw_id=req.ldraw_id,
            name=req.name or title,
            category=category,
            mass_g=req.mass_g,
            mass_source="entered by the team (unverified)",
            holes=req.holes,
            length_studs=req.length_studs,
            color=req.color,
            bbox_mm=core_bbox_mm(box.lo, box.hi),
            origin="local",
        )
        save_local_entry(entry)
        self.cat.extend(entry)
        return next(p for p in self.parts(req.ldraw_id) if p.key == entry.key)

    # ---- quick-start
    def quickstart_schema(self) -> QuickstartSchema:
        return QuickstartSchema(
            json_schema=QuickStartParams.model_json_schema(),
            defaults=QuickStartParams().model_dump(mode="json"),
            options={
                "layout": ["rwd", "awd", "fwd"],
                "wheelbase_studs": list(range(11, 22)),
                "track_studs": list(range(9, 16)),
                "wheel": list(WHEELS),
                "drive_gears_differential": list(DIFF_GEARS),
                "drive_gears_locked": list(LOCKED_GEARS),
                "drive_motor": ["ev3_large", "ev3_medium", "dc_motor"],
                "steering_motor": ["ev3_medium", "servo"],
                "board": ["raspberry_pi_5", "orange_pi_5", "none"],
                "battery": ["powerbank", "none"],
                "sensor_kind": ["ev3_ultrasonic", "ev3_gyro", "ev3_touch", "lidar_2d"],
                "sensor_preset": ["front", "left", "right", "rear", "center", "top"],
            },
        )

    def quickstart(self, params: QuickStartParams) -> QuickstartResponse:
        res = generate(params, self.cat)
        derived = derive(res.assembly, self.cat, vehicle_spec(res, self.cat))
        d = dataclasses.asdict(derived)
        warnings = [Warning(code=w["code"], message=w["message"]) for w in d.pop("warnings")]
        return QuickstartResponse(
            assembly=to_jsonable(res.assembly),
            derived=d,
            warnings=warnings,
            car=self.car_scene("car", res.assembly),
        )

    def edit_assembly(self, req: AssemblyEditRequest) -> AssemblyEditResponse:
        """Apply one editor operation and evaluate the result (spec 0015)."""
        from raceforge.construct import editor as ed

        assembly = load_as(Assembly, req.assembly)
        op = req.op
        selected: list[str] | None = op.path or None
        many: list[list[str]] = op.paths or ([op.path] if op.path else [])
        snapped: SnapInfo | None = None
        candidates = 0
        if op.paths and op.kind in ("move", "rotate", "delete", "duplicate", "mirror"):
            if op.kind == "move":
                assembly = ed.move_many(assembly, op.paths, op.delta)
            elif op.kind == "rotate":
                assembly = ed.rotate_many(assembly, op.paths, op.axis, op.turns)
            elif op.kind == "delete":
                assembly, many = ed.delete_many(assembly, op.paths), []
            elif op.kind == "duplicate":
                assembly, many = ed.duplicate(assembly, self.cat, op.paths, op.delta)
            else:
                assembly, many = ed.mirror_copy(assembly, self.cat, op.paths, op.axis)
            selected = many[0] if len(many) == 1 else None
        elif op.kind == "move":
            assembly = ed.move(assembly, op.path, op.delta)
        elif op.kind == "rotate":
            assembly = ed.rotate(assembly, op.path, op.axis, op.turns)
        elif op.kind == "delete":
            assembly, selected = ed.delete(assembly, op.path), None
        elif op.kind == "attach":
            if not op.key or not op.path:
                raise ValueError("attach: key (catalogue part) and path (target part) are needed")
            assembly, new, candidates = ed.attach(assembly, self.cat, op.key, op.path, op.candidate)
            if not candidates:
                raise ValueError(
                    f"{op.key} has no connector that fits a free connector of the part"
                )
            selected = new
        elif op.kind == "add":
            if not op.key:
                raise ValueError("key: which catalogue part to add")
            assembly, selected = ed.add(assembly, self.cat, op.key, op.position)
        if not op.paths:
            many = [selected] if selected else []
        if (
            selected
            and not op.paths
            and (op.kind == "snap" or (req.snap and op.kind in ("move", "add")))
        ):
            res = ed.snap(assembly, self.cat, selected)
            assembly = res.assembly
            if res.snapped and res.connector and res.target and res.target_connector:
                snapped = SnapInfo(
                    connector=res.connector,
                    target=list(res.target),
                    target_connector=res.target_connector,
                    distance_m=res.distance_m,
                )
        problems: list[str] = []
        try:
            assembly.validate_against_parts(self.cat.parts_by_hash())
        except AssemblyError as e:
            problems = [line for line in str(e).splitlines() if line.strip()]
        spec = vehicle_spec(generate(req.quickstart, self.cat), self.cat)
        d = dataclasses.asdict(derive(assembly, self.cat, spec))
        warnings = [Warning(code=w["code"], message=w["message"]) for w in d.pop("warnings")]
        parts = [
            EditorPartView(
                path=list(p.path),
                key=p.key,
                name=p.name,
                ldraw_id=self.cat.entry(p.key).ldraw_id,
                category=p.category,
                color=self.colours(assembly, list(p.path), p.key, p.color)[0],
                real_color=self.colours(assembly, list(p.path), p.key, p.color)[1],
                mesh_url=self.mesh_url(p.key),
                pos=_v3(p.position),
                quat=_mat_to_quat(p.rotation),
                bbox_lo=_v3(p.bbox_lo),
                bbox_hi=_v3(p.bbox_hi),
                mirrored=p.mirrored,
                linked=p.linked,
                connectors=[
                    EditorConnector(
                        id=c.id, type=c.type.value, pos=_v3(c.position), axis=_v3(c.axis)
                    )
                    for c in p.connectors
                ],
            )
            for p in ed.view(assembly, self.cat)
        ]
        rules, overlap_pairs, budget = self._rules(assembly, float(d["mass_kg"]))
        return AssemblyEditResponse(
            assembly=to_jsonable(assembly),
            parts=parts,
            derived=d,
            warnings=warnings,
            problems=problems,
            selected=selected,
            selected_many=many,
            candidates=candidates,
            snapped=snapped,
            rules=rules,
            overlaps=overlap_pairs,
            budget=budget,
        )

    def _rules(
        self, assembly: Assembly, mass_kg: float
    ) -> tuple[list[RuleCheck], list[list[list[str]]], BudgetView]:
        """Rule checker, overlaps and budget (spec 0016) with the local Construct settings."""
        from datetime import date

        from raceforge.api.construct_settings import load_settings
        from raceforge.construct import rules as r

        cfg = load_settings()
        shop = r.budget(
            assembly,
            self.cat,
            {k: v.eur for k, v in cfg.prices.items()},
            cfg.budget_eur,
            cfg.filament_eur_per_kg,
        )
        lim = cfg.limits
        checks = r.check_rules(
            assembly,
            self.cat,
            mass_kg,
            shop,
            r.Limits(lim.max_length_m, lim.max_width_m, lim.max_height_m, lim.max_mass_kg),
        )
        pairs = r.overlaps(assembly, self.cat)
        rules = [
            RuleCheck(id=c.id, ok=c.ok, params=c.params, paths=[list(p) for p in c.paths])
            for c in checks
        ]
        rules.append(RuleCheck(id="overlaps", ok=not pairs, params={"count": len(pairs)}))

        def stale(key: str) -> bool:
            entry = cfg.prices.get(key)
            if entry is None or not entry.date:
                return False
            try:
                return (date.today() - date.fromisoformat(entry.date)).days > 30
            except ValueError:
                return False

        view = BudgetView(
            total_eur=round(shop.total_eur, 2),
            limit_eur=shop.limit_eur,
            missing=shop.missing,
            items=[
                BudgetLine(
                    key=i.key, name=i.name, count=i.count, unit_eur=i.unit_eur, stale=stale(i.key)
                )
                for i in shop.items
            ],
        )
        return rules, [[list(a), list(b)] for a, b in pairs], view

    def export(self, params: QuickStartParams, kind: str) -> tuple[str, str]:
        """Returns (filename, text)."""
        res = generate(params, self.cat)
        if kind == "assembly":
            return "assembly.json", dump(res.assembly) + "\n"
        if kind == "mpd":
            return "car.mpd", export_mpd(res.assembly, self.cat)
        if kind == "mjcf":
            return "car.xml", build_mjcf(res.assembly, self.cat, vehicle_spec(res, self.cat))[0]
        raise ValueError(f"unknown export kind {kind!r}")

    def export_assembly(
        self, assembly_json: dict[str, Any], params: QuickStartParams, kind: str
    ) -> tuple[str, str]:
        """Export an edited assembly (spec 0015): assembly JSON, LDraw MPD, MJCF or a BOM CSV."""
        import csv
        import io

        from raceforge.construct.rules import is_lego

        assembly = load_as(Assembly, assembly_json)
        if kind == "assembly":
            return "assembly.json", dump(assembly) + "\n"
        if kind == "mpd":
            return "car.mpd", export_mpd(assembly, self.cat)
        if kind == "mjcf":
            spec = vehicle_spec(generate(params, self.cat), self.cat)
            return "car.xml", build_mjcf(assembly, self.cat, spec)[0]
        if kind == "bom":
            from collections import Counter

            from raceforge.api.construct_settings import load_settings
            from raceforge.construct.derive import placed_parts

            prices = load_settings().prices
            counts = Counter(p.key for p in placed_parts(assembly, self.cat))
            out = io.StringIO()
            w = csv.writer(out)
            w.writerow(
                [
                    "key",
                    "ldraw_id",
                    "name",
                    "category",
                    "count",
                    "lego",
                    "unit_eur",
                    "total_eur",
                    "link",
                ]
            )
            for key, n in sorted(counts.items()):
                e = self.cat.entry(key)
                price = prices.get(key)
                unit = price.eur if price else (0.0 if is_lego(self.cat, key) else None)
                w.writerow(
                    [
                        key,
                        e.ldraw_id or "",
                        self.cat.part(key).name,
                        e.category.value,
                        n,
                        "yes" if is_lego(self.cat, key) else "no",
                        "" if unit is None else f"{unit:.2f}",
                        "" if unit is None else f"{unit * n:.2f}",
                        price.link if price else "",
                    ]
                )
            return "bom.csv", out.getvalue()
        raise ValueError(f"unknown export kind {kind!r}")

    def colours(
        self, assembly: Assembly, chain: list[str], key: str, inst_color: int | None
    ) -> tuple[int, int]:
        """(function colour by submodel role, real LEGO colour) as LDraw codes (spec 0017)."""
        entry = self.cat.entry(key)
        sub = assembly.submodels[assembly.root]
        role = sub.role
        for instance_id in chain[:-1]:
            item = sub.item(instance_id)
            if item is None or item.kind != "submodel":
                break
            sub = assembly.submodels[item.submodel]
            role = sub.role or role
        if entry.ldraw_id is None and entry.device is None:
            function = PRINTED_COLOUR
        else:
            function = ROLE_COLOURS.get(role.value if role else "", COLOURS.get(entry.category, 16))
        real = inst_color if inst_color is not None else entry.color
        return function, real if real is not None else COLOURS.get(entry.category, 16)

    def car_scene(
        self,
        name: str,
        assembly: Assembly,
        part_bodies: dict[str, str] | None = None,
        body_frames: dict[str, tuple[np.ndarray, np.ndarray]] | None = None,
    ) -> CarScene:
        """Parts grouped by body, posed relative to each body's frame (identity if not given)."""
        bodies: dict[str, list[ScenePart]] = {}
        for chain, inst, placement in iter_part_placements(assembly):
            key = self.cat.key_for_hash(inst.part.content_hash)
            entry = self.cat.entry(key)
            body = (part_bodies or {}).get("/".join(chain), "chassis")
            p = np.array(placement.pose.position.as_tuple())
            q = placement.pose.orientation
            rot = _quat_to_mat((q.w, q.x, q.y, q.z))
            if body_frames and body in body_frames:
                bpos, brot = body_frames[body]
                p = brot.T @ (p - bpos)
                rot = brot.T @ rot
            lo, hi = self.cat.bbox(key)
            fc, rc = self.colours(assembly, chain, key, inst.color)
            bodies.setdefault(body, []).append(
                ScenePart(
                    key=key,
                    ldraw_id=entry.ldraw_id,
                    category=entry.category.value,
                    pos=_v3(p),
                    quat=_mat_to_quat(rot),
                    color=fc,
                    real_color=rc,
                    mesh_url=self.mesh_url(key),
                    bbox_lo=_v3(lo),
                    bbox_hi=_v3(hi),
                )
            )
        return CarScene(
            name=name, bodies=[SceneBody(name=b, parts=ps) for b, ps in sorted(bodies.items())]
        )

    # ---- tracks, controllers, replays
    def corridor(self, params: CorridorParams) -> CorridorResponse:
        from raceforge.sim.world import build_world

        cor = generate_corridor(params)
        world = build_world(cor.track, [], self.cat, seed=params.seed)
        from raceforge.sim.mj import mujoco

        data = mujoco.MjData(world.model)
        mujoco.mj_forward(world.model, data)
        return CorridorResponse(
            track=to_jsonable(cor.track),
            primitives=track_primitives(world.model, world.surface_of_geom),
            centreline=[(round(float(x), 4), round(float(y), 4)) for x, y in world.centreline],
        )

    def controllers(self, extra: list[Path] | None = None) -> list[ControllerInfo]:
        out = [
            ControllerInfo(name=p.stem, path=str(p), template=True)
            for p in sorted(TEMPLATES_DIR.glob("*.py"))
        ]
        out += [
            ControllerInfo(name=p.stem, path=str(p), template=False)
            for p in (extra or [])
            if p.is_file()
        ]
        return out

    def replay(self, path: Path) -> ReplaySummary:
        frames = read_frames(path)
        truth = read_truth(path)
        keys = sorted(
            {k for f in frames for k, v in f.channels.items() if isinstance(v, int | float)}
        )
        channels: dict[str, list[float | None]] = {k: [] for k in keys}
        for f in frames:
            for k in keys:
                v = f.channels.get(k)
                channels[k].append(
                    float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None
                )
        t = [f.t.mono_ns / 1e9 for f in frames]
        return ReplaySummary(
            frames=len(frames),
            duration_s=(t[-1] - t[0]) if t else 0.0,
            t=t,
            steering_cmd=[f.cmd.steering_rad for f in frames],
            speed_cmd=[f.cmd.speed_m_s for f in frames],
            speed_meas=[f.meas.speed_m_s for f in frames],
            states=[f.state for f in frames],
            truth_xy=[(round(x, 4), round(y, 4)) for x, y in truth],
            channels=channels,
        )


def track_primitives(model: Any, surfaces: list[str]) -> list[Primitive]:
    out: list[Primitive] = []
    for gid in range(model.ngeom):
        name = model.geom(gid).name
        if not (name.startswith("track/") or name == "floor"):
            continue
        kind = _GEOM_KIND.get(int(model.geom_type[gid]))
        if kind is None:
            continue
        r, g, b, a = (float(v) for v in model.geom_rgba[gid])
        if name == "floor":
            r, g, b, a = 0.82, 0.82, 0.8, 1.0
        size = [float(v) for v in model.geom_size[gid]]
        out.append(
            Primitive(
                kind=kind,
                pos=_v3(model.geom_pos[gid]),
                quat=_q4(model.geom_quat[gid]),
                size=(size[0], size[1], size[2] if kind == "box" else 0.0),
                color=f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}",
                opacity=a,
                surface=surfaces[gid],
            )
        )
    return out


def _v3(v: Any) -> tuple[float, float, float]:
    return (round(float(v[0]), 5), round(float(v[1]), 5), round(float(v[2]), 5))


def _q4(v: Any) -> tuple[float, float, float, float]:
    return (
        round(float(v[0]), 6),
        round(float(v[1]), 6),
        round(float(v[2]), 6),
        round(float(v[3]), 6),
    )


def _quat_to_mat(q: tuple[float, float, float, float]) -> np.ndarray:
    w, x, y, z = q
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ]
    )


def _mat_to_quat(m: np.ndarray) -> tuple[float, float, float, float]:
    from raceforge.core.frames import matrix_to_quat

    q = matrix_to_quat(tuple(tuple(float(v) for v in row) for row in m))  # type: ignore[arg-type]
    return _q4((q.w, q.x, q.y, q.z))


def quickstart_result(params: QuickStartParams, cat: Catalogue) -> QuickStartResult:
    return generate(params, cat)


_ = math
