# Spec: Construct editor v1 (part A) — edit the assembly

- **Status:** approved (owner request 2026-10-09: "continue working off the list of features")
- **Owner:** Michael Spisak
- **Plan section:** docs/PLAN.md §1 (Construct), milestone weeks 4–5 ("Editor v1")
- **Depends on:** Spec 0001 (Assembly, Part, connectors), Spec 0002 (quick-start, derive)

## Purpose
Turn the quick-start car into an editable assembly: the 3D modeler can move, turn, add and delete parts,
snap them onto compatible connectors and save the result as a team version, with derived data (mass, CoG,
wheelbase, warnings) updated after every edit. The Assembly stays the single source of truth.

## Scope
- In scope (part A): engine edit operations (`raceforge.construct.editor`), `POST /api/v1/assembly/edit`,
  `POST /api/v1/workspace/save/assembly`, a parts editor in the Construct tab (click/list selection, keyboard
  moves on the stud grid with 1-LDU fine steps, 90° turns, delete, add from the catalogue, quick snap,
  undo/redo, live derived data and validation problems, save as version).
- Part C (added 2026-10-09): multi-select (Shift+click), group move/turn (about the selection's pivot on the
  LDU grid)/delete, duplicate (Ctrl+D, with the connections between the copied parts), mirror copy (M, across the
  XZ plane: position y → −y, rotation M·R·M — exact for parts symmetric to their own mirror plane).
- Out of scope (later parts): gizmo dragging, submodel creation/linking UI,
  precision snap (pick A then B), rule checker and budget panel, overlap highlighting, gears/kinematics,
  custom parts.

## Interfaces (additive)
- `POST /api/v1/assembly/edit` — `AssemblyEditRequest {assembly, quickstart (drives/steering for derived
  data), op: EditOp {kind: none|move|rotate|delete|add|snap|duplicate|mirror, path, paths (selection), delta (m, world), axis, turns, key,
  position}, snap: bool}` → `AssemblyEditResponse {assembly, parts: [EditorPartView{path, key, name,
  ldraw_id, category, color, pos, quat, bbox, mirrored, linked, connectors (world)}], derived, warnings,
  problems, selected, snapped?}`. Unknown parts/paths → 422.
- `POST /api/v1/assembly/export/{assembly|mpd|mjcf|bom}` (`AssemblyExportRequest {assembly, quickstart}`) → text
  file of the edited car (BOM CSV with counts, LEGO flag and prices from the Construct settings).
- `POST /api/v1/workspace/save/assembly {slug, assembly, message}` → `LocalVersion` (validated first).
- `SimStart.assembly: dict | null`: race the edited car as "ego" (bodies and joints come from its connection
  graph and joint roles; drives and steering motor from `SimStart.quickstart`). The Simulate tab offers
  "car from the Construct editor" while the editor has a valid car open (session only).

## Behaviour
- Parts are addressed by their instance chain (`path`). Moves and turns are given in world axes and converted
  into the parent frame, so they work in nested and mirrored submodels; editing inside a linked submodel
  changes every copy (the UI says so).
- Quick snap (after a move/add, or on demand): a connector of the part engages a compatible connector of
  another part when their axes are parallel (|cos| ≥ 0.98), the axis lines are ≤ 6 mm apart and they overlap
  along the axis; the part is moved sideways onto the axis and the connection is recorded (once).
- Deleting a part removes its connections and re-indexes joint roles.
- Undo/redo keep up to 100 assemblies in the UI.

## Acceptance criteria (→ tests, critical paths)
- [ ] AC1: Move (world delta), rotate (90°), delete (connections removed, no validation problems), add
  (catalogue part, new id) work through the API on the quick-start car.
- [ ] AC2: A pin dropped 3 mm beside a free beam's hole snaps onto the hole axis and records one connection.
- [ ] AC2b: A car edited in the editor (a part deleted) drives and finishes a race in the sim.
- [ ] AC2c: A group of two parts turned 90° about z keeps their distance ((x, y) → (−y, x)); duplicate adds the
  copies at the offset; a mirror copy has y → −y; deleting the copies restores the part count without problems.
- [ ] AC3: The editor in the app selects, moves, undoes and deletes parts (manual check by the owner).
