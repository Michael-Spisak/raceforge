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
| 0010 | Teleop v1 (gamepad/keyboard/touch; sim + real car; demonstrations) | 4–5 | approved |
