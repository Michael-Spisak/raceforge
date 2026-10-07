# Writing a controller (for Java / C# / JavaScript developers)

A **controller** decides, 50 times per second, how to steer and how fast to drive. The same Python file
runs in the RaceForge simulator and on the real car.

## 1. Python in 5 minutes (compared to Java/C#)

| Java / C# | Python |
|---|---|
| `int x = 3;` | `x: int = 3` (type hints are checked by pyright, not at runtime) |
| `final record Point(double x, double y)` | `@dataclass(frozen=True) class Point: x: float; y: float` |
| `interface RobotIO { … }` | `class RobotIO(Protocol): …` (structural typing) |
| `enum State { DRIVE, STOP }` | `class State(Enum): DRIVE = auto(); STOP = auto()` |
| `null` | `None` — check with `if x is None:` |
| `"a=" + a` / `$"a={a}"` | `f"a={a}"` |
| `List<Double>` / `Map<String, Double>` | `list[float]` / `dict[str, float]` |
| `for (int i = 0; i < n; i++)` | `for i in range(n):` |
| JUnit / xUnit | `pytest` — functions named `test_*` with plain `assert` |

Indentation (4 spaces) replaces `{ }`. Units are always SI: metres, seconds, radians.

```python
from dataclasses import dataclass
from enum import Enum, auto


@dataclass(frozen=True)
class Point:
    x: float
    y: float


class State(Enum):
    DRIVE = auto()
    STOP = auto()


p = Point(1.0, 2.0)
distance: float | None = None
assert p.x == 1.0 and distance is None and State.DRIVE.name == "DRIVE"
assert f"x={p.x:.1f}" == "x=1.0"
```

## 2. The smallest controller

```python
import math

from raceforge.control import Command, Controller, ControllerParams, Observation, Tunable


class MyParams(ControllerParams):
    speed_m_s: float = Tunable(0.3, 0.1, 1.0, description="cruise speed")


class MyController(Controller[MyParams]):
    Params = MyParams

    def step(self, obs: Observation) -> Command:
        front = obs.ultrasonic_m.get("front")
        if front is not None and front < 0.5:  # wall ahead -> turn left
            return Command(steering_rad=math.radians(25), speed_m_s=0.15)
        return Command(steering_rad=0.0, speed_m_s=self.params.speed_m_s)


cmd = MyController().step(Observation(t_s=0.0, dt_s=0.02, ultrasonic_m={"front": 0.4}))
assert cmd.steering_rad > 0
```

- `Observation` contains the sensor values (`ultrasonic_m`, `lidar`, `heading_rad`, `speed_m_s`, …);
  a value is `None` when the sensor has no reading.
- `Command(steering_rad, speed_m_s)`: positive steering = left. The runtime limits steering and speed.
- `Tunable(default, min, max)` marks parameters the automatic tuner may optimise later.
- `self.emit("name", value)` sends extra values to the live plots; `self.state = "…"` shows your state.

## 3. Run it in the simulator

```bash
raceforge sim --controller my_controller.py --laps 3 --opponents 2
raceforge sim --controller my_controller.py --watch      # re-runs every time you save
raceforge sim --controller my_controller.py --record out/ # writes run.mcap (open in Foxglove Studio)
```

Start from a template in `controllers/templates/`: `wall_follow.py` (ultrasonic + gyro, no LiDAR),
`centering.py` (LiDAR), `state_machine.py` (LiDAR, states STRAIGHT/CURVE/OBSTACLE/RECOVER).
Parameters live in a YAML file next to the controller (e.g. `centering.yaml`).

## 4. Useful building blocks

```python
from raceforge.control import PID, Ema, Median, clamp

pid = PID(kp=1.5, kd=0.3, out_min=-0.5, out_max=0.5)
steer = pid.update(setpoint=0.45, measurement=0.60, dt_s=0.02)  # too far from the wall -> negative
assert -0.5 <= steer < 0

median = Median(3)  # removes single ultrasonic spikes
for value in (0.50, 2.55, 0.52):
    filtered = median.update(value)
assert filtered == 0.52
assert clamp(1.7, -1.0, 1.0) == 1.0
smooth = Ema(alpha=0.2).update(1.0)
assert smooth == 1.0
```

## 5. Rules for AI assistants (Copilot, Claude) writing controllers
- Only use `raceforge.control` — never import `raceforge.sim` in a controller.
- `step()` must be fast (< 2 ms) and must never block or sleep.
- Always handle `None` sensor values.
- Keep every tunable number in the `Params` class with `Tunable(...)`, not as a magic constant.
- Add a `test_*.py` with at least one hand-made `Observation`.
