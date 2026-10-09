# Module specs

Every module gets a spec here **before** any code is written (see AGENTS.md rule 1).
Detail questions are asked just-in-time while writing each spec.

| # | Spec | Week | Status |
|---|------|------|--------|
| 0001 | Core schemas (vehicle/assembly, track, run log, telemetry frame) | 1–2 | implemented (PR) |
| 0002 | Parametric quick-start (real LEGO parts) → MJCF | 2 | implemented (PR) |
| 0003 | Sim runtime: sensors, procedural corridors, multi-car worlds, MCAP recording | 2–3 | implemented (PR) |
| 0004 | RobotIO + controller SDK (for Java/C# devs) | 2–3 | implemented |
| 0005 | car_runtime (Rust core) + EV3 serial bridge + safety | 2–3 | implemented |
| 0006 | Backend v1 (auth, versions, blobs, sync) | 2–3 | implemented (PR) |
| 0007 | TrackScout v1 (recording, passes, .tscan, upload routing, importer) | 2–5 | approved |
| 0008 | Frontend shell v1 (engine API, Electron, parts, construct, simulate, replay) | 2–3 | implemented (PR) |
| 0009 | Scan viewer v1 (TrackScout passes in the desktop app) | 4–5 | implemented (PR) |
| 0010 | Teleop v1 (gamepad/keyboard/touch; sim + real car; demonstrations) | 4–5 | implemented (PR) |
| 0012 | Deploy from the app (bundle + SSH/USB install, Live tab) | 4–5 | approved |
| 0013 | Training v1: RaceForgeEnv, benchmark, Optuna tuning (+ Train tab) | 4–5 | approved |
| 0014 | Quick tracks: draw a 2D corridor, race on it (Tracks tab) | 4–5 | approved |
| 0015 | Construct editor v1 part A: edit the assembly (move, turn, add, delete, snap, undo) | 4–5 | approved |
| 0016 | Rule checker, budget and overlaps in the Construct editor | 4–5 | approved |
| 0018 | More parts: LDraw library search, team catalogue additions | 4–5 | approved |
| 0019 | Import 3D-printed parts (STL/3MF/OBJ/PLY, mass, cost, mesh) | 6–7 | approved |
| 0020 | Workers v1: team computers run training/benchmark jobs (worker tokens) | 4–5 | part C in progress |
| 0021 | Connectors on 3D-printed parts (click on the mesh) | 6–7 | approved |
