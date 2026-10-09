# ADR-0029: trimesh (+ lxml, networkx) for 3D-printed part import

- **Status:** accepted
- **Date:** 2026-10-09

## Context
Spec 0019 imports the team's 3D-printed parts from Blender, Tinkercad, FreeCAD, SolidWorks and Inventor exports
(STL, OBJ, PLY, 3MF), computes volume and bounding box for mass, cost and collision, and serves the mesh to the
editor. docs/PLAN.md §Tech stack already names trimesh for custom-part import.

## Decision
- **trimesh** (MIT): mesh loading, volume, watertightness, transforms, STL export.
- **lxml** (BSD-3) and **networkx** (BSD-3): needed by trimesh's 3MF reader.

All compatible with GPL-3.0. STEP (OCP/CadQuery) stays out for now: much larger (OpenCascade); teams export STL/3MF
from their CAD tools.

## Consequences
Pure-Python/wheel dependencies, no system libraries. STEP import needs a later ADR.
