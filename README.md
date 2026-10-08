# RaceForge

Construction, simulation and training tool for an autonomous LEGO Mindstorms (EV3) race car — plus **TrackScout**, a free iOS LiDAR app for scanning the race corridor.

> Status: **week 1 — foundation.** Nothing usable yet. See the [plan](docs/PLAN.md).

## What it will do
- **Construct** the car in a 3D editor from LEGO parts (LDraw) and your own 3D-printed parts.
- **Scan** the real corridor with TrackScout, merge passes, label walls/floor/objects, place start/finish.
- **Simulate** the car (MuJoCo) in the scanned corridor with opponents.
- **Train** controllers: classic tuning, reinforcement learning, imitation learning — on your team's GPU machines.
- **Drive** the real car with the same controller code, watch live telemetry, replay and compare runs.
- **Share** everything through a self-hosted backend; let AI assistants use it via MCP.

## Development
Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
```

### Start the user interface
```bash
uv sync                                   # Python engine (once)
cd frontend && npm ci && npm run build    # UI (once, and after UI changes)
npm run electron                          # desktop app (starts the engine itself)
```
or, without Electron: `PYTHONPATH=src uv run raceforge ui --browser` (opens http://127.0.0.1:8765).
For real LEGO geometry download the LDraw library once: `PYTHONPATH=src uv run raceforge parts fetch`.
UI development with hot reload: run `PYTHONPATH=src uv run raceforge ui` and, in `frontend/`, `npm run dev`
(http://localhost:5173 proxies the engine).

Rules for contributors and AI agents: [AGENTS.md](AGENTS.md).

## License
GPL-3.0-or-later. LEGO® is a trademark of the LEGO Group, which does not sponsor or endorse this project.
