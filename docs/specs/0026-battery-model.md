# Spec 0026: Battery and power model in the simulator

- **Status:** approved (owner request 2026-10-09: "next item from the plan")
- **Owner:** Michael Spisak
- **Plan section:** docs/PLAN.md §5e (battery and power model), milestone weeks 6–7 ("battery model")
- **Depends on:** Spec 0003 (sim engine), `core.devices.BatteryParams`

## Purpose
A real race lasts minutes with a nearly empty or sagging pack. The sim models the motor battery so
controllers and the benchmark see lower top speed under load, remaining charge and brownouts.

## Scope
- In scope: `raceforge.sim.battery.BatteryModel` (open-circuit voltage curve from state of charge, internal
  resistance, motor current from duty/speed), integration in `Simulation` (opt-in), `Readings.battery_v` and
  `Simulation.battery_state()`, events `low_battery` (< 10 % charge) and `brownout` (terminal voltage below
  the cut-off: drive motors cut until it recovers), `SimStart.battery: bool`.
- Out of scope: fitting the curve from real logs (calibration spec), board supply brownout warning in the
  construct editor, Live-tab battery plots.

## Interfaces (additive)
- `Simulation(world, seed, ..., battery: BatteryParams | None = None, battery_soc: float = 1.0)`; `None` keeps the previous behaviour
  exactly (fixed 7.4 V, no sag).
- `Simulation.battery_state(car) -> BatteryState {soc, volts, amps, browned_out}` (None without battery).
- `SimStart.battery: bool = False` → default EV3 pack (7.4 V, 15 Wh, 0.25 Ω).
- `Readings.battery_v` is the terminal voltage when the model is on.

## Behaviour
- OCV(soc) is a 2S Li-ion curve (8.4 V full … 6.0 V empty) scaled by `nominal_v / 7.4`.
- Motor: k_e = nominal_v / no_load_speed, R_m = nominal_v · k_e / stall_torque; current i = (duty·V − k_e·ω)/R_m,
  no regeneration (negative current counts as 0); total over the drive motors.
- Terminal V = OCV − I·R_int; the motor duty is scaled by min(1, V / motor nominal_v), so top speed drops as
  the pack sags or empties. Energy used integrates V·I over time.
- Brownout: V < 0.8 · nominal_v → motors off (events once per entry) until V > 0.85 · nominal_v.

## Acceptance criteria (→ tests)
- [ ] AC1: Without battery the sim result is unchanged; with a full pack top speed is lower than without.
- [ ] AC2: A nearly empty pack (state of charge preset) is slower than a full one and ends in brownout when empty.
- [ ] AC3: SOC falls monotonically while driving, stays constant at standstill; `battery_v` follows the model.
- [ ] AC4: `SimStart.battery` works over the sim WebSocket.
