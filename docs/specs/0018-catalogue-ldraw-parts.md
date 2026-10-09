# Spec: More parts — search the LDraw library, add parts to the team catalogue

- **Status:** approved (owner request 2026-10-09: "weitere Parts einfacher hinzufügen" → more parts in the catalogue)
- **Owner:** Michael Spisak
- **Plan section:** docs/PLAN.md §1 "Part library" (rest of the LDraw library searchable, marked unverified)
- **Depends on:** Spec 0002 (catalogue, LDraw reader)

## Purpose
The curated catalogue has ~40 parts. The team must be able to use any LEGO part from the LDraw library: find
it, add it once with a category, mass and simple connectors, and build with it in the editor right away.

## Scope
- In scope: an index of the LDraw library (`parts/*.dat` headers; aliases/moved/sub-parts skipped; cached as
  `ldraw-index.json` next to the library), `GET /api/v1/parts/ldraw?query&limit`, `POST /api/v1/parts/local`,
  a local catalogue file `catalogue.local.yaml` (`$RACEFORGE_LOCAL_CATALOGUE`, else next to the workspace cache)
  merged by `Catalogue.load()`, `Catalogue.extend()` so the running engine uses a new part at once, Parts tab:
  "search the whole LDraw library" + "add to catalogue" form, team badge on local parts.
- Out of scope: LDCad shadow-library connectors, connector editing per part, team sync of local parts
  (workspace object later), BrickLink masses, removing local parts.

## Interfaces (additive)
- `LDrawPart {ldraw_id, title, category, in_catalogue}`.
- `LocalPartRequest {ldraw_id, name?, category, mass_g, holes?, length_studs?, color?}` → `PartSummary`
  (`origin: "local"`). `holes` creates pin holes like curated beams, `length_studs` end connectors like pins/axles.
  Curated keys cannot be overridden (422); unknown LDraw ids → 422.
- `CatalogueEntry.origin: curated | local` (not part of the part hash). The bounding box of a local part is
  computed from its LDraw geometry and stored as `bbox_mm` (core frame).

## Acceptance criteria (→ tests)
- [ ] AC1: Searching "technic beam 1" lists 18654 as not in the catalogue; adding it with 1 hole stores it in the
  local file, returns `origin: local` with 1 connector, and the editor can add it immediately; a curated key is
  rejected. (Skipped when the LDraw library is not installed.)
