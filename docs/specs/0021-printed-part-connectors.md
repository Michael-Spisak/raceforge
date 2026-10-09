# Spec: Connectors on 3D-printed parts

- **Status:** approved (owner request 2026-10-09: "arbeite weiter die Features ab"; plan §1 "Define connectors by
  clicking a hole or face")
- **Owner:** Michael Spisak
- **Depends on:** Spec 0019 (printed parts), Spec 0015 (snap, attach)

## Purpose
Printed parts must snap to LEGO: the modeler clicks on the part's mesh, chooses a connector type, and the editor's
snapping and docking then work with it like with any LEGO part.

## Scope
- In scope: `GET|PUT /api/v1/parts/{key}/connectors` (`ConnectorDef {id, type, pos, axis}` in the part's core
  frame, metres; PUT only for 3D-printed parts), connector editor in the Parts tab (click on the mesh → connector at
  the hit point with the face normal as axis; flip axis; delete; save).
- Out of scope: snapping the click to hole centres (the click point is used, rounded to 0.1 mm), connector
  length, strength hints.

## Behaviour
- Genders follow the type (holes/anti-stud/screw hole: female; pin/axle/stud: male; fixed mount: neutral).
- Editing connectors changes the part's content hash. The old hash is kept in `previous_hashes`, so assemblies
  saved before still resolve to the part (and get the new connectors); an unchanged save does nothing.

## Acceptance criteria (→ tests)
- [ ] AC1: Setting a pin hole on a printed box returns it in the part frame (axis normalised); an assembly saved
  before the change still evaluates without problems; a pin docks onto the new hole; LEGO parts are refused (422).
