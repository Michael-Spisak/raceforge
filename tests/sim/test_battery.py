"""Spec 0026: battery model — sag, charge, brownout."""

from raceforge.core.devices import BatteryParams
from raceforge.parts.catalogue import Catalogue
from raceforge.sim.battery import EV3_PACK
from raceforge.sim.engine import CarCommand, Simulation
from raceforge.sim.world import build_world
from raceforge.track.procedural import CorridorParams, generate_corridor
from tests.sim.helpers import car


def make(cat: Catalogue, battery: BatteryParams | None, soc: float = 1.0) -> Simulation:
    track = generate_corridor(CorridorParams(seed=1, length_m=60)).track
    world = build_world(track, [car(cat)], cat, seed=0)
    return Simulation(world, seed=0, battery=battery, battery_soc=soc)


def drive(sim: Simulation, seconds: float, speed: float = 10.0) -> float:
    sim.command("ego", CarCommand(speed_m_s=speed))
    for _ in range(int(seconds / sim.control_dt)):
        sim.step()
    return sim.truth("ego").speed_m_s


def test_pack_lowers_top_speed_and_voltage_follows_model(cat: Catalogue) -> None:
    free = drive(make(cat, None), 4.0)
    sim = make(cat, EV3_PACK)
    with_pack = drive(sim, 4.0)
    assert 0 < with_pack < free
    st = sim.battery_state("ego")
    assert st is not None and st.soc < 1.0 and st.volts < 8.4
    assert sim.readings("ego").battery_v == st.volts


def test_charge_falls_while_driving_and_holds_at_standstill(cat: Catalogue) -> None:
    sim = make(cat, EV3_PACK)
    socs = []
    for _ in range(3):
        drive(sim, 2.0)
        state = sim.battery_state("ego")
        assert state is not None
        socs.append(state.soc)
    assert socs[0] > socs[1] > socs[2]
    sim.command("ego", CarCommand(speed_m_s=0.0))
    for _ in range(100):
        sim.step()
    held = sim.battery_state("ego")
    assert held is not None
    for _ in range(100):
        sim.step()
    after = sim.battery_state("ego")
    assert after is not None and held.soc - after.soc < 1e-4


def test_empty_pack_is_slower_and_browns_out(cat: Catalogue) -> None:
    full = drive(make(cat, EV3_PACK, 1.0), 4.0)
    low = drive(make(cat, EV3_PACK, 0.15), 4.0)
    assert low < full
    sim = make(cat, EV3_PACK, 0.005)
    drive(sim, 3.0)
    kinds = [e.kind for e in sim.events]
    assert "low_battery" in kinds and "brownout" in kinds
    state = sim.battery_state("ego")
    assert state is not None and state.browned_out
    assert abs(sim.truth("ego").speed_m_s) < 0.2  # motors cut
