"""Import of the team's 3D-printed parts (spec 0019): mesh → catalogue entry.

Meshes are read with trimesh (ADR-0029), scaled to metres, turned to the core frame (Z up) and
centred on their bounding box. Mass = volume * density * effective fill, where the effective fill
adds the solid walls/top/bottom (``SHELL_FRACTION``) to the infill; a measured mass overrides it.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import trimesh

Units = Literal["mm", "cm", "m", "in"]
Up = Literal["z", "y"]
Material = Literal["PLA", "PETG", "TPU"]

SCALE = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254}
DENSITY_G_CM3: dict[str, float] = {"PLA": 1.24, "PETG": 1.27, "TPU": 1.21}
SHELL_FRACTION = 0.25  # share of the volume printed solid (walls, top, bottom) at typical settings
FORMATS = (".stl", ".obj", ".ply", ".3mf")


class MeshImportError(ValueError):
    pass


@dataclass(frozen=True)
class ImportedMesh:
    mesh: trimesh.Trimesh  # metres, core frame, centred on its bounding box
    volume_cm3: float
    watertight: bool

    @property
    def size_mm(self) -> tuple[float, float, float]:
        e = self.mesh.extents * 1000
        return (float(e[0]), float(e[1]), float(e[2]))


def load_mesh(path: Path, units: Units = "mm", up: Up = "z") -> ImportedMesh:
    if path.suffix.lower() not in FORMATS:
        raise MeshImportError(f"{path.name}: use STL, OBJ, PLY or 3MF")
    if not path.is_file():
        raise MeshImportError(f"{path}: file not found")
    try:
        loaded = trimesh.load(path, force="mesh")
    except Exception as e:  # trimesh raises many types for broken files
        raise MeshImportError(f"{path.name}: cannot read the mesh ({e})") from e
    if not isinstance(loaded, trimesh.Trimesh) or len(loaded.faces) == 0:
        raise MeshImportError(f"{path.name}: no triangles in the file")
    mesh = loaded.copy()
    mesh.apply_scale(SCALE[units])
    if up == "y":  # Y-up tools (e.g. some OBJ exports): turn so +Y becomes +Z
        mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    lo, hi = mesh.bounds
    mesh.apply_translation(-(lo + hi) / 2)
    watertight = bool(mesh.is_watertight)
    volume = float(abs(mesh.volume)) if watertight else float(mesh.convex_hull.volume)
    return ImportedMesh(mesh, volume * 1e6, watertight)


def estimate_mass_g(volume_cm3: float, material: Material, infill_pct: float) -> float:
    fill = SHELL_FRACTION + (1 - SHELL_FRACTION) * infill_pct / 100
    return volume_cm3 * DENSITY_G_CM3[material] * fill


def print_cost_eur(
    volume_cm3: float, material: Material, infill_pct: float, eur_per_kg: float
) -> float:
    return estimate_mass_g(volume_cm3, material, infill_pct) / 1000 * eur_per_kg


def slug_key(name: str) -> str:
    core = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "part"
    return f"printed-{core}"[:60]
