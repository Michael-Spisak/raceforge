"""Trace a controller run around the first wall contact (dev helper)."""

import math
import sys
from pathlib import Path

from eval_controller import SENSORS

from raceforge.construct.quickstart import QuickStartParams, generate, vehicle_spec
from raceforge.control.controller import ControllerHost, load_controller
from raceforge.parts.catalogue import Catalogue
from raceforge.sim.engine import Simulation
from raceforge.sim.simio import SimIO
from raceforge.sim.world import CarEntry, build_world
from raceforge.track.procedural import CorridorParams, generate_corridor

path, seed = Path(sys.argv[1]), int(sys.argv[2])
loop = (sys.argv[3] if len(sys.argv) > 3 else "loop") == "loop"
cat = Catalogue.load()
res = generate(QuickStartParams(drive_gears="20-28", sensors=SENSORS), cat)
cor = generate_corridor(CorridorParams(seed=seed, loop=loop, length_m=40))
sim = Simulation(
    build_world(
        cor.track, [CarEntry("ego", res.assembly, vehicle_spec(res, cat), 0)], cat, seed=seed
    ),
    seed=seed,
)
io = SimIO(sim, "ego")
host = ControllerHost(load_controller(path), io, 0.005)
host.start()
hist = []
for _ in range(15000):
    host.step()
    sim.step()
    tr = sim.truth("ego")
    obs_us = {n: (v.value if v else None) for n, v in sim.readings("ego").ultrasonic_m.items()}
    hist.append(
        (
            round(sim.t, 2),
            round(tr.x, 2),
            round(tr.y, 2),
            round(math.degrees(tr.yaw)),
            host.controller.state,
            obs_us,
            round(io.last_cmd.steering_rad, 2),
        )
    )
    if sim.events:
        e = sim.events[0]
        print("EVENT", e)
        for h in hist[-60::4]:
            print(h)
        near = [
            (o.id, round(o.pose.position.x, 1), round(o.pose.position.y, 1))
            for o in cor.track.objects
            if math.hypot(o.pose.position.x - tr.x, o.pose.position.y - tr.y) < 1.5
        ]
        print("near objects", near, sim.world.door_open)
        break
