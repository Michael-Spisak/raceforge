"""Curated parts catalogue: masses, connectors, devices and bounding boxes (spec 0002)."""

import hashlib
import os
import uuid
from enum import StrEnum
from functools import cached_property
from importlib import resources
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt

from raceforge.core.connectors import ConnectorType, Gender
from raceforge.core.devices import Device
from raceforge.core.frames import LDU_M, ldraw_point_to_core
from raceforge.core.io import content_hash
from raceforge.core.parts import Connector, Part, PartSource
from raceforge.core.primitives import Pose, UnitVec3, Vec3, VersionRef

type Vec = tuple[float, float, float]

DATA = resources.files("raceforge.parts").joinpath("data")


class Category(StrEnum):
    BEAM = "beam"
    AXLE = "axle"
    PIN = "pin"
    BUSH = "bush"
    AXLE_JOINER = "axle_joiner"
    GEAR = "gear"
    DIFFERENTIAL = "differential"
    STEERING_ARM = "steering_arm"
    STEERING_LINK = "steering_link"
    CV_JOINT = "cv_joint"
    WHEEL_RIM = "wheel_rim"
    TYRE = "tyre"
    EV3_BRICK = "ev3_brick"
    MOTOR = "motor"
    SENSOR = "sensor"
    BOARD = "board"
    BATTERY = "battery"
    PRINTED = "printed"  # team 3D-printed part (spec 0019)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConnectorSpec(_Model):
    id: str
    type: ConnectorType
    pos_ldu: tuple[float, float, float]
    axis_ld: tuple[float, float, float] = (0.0, 0.0, 1.0)
    gender: Gender


class WheelSpec(_Model):
    radius_mm: PositiveFloat
    width_mm: PositiveFloat


class PrintedSpec(_Model):
    """3D-printed part (spec 0019): mesh file next to the local catalogue, print settings."""

    mesh: str  # file name in the printed-parts folder (binary STL, metres, core frame)
    material: Literal["PLA", "PETG", "TPU"] = "PLA"
    infill_pct: float = Field(default=20.0, ge=0, le=100)
    volume_cm3: float = Field(ge=0)


class CatalogueEntry(_Model):
    key: str
    ldraw_id: str | None = None
    name: str
    category: Category
    mass_g: PositiveFloat
    mass_source: str = "approx. BrickLink-style value, unverified"
    holes: PositiveInt | None = None
    length_studs: PositiveInt | None = None
    teeth: PositiveInt | None = None
    wheel: WheelSpec | None = None
    connectors: list[ConnectorSpec] = Field(default_factory=list[ConnectorSpec])
    color: int | None = None  # usual LDraw colour of this part in the kit (approximate)
    device: Device | None = None
    sense_axis_ld: tuple[float, float, float] | None = None
    bbox_mm: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None
    # local: added by the team (LDraw part, spec 0018, or 3D-printed part, spec 0019)
    origin: Literal["curated", "local"] = "curated"
    printed: PrintedSpec | None = None
    # Content hashes of earlier versions of this local part (e.g. before its connectors were
    # edited): assemblies that reference them keep resolving to this entry (spec 0021).
    previous_hashes: list[str] = Field(default_factory=list[str])

    def all_connectors(self) -> list[ConnectorSpec]:
        out: list[ConnectorSpec] = []
        if self.holes:
            first = -(self.holes - 1) * 10.0
            for n in range(self.holes):
                out.append(
                    ConnectorSpec(
                        id=f"h{n + 1}",
                        type=ConnectorType.PIN_HOLE,
                        pos_ldu=(0.0, 0.0, first + 20.0 * n),
                        axis_ld=(1.0, 0.0, 0.0),
                        gender=Gender.FEMALE,
                    )
                )
        if self.length_studs and self.category in (Category.AXLE, Category.PIN):
            half = self.length_studs * 10.0
            kind = ConnectorType.AXLE if self.category is Category.AXLE else ConnectorType.PIN
            for cid, x in (("e1", -half + 10.0), ("e2", half - 10.0)):
                out.append(
                    ConnectorSpec(
                        id=cid,
                        type=kind,
                        pos_ldu=(x, 0.0, 0.0),
                        axis_ld=(1.0, 0.0, 0.0),
                        gender=Gender.MALE,
                    )
                )
            if self.category is Category.AXLE:
                out.append(
                    ConnectorSpec(
                        id="mid",
                        type=kind,
                        pos_ldu=(0.0, 0.0, 0.0),
                        axis_ld=(1.0, 0.0, 0.0),
                        gender=Gender.MALE,
                    )
                )
        return out + list(self.connectors)


def _to_core_dir(v: Vec) -> Vec:
    x, y, z = ldraw_point_to_core(v)
    return (x / LDU_M, y / LDU_M, z / LDU_M)


