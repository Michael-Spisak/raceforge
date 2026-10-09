# Spec: Rule checker, budget and overlap check in the Construct editor

- **Status:** approved (owner request 2026-10-09: "arbeite weiter die Features ab")
- **Owner:** Michael Spisak
- **Plan section:** docs/PLAN.md §1 "Rule checker", "Prices", "Overlaps"
- **Depends on:** Spec 0015 (editor), Spec 0002 (catalogue, derive)

## Purpose
Show live, while the car is edited, whether it follows the race rules, what it costs and where parts
interpenetrate, so problems are found before the car is built.

## Scope
- In scope: `raceforge.construct.rules` (rules, budget, overlaps), results in `AssemblyEditResponse`
  (`rules`, `overlaps`, `budget`), local settings (prices, limits, budget) with `GET|PUT /api/v1/construct/settings`,
  a Rules panel in the editor (click a failed rule to select the part; red highlight; price and limit inputs).
- Out of scope: inventory ("our EV3 box") and kit import, price reminders by notification, radio check of the
  race config (car runtime race check), printed-part cost from volume, team sync of prices.

## Rules (ids; UI texts DE/EN)
- `wheels_steering_lego`: tyres, rims, steering arms and links are LEGO (catalogue parts with an LDraw id).
- `steering_submodel_lego`: every part inside a submodel with role `steering` is LEGO, except motors.
- `ev3_drives`: an EV3 brick and at least one EV3 motor are in the car.
- `budget`: Σ count × price ≤ budget (default 200 €); LEGO parts default to 0 € (school kit), other parts need a
  price — missing prices fail the rule and are listed. Prices older than 30 days are marked.
- `max_length|max_width|max_height|max_mass`: world bounding box / mass against optional limits (`ok: null` =
  no limit set).
- `overlaps`: pairs of parts whose oriented boxes (shrunk by 0.8 mm) interpenetrate although they are not
  directly connected; highlighted in red on request.

## Acceptance criteria (→ tests, critical paths)
- [ ] AC1: The quick-start car passes the LEGO rules and the EV3 rule; without prices its budget fails with
  `rpi5, powerbank` missing; with prices the total is their sum; a mass limit below the mass fails.
- [ ] AC2: A non-LEGO part placed in the steering submodel fails `steering_submodel_lego` with its path.
- [ ] AC3: Two unconnected beams placed into each other are reported as one new overlap pair.

## Notes
The quick-start car currently shows ~79 overlap pairs: its layout is approximate (e.g. motor inside the power
bank), see the separate quick-start geometry task.
