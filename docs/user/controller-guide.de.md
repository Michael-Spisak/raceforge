# Einen Controller schreiben (für Java- / C#- / JavaScript-Entwickler)

Ein **Controller** entscheidet 50-mal pro Sekunde, wie das Auto lenkt und wie schnell es fährt. Dieselbe
Python-Datei läuft im RaceForge-Simulator und auf dem echten Auto.

## 1. Python in 5 Minuten (im Vergleich zu Java/C#)

| Java / C# | Python |
|---|---|
| `int x = 3;` | `x: int = 3` (Typangaben prüft pyright, nicht die Laufzeit) |
| `final record Point(double x, double y)` | `@dataclass(frozen=True) class Point: x: float; y: float` |
| `interface RobotIO { … }` | `class RobotIO(Protocol): …` (strukturelle Typisierung) |
| `enum State { DRIVE, STOP }` | `class State(Enum): DRIVE = auto(); STOP = auto()` |
| `null` | `None` — prüfen mit `if x is None:` |
| `"a=" + a` / `$"a={a}"` | `f"a={a}"` |
| `List<Double>` / `Map<String, Double>` | `list[float]` / `dict[str, float]` |
| `for (int i = 0; i < n; i++)` | `for i in range(n):` |
| JUnit / xUnit | `pytest` — Funktionen `test_*` mit einfachem `assert` |

Einrückung (4 Leerzeichen) ersetzt `{ }`. Einheiten sind immer SI: Meter, Sekunden, Radiant.

```python
from dataclasses import dataclass
from enum import Enum, auto


@dataclass(frozen=True)
class Punkt:
    x: float
    y: float


class Zustand(Enum):
    FAHREN = auto()
    STOPP = auto()


p = Punkt(1.0, 2.0)
abstand: float | None = None
assert p.x == 1.0 and abstand is None and Zustand.FAHREN.name == "FAHREN"
assert f"x={p.x:.1f}" == "x=1.0"
```

## 2. Der kleinste Controller

```python
import math

from raceforge.control import Command, Controller, ControllerParams, Observation, Tunable


class MeineParams(ControllerParams):
    speed_m_s: float = Tunable(0.3, 0.1, 1.0, description="Reisegeschwindigkeit")


class MeinController(Controller[MeineParams]):
    Params = MeineParams

    def step(self, obs: Observation) -> Command:
        vorne = obs.ultrasonic_m.get("front")
        if vorne is not None and vorne < 0.5:  # Wand voraus -> links abbiegen
            return Command(steering_rad=math.radians(25), speed_m_s=0.15)
        return Command(steering_rad=0.0, speed_m_s=self.params.speed_m_s)


cmd = MeinController().step(Observation(t_s=0.0, dt_s=0.02, ultrasonic_m={"front": 0.4}))
assert cmd.steering_rad > 0
```

- `Observation` enthält die Sensorwerte (`ultrasonic_m`, `lidar`, `heading_rad`, `speed_m_s`, …);
  ein Wert ist `None`, wenn der Sensor gerade nichts misst.
- `Command(steering_rad, speed_m_s)`: positive Lenkung = links. Die Runtime begrenzt Lenkung und Tempo.
- `Tunable(standard, min, max)` markiert Parameter, die der automatische Tuner später optimieren darf.
- `self.emit("name", wert)` schickt Zusatzwerte in die Live-Plots; `self.state = "…"` zeigt den Zustand.

## 3. Im Simulator fahren

```bash
raceforge sim --controller mein_controller.py --laps 3 --opponents 2
raceforge sim --controller mein_controller.py --watch      # startet bei jedem Speichern neu
raceforge sim --controller mein_controller.py --record out/ # schreibt run.mcap (Foxglove Studio)
```

Starte mit einer Vorlage aus `controllers/templates/`: `wall_follow.py` (Ultraschall + Gyro, ohne LiDAR),
`centering.py` (LiDAR), `state_machine.py` (LiDAR, Zustände STRAIGHT/CURVE/OBSTACLE/RECOVER).
Parameter stehen in einer YAML-Datei neben dem Controller (z. B. `centering.yaml`).

## 4. Nützliche Bausteine

```python
from raceforge.control import PID, Ema, Median, clamp

pid = PID(kp=1.5, kd=0.3, out_min=-0.5, out_max=0.5)
lenkung = pid.update(setpoint=0.45, measurement=0.60, dt_s=0.02)  # zu weit von der Wand -> negativ
assert -0.5 <= lenkung < 0

median = Median(3)  # entfernt einzelne Ultraschall-Ausreißer
for wert in (0.50, 2.55, 0.52):
    gefiltert = median.update(wert)
assert gefiltert == 0.52
assert clamp(1.7, -1.0, 1.0) == 1.0
glatt = Ema(alpha=0.2).update(1.0)
assert glatt == 1.0
```

## 5. Regeln für KI-Assistenten (Copilot, Claude), die Controller schreiben
- Nur `raceforge.control` verwenden — in einem Controller nie `raceforge.sim` importieren.
- `step()` muss schnell sein (< 2 ms) und darf nie blockieren oder schlafen.
- Sensorwerte können `None` sein — immer behandeln.
- Jede einstellbare Zahl in die `Params`-Klasse mit `Tunable(...)`, keine magischen Konstanten.
- Einen `test_*.py` mit mindestens einer selbst gebauten `Observation` dazulegen.