def deterministic_object_id(key: str) -> str:
    """UUIDv7-shaped id derived from a catalogue key (stable across machines and runs)."""
    digest = int.from_bytes(
        hashlib.sha256(f"raceforge.catalogue:{key}".encode()).digest()[:16], "big"
    )
    value = (digest & ~(0xF << 76) & ~(0b11 << 62)) | 0x7 << 76 | 0b10 << 62
    value &= ~(((1 << 48) - 1) << 80)  # timestamp 0 marks catalogue ids
    return str(uuid.UUID(int=value))


def local_catalogue_path() -> Path:
    """Team additions to the catalogue: $RACEFORGE_LOCAL_CATALOGUE, else next to the workspace
    cache (``$RACEFORGE_WORKSPACE_DIR/..`` or ``~/.cache/raceforge``)."""
    env = os.environ.get("RACEFORGE_LOCAL_CATALOGUE")
    if env:
        return Path(env)
    ws = os.environ.get("RACEFORGE_WORKSPACE_DIR")
    base = Path(ws).parent if ws else Path.home() / ".cache" / "raceforge"
    return base / "catalogue.local.yaml"


def core_bbox_mm(lo_ldu: Vec, hi_ldu: Vec) -> tuple[Vec, Vec]:
    """An LDraw-frame box (LDU) as the core-frame box in millimetres (for ``bbox_mm``)."""
    corners = [
        ldraw_point_to_core((x, y, z))
        for x in (lo_ldu[0], hi_ldu[0])
        for y in (lo_ldu[1], hi_ldu[1])
        for z in (lo_ldu[2], hi_ldu[2])
    ]
    xs, ys, zs = zip(*corners, strict=True)
    lo = (round(min(xs) * 1000, 3), round(min(ys) * 1000, 3), round(min(zs) * 1000, 3))
    hi = (round(max(xs) * 1000, 3), round(max(ys) * 1000, 3), round(max(zs) * 1000, 3))
    return lo, hi


def printed_dir() -> Path:
    """Meshes of the team's 3D-printed parts, next to the local catalogue file."""
    return local_catalogue_path().parent / "printed-parts"


def load_local_entries(path: Path) -> list[CatalogueEntry]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [
        CatalogueEntry.model_validate({**cast(dict[str, Any], item), "origin": "local"})
        for item in cast(list[Any], raw)
    ]


def save_local_entry(entry: CatalogueEntry, path: Path | None = None) -> None:
    """Add or replace ``entry`` in the local catalogue file (written atomically)."""
    p = path or local_catalogue_path()
    entries = [e for e in (load_local_entries(p) if p.is_file() else []) if e.key != entry.key]
    entries.append(entry)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    data = [e.model_dump(mode="json", exclude_defaults=True, exclude={"origin"}) for e in entries]
    tmp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    tmp.replace(p)


