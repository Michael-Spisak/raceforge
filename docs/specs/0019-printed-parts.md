# Spec: Import 3D-printed parts

- **Status:** approved (owner request 2026-10-09: "eigene 3D-Druckteile")
- **Owner:** Michael Spisak
- **Plan section:** docs/PLAN.md §1 "Custom 3D-printed parts", milestone weeks 6–7
- **Related ADRs:** ADR-0029 (trimesh, lxml, networkx)
- **Depends on:** Spec 0018 (local catalogue), Spec 0016 (budget)

## Purpose
The team prints its own parts (sensor mounts, LiDAR mast, battery holder). They must be importable from the
modeling tools, weigh and cost the right amount, show their real shape in the editor and simulate with the car.

## Scope
- In scope: STL/OBJ/PLY/3MF import (units mm/cm/m/in, Y- or Z-up), mesh normalised to metres, core frame,
  centred on its bounding box and stored as binary STL next to the local catalogue (`printed-parts/`);
  catalogue entry (category `printed`, `PrintedSpec {mesh, material, infill_pct, volume_cm3}`, colour orange);
  mass estimate, print cost in the budget (`filament_eur_per_kg`, default 25 €/kg), mesh rendering in Parts,
  editor and Simulate; import panel in the Parts tab with a check (size, volume, mass, cost, watertightness).
- Out of scope (next steps): clicking connectors on the mesh (until then printed parts are placed freely and are
  rigid with the chassis in the sim), watch folder re-import, versioned parts in the team workspace, STEP,
  strength hints, convex decomposition (collision uses the bounding box).

## Interfaces (additive)
- `POST /api/v1/parts/printed/preview` and `POST /api/v1/parts/printed` with `PrintedImportRequest {path, name,
  units, up, material, infill_pct, measured_mass_g?}` → `PrintedPreview` / `PartSummary`.
- `GET /api/v1/parts/printed/{key}/mesh` → binary STL.
- `mesh_url` on `PartSummary`, `ScenePart`, `EditorPartView`; `ConstructSettings.filament_eur_per_kg`.

## Behaviour
- Mass = volume × density (PLA 1.24, PETG 1.27, TPU 1.21 g/cm³) × (0.25 + 0.75 × infill); a measured mass wins.
  Open meshes use the convex hull volume (warned: too high).
- Keys are `printed-<name>`; a different part never replaces an existing key (`-2`, …); an identical re-import
  returns the existing part.
- Printed parts are not LEGO for the rule checker (not allowed in wheels/steering).

## Acceptance criteria (→ tests)
- [ ] AC1: A 40×20×10 mm box (PETG, 50 %) previews 8 cm³, ≈ 6.35 g, a cost > 0; importing gives
  `printed-sensor-mount` with a mesh; re-import reuses it, other settings get `-2`; the editor shows it orange
  with its mesh URL, the budget prices it, MJCF export works; a `.step` file is rejected.