class Catalogue:
    """Loaded catalogue with core ``Part`` objects, version refs and core-frame bounding boxes."""

    def __init__(
        self, entries: list[CatalogueEntry], bboxes_ldu: dict[str, tuple[Vec, Vec]]
    ) -> None:
        self.entries = {e.key: e for e in entries}
        self._bboxes_ldu = bboxes_ldu

    @classmethod
    def load(
        cls, directory: Path | None = None, local: Path | Literal[False] | None = None
    ) -> "Catalogue":
        """The curated catalogue plus the team's local additions (``local``: file, default
        :func:`local_catalogue_path`; ``False``: curated only)."""
        cat = cls._load_curated(directory)
        local_path = local_catalogue_path() if local is None else local
        if local_path is not False and local_path.is_file():
            for entry in load_local_entries(local_path):
                if entry.key not in cat.entries:
                    cat.entries[entry.key] = entry
        return cat

    def key_for_entry(self, entry: CatalogueEntry) -> str | None:
        """Key of an existing entry whose part has the same content (identical re-import)."""
        return self._by_hash.get(content_hash(self._build_part(entry)))

    def extend(self, entry: CatalogueEntry) -> None:
        """Add or replace an entry at runtime (spec 0018); cached parts/refs are rebuilt."""
        self.entries[entry.key] = entry
        for name in ("_parts", "_refs", "_by_hash"):
            self.__dict__.pop(name, None)

    @classmethod
    def _load_curated(cls, directory: Path | None = None) -> "Catalogue":
        base: Any = directory if directory is not None else DATA
        raw = yaml.safe_load(base.joinpath("catalogue.yaml").read_text(encoding="utf-8"))
        entries = [CatalogueEntry.model_validate(item) for item in cast(list[Any], raw)]
        bbox_file = base.joinpath("bboxes.yaml")
        bboxes: dict[str, tuple[Vec, Vec]] = {}
        if bbox_file.is_file():
            data = cast(
                dict[str, list[list[float]]],
                yaml.safe_load(bbox_file.read_text(encoding="utf-8")) or {},
            )
            for key, (lo, hi) in data.items():
                bboxes[key] = ((lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2]))
        return cls(entries, bboxes)

    def entry(self, key: str) -> CatalogueEntry:
        try:
            return self.entries[key]
        except KeyError:
            raise KeyError(f"part {key!r} is not in the catalogue") from None

    def bbox(self, key: str) -> tuple[Vec, Vec]:
        """Axis-aligned bounding box in the part's core frame, metres."""
        e = self.entry(key)
        if e.bbox_mm is not None:
            lo, hi = e.bbox_mm
            return (tuple(v / 1000 for v in lo), tuple(v / 1000 for v in hi))  # type: ignore[return-value]
        if e.ldraw_id is None or e.ldraw_id not in self._bboxes_ldu:
            raise KeyError(f"no bounding box for {key!r}; run `raceforge parts build-catalogue`")
        lo, hi = self._bboxes_ldu[e.ldraw_id]
        corners = [
            ldraw_point_to_core((x, y, z))
            for x in (lo[0], hi[0])
            for y in (lo[1], hi[1])
            for z in (lo[2], hi[2])
        ]
        xs, ys, zs = zip(*corners, strict=True)
        return ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))

    def part(self, key: str) -> Part:
        return self._parts[key]

    def ref(self, key: str) -> VersionRef:
        return self._refs[key]

    def key_for_hash(self, content_hash_hex: str) -> str:
        return self._by_hash[content_hash_hex]

    def parts_by_hash(self) -> dict[str, Part]:
        out = {self._refs[k].content_hash: p for k, p in self._parts.items()}
        for key, e in self.entries.items():
            for old in e.previous_hashes:
                out.setdefault(old, self._parts[key])
        return out

    @cached_property
    def _parts(self) -> dict[str, Part]:
        return {key: self._build_part(e) for key, e in self.entries.items()}

    @cached_property
    def _refs(self) -> dict[str, VersionRef]:
        return {
            key: VersionRef(
                object_id=deterministic_object_id(key), semver="1.0.0", content_hash=content_hash(p)
            )
            for key, p in self._parts.items()
        }

    @cached_property
    def _by_hash(self) -> dict[str, str]:
        out = {r.content_hash: k for k, r in self._refs.items()}
        for key, e in self.entries.items():
            for old in e.previous_hashes:
                out.setdefault(old, key)
        return out

    def _build_part(self, e: CatalogueEntry) -> Part:
        connectors = [
            Connector(
                id=c.id,
                type=c.type,
                pose=Pose(
                    position=Vec3(**dict(zip("xyz", ldraw_point_to_core(c.pos_ldu), strict=True)))
                ),
                axis=UnitVec3(**dict(zip("xyz", _normalise(_to_core_dir(c.axis_ld)), strict=True))),
                gender=c.gender,
            )
            for c in e.all_connectors()
        ]
        if e.printed is not None:
            source = PartSource.PRINTED
        elif e.device is not None:
            source = PartSource.DEVICE
        elif e.ldraw_id is not None:
            source = PartSource.LEGO_LDRAW
        else:
            source = PartSource.OTHER
        return Part(
            source=source,
            ldraw_id=e.ldraw_id,
            name=e.name,
            mass_kg=e.mass_g / 1000,
            connectors=connectors,
            device=e.device,
            verified=False,
        )


def _normalise(v: Vec) -> Vec:
    n = (v[0] ** 2 + v[1] ** 2 + v[2] ** 2) ** 0.5
    return (v[0] / n, v[1] / n, v[2] / n)


def build_bboxes(library_root: Path, catalogue_dir: Path) -> Path:
    """Compute LDraw bounding boxes for all catalogue parts and write ``bboxes.yaml``."""
    from raceforge.parts.ldraw import LDrawLibrary

    lib = LDrawLibrary(library_root)
    cat = Catalogue.load(catalogue_dir)
    out: dict[str, list[list[float]]] = {}
    for e in cat.entries.values():
        if e.ldraw_id is None:
            continue
        box = lib.bbox(f"{e.ldraw_id}.dat")
        if box is None:
            raise FileNotFoundError(f"LDraw part {e.ldraw_id}.dat not found in {library_root}")
        out[e.ldraw_id] = [[round(v, 3) for v in box.lo], [round(v, 3) for v in box.hi]]
    path = catalogue_dir / "bboxes.yaml"
    header = "# Generated by `raceforge parts build-catalogue` from LDraw (LDU, LDraw frame).\n"
    path.write_text(
        header + yaml.safe_dump(dict(sorted(out.items())), default_flow_style=None),
        encoding="utf-8",
    )
    return path
