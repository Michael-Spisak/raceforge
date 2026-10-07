# Plan: "RaceForge": construction, simulation and training tool for the autonomous LEGO race car (+ "TrackScout" iOS scanning app)

## Context
This is a school project (SYP 5A). An autonomous LEGO Mindstorms car has to race through a school corridor. There is no guide line and no wireless connection is allowed during the race. The wheels and the steering mechanism must be LEGO, and the budget is 200 € (see `~/Downloads/lego-roboter-technik-prompt.md`). The team wants software to (1) construct the car from LEGO parts and its own 3D-printed parts, (2) simulate it in the real corridor with other cars, and (3) train its driving behaviour. The project folder `construction-and-simulation-tool/` is empty, so this starts from scratch.

**Decisions from Q&A:**
- **Hardware:** EV3 + an onboard Linux computer (Raspberry Pi 5, Orange Pi 5 or similar; chosen mid-January by benchmark). The steering motor may be non-LEGO; the steering system itself must be LEGO; 3D-printed parts are allowed everywhere else.
- **Infrastructure:** GitHub for source code and release downloads; backend on the home Proxmox server; compute only on team workers (RTX 4070 laptop, RTX 3070 PC, gaming laptops, MacBook Air M2s, ThinkPad); TrackScout on LiDAR iPhones/iPads; English + German UI.
- **Construction:** a **full 3D editor** in our own tool for LEGO parts *and* custom 3D-printed parts. A parametric quick-start generates a starter car.
- **Simulation:** 3D physics engine.
- **Training:** all three methods (classic controller tuning, reinforcement learning, imitation learning).
- **Track:** scanned with the iPhone 17 Pro LiDAR.
- **Stack and platforms:** Python, running on macOS, Windows 11 and Linux.
- **Sim-to-real:** full loop (export controller, import real logs, record real drives).
- **Timeline:** **race in late February**, feature freeze on Feb 2. Full scope is kept. You build the tool alone with Claude Code + Copilot (20+ h/week); the car team (4 Java/C#/JS coders + 1 3D modeler) uses it once the car exists.
- **AI access:** an MCP server so external AIs (Claude, Cursor, Perplexity and others) can operate the tool.
- **Collaboration:** versioning of the car and all configs, easy sharing between team members, and a live screen showing the real car's status and performance.
- **Development method:** the tool itself is built entirely by AI (Claude Code, Cursor and others) using **controlled vibe coding**. Humans write specs, contracts and reviews; AI writes the code; automated guardrails decide what gets merged.

## Development approach: controlled vibe coding
**Setup:** **one human developer (you), 20+ h/week**, building the whole tool with Claude Code (Pro/Max) and GitHub Copilot (Student) until the **race in late February** (about 19 weeks). The full scope is kept, which is a deliberate high-risk choice (see *Risk management* under Milestones). The 4 car coders (Java/C#/JS, little Python) and the 3D modeler are **users** of the tool and write the car controllers in Python with AI help. They start once the real car exists.

The goal is AI speed without losing control of the codebase. **You own the "what" and the gates; AI owns the "how".** Throughput comes from running **several AI agents in parallel** on independent modules.

**1. Guardrail files (created first, before any feature code)**
- `AGENTS.md`, with a `CLAUDE.md` pointing to it and `.cursor/rules/` mirroring it, so every AI tool reads the same rules. It covers:
  - architecture rules (front-ends only call `raceforge.api`; `control/` has no sim imports; the schemas in `core/` are the contract);
  - coding conventions and the definition of done;
  - forbidden actions (no new dependencies without an ADR, no edits to `core/` schemas without spec approval, never weaken or delete tests to make CI pass);
  - commands to run before finishing a task.
- `docs/specs/<module>.md`: one spec per module (purpose, inputs/outputs, acceptance criteria, examples), written or approved by a human **before** AI implements it. Specs are generated with AI help and then edited by the team.
- `docs/adr/`: short Architecture Decision Records for every stack or design decision. The decisions in this plan become ADR-001 to ADR-0xx.
- **GitHub Issues + GitHub Projects** (a Kanban board each for the tool repo and the controller repo), using an issue template with goal, spec link, acceptance tests, files in scope and files out of scope.

**2. Workflow for every feature (repeated hundreds of times)**
1. You pick or split an issue sized for one AI session (about 1–4 h; one module; ideally under 500 changed lines).
2. **Parallel agents:** with **Claude Pro**, realistically **1–2 Claude Code sessions** run at a time, locally and as **cloud sessions** (claude.ai/code) that keep working while your laptop is closed. Each runs in its **own branch/worktree** on a *different* module. Work that needs hardware, iOS or a GPU runs locally.
   - **GitHub Copilot coding agent** (included in Copilot Student) takes well-specified low-risk issues (UI polish, docs, tests, reports, i18n strings) and opens PRs on its own. This is a third parallel "developer".
   - Module boundaries and contract tests keep the agents from colliding.
3. AI first writes or extends **tests from the acceptance criteria** (test-first). You skim them, because the tests are the real spec.
4. AI implements until all gates are green, then opens a PR with a summary, risks and screenshots/GIFs for UI work.
5. **Two AI reviews + your review:**
   - Claude `/code-review` for correctness;
   - **GitHub Copilot code review** on the PR as an independent second reviewer;
   - you review the diff for intent and architecture (you are strong in Python and TypeScript/React; **Swift/TrackScout code relies more on XCTest, the device checklist and both AI reviews**), with the depth set by risk (`core/` schemas, API, car runtime and race-mode code get a line-by-line review; UI polish gets a skim plus a screenshot check).
6. **Merge rules:**
   - **Auto-merge for low-risk areas** (UI polish, docs, tests, reports, i18n) when all gates are green and both AI reviews pass; a path-based CODEOWNERS/label rule decides.
   - **Your approval is always required** for `core/`, API/MCP schemas, `car_runtime`, race mode, safety and the backend auth code.
   - After merging, CI publishes nightly builds for all 3 OSes.
7. **Teammates:** controller code PRs from the car team go through the same gates, with you as reviewer (Copilot helps them write Python).

**3. Automated gates (branch protection on `main`, nothing merges red)**
- Python: `ruff` (lint + format), `pyright` (strict in `core/`, `api/`, `control/`), `pytest` with a coverage floor (≥ 80 % for core, construct, sim and control).
- Front-end: `eslint`, `tsc --noEmit`, Vitest, Playwright e2e.
- **Contract tests:** the OpenAPI schema and MCP tool schemas are snapshot-tested, so an AI cannot silently change an interface.
- **Golden tests:** reference assemblies → expected mass, CoG and turning radius; reference sim runs → expected metrics within tolerance; these catch "it compiles but physics is wrong".
- **Architecture tests:** import-linter enforces the module boundaries.
- Pre-commit hooks run the same checks locally, and CI runs a matrix on macOS, Windows and Linux.

**4. Control points where humans decide**
- Schemas in `core/`, the API, the MCP tool list, the telemetry frame format, and anything that touches the real car or the race rules.
- Real-hardware tests are always done by people. AI can analyse the logs afterwards through the tool's own MCP server.
- At each milestone end there is a review: architecture drift check, deletion of dead code, and an update of `AGENTS.md` with lessons learned. Mistakes that AI made repeatedly turn into new rules.

**4b. GitHub: source code and software download**
- All tool source code (desktop app, backend, worker, car runtime, EV3 side, TrackScout) lives in **one public GitHub repo on your personal account**. The car team's controllers live in a separate private repo that a teammate creates. Teammates are added as collaborators where needed.
- The tool repo uses with GitHub Issues/Projects for tasks and GitHub Actions for CI.
- **Being public means:**
  - **unlimited GitHub-hosted CI minutes**, including macOS and Windows runners;
  - **no self-hosted runners**, because fork PRs could run code on your machines; GPU tests run as manual workflows on your workers instead;
  - **no secrets or data in the repo**: domain, tokens and Cloudflare/Tailscale config come from environment and secrets, with **secret scanning + push protection** enabled. Scans, models and logs only ever live on the backend;
  - licence: **GPL-3.0**. Dependencies are GPL-compatible (MuJoCo, SAM 2, Grounding DINO and gsplat are Apache-2.0; Open3D, three.js and FastAPI are MIT); a CI licence check (`pip-licenses`, `license-checker`) blocks incompatible additions. Third-party data licences are respected: LDraw parts (CC BY 2.0, attribution in the About dialog) and the LDCad shadow library.
- **Releases:** tagging `vX.Y.Z` makes GitHub Actions build and attach to a **GitHub Release**:
  - desktop app installers: Windows `.msi/.exe`, macOS `.dmg` (arm64), Linux AppImage/`.deb`;
  - the `raceforge-worker` installer;
  - the `car_runtime` package for the RPi;
  - the EV3 program.
- **Backend images** go to **GitHub Container Registry (ghcr.io)**. The Proxmox VM pulls a pinned release tag with `docker compose pull`.
- **Updates:**
  - The desktop app and workers check GitHub Releases and offer one-click updates.
  - Workers automatically prepare the environment for any code version a job needs.
  - The backend refuses clients whose version is too old or too new for its API, and shows an update hint.
- **TrackScout:** Apple doesn't allow installable iOS builds from GitHub without a paid account. The source is in the same repo, and each iPhone owner builds and installs it from a MacBook Air M2 with a free Apple ID; the README has a step-by-step guide.

**4c. Documentation and onboarding**
- **Setup guides** for all three OSes (the car team uses Windows, macOS and Linux).
- **Documentation website** in **German + English** (MkDocs Material with i18n) on GitHub Pages, built automatically from `docs/` on every release. It covers:
  - user guides per tab;
  - the "Python for Java/C# devs" controller guide;
  - the TrackScout build-and-install guide;
  - backend/worker setup;
  - race-day checklist;
  - API/MCP reference generated from the schemas.
- **App structure:** one window with tabs; panels and tabs can be **popped out** to a second monitor. A **command palette** (Ctrl/Cmd+K: commands, parts, cars, runs) and a keyboard-shortcut overview.
- **Demo workspace:** a read-only, copyable "Demo" workspace (demo car, demo corridor, example controller) ships with every install, so a first sim run in under 15 minutes is possible.
- **Reports are created on demand only** (button, CLI or MCP); nothing is generated automatically after sessions.
- **In-app help:**
  - a **context help "?"** on every panel that opens the matching docs page;
  - **tooltips** everywhere;
  - an **FAQ/troubleshooting** section (car won't connect, worker offline, upload stuck …).
- **In-app guided tours** on first start of each tab (editor, track, train, live), re-startable from the help menu and available in both languages.

**4d. Quality feedback loop**
- **"Report a bug" button** in the desktop/browser app, TrackScout and workers. It creates a **GitHub issue** (through the backend's GitHub App/token, so users need no GitHub login) with a screenshot, version, OS and recent logs, with secrets removed.
- **Crash reports:** a self-hosted **GlitchTip** (Sentry-compatible) in the backend Compose, collecting crashes from the desktop app, worker, backend and car_runtime (car_runtime crashes upload after the drive).
- **Release QA:** green CI plus a **10-minute manual smoke checklist** on Windows and macOS before every release. Releases continue normally during race week; there is no update freeze.
- **Language:** AGENTS.md, specs, ADRs and code docs are written in **English**; the UI and user docs are German + English.

**5. Dogfooding:** once the MCP server exists (January), AI agents use the tool through MCP to test it. For example: "build a car, simulate it, report anything weird". Bugs they find become issues.

**6. Roles:**
- **You** are the tool owner: specs, architecture, reviews, releases and backend ops.
- **The 4 car coders** write the Python controllers (state machine, PID, sensor fusion) against the `raceforge.control` SDK, run sims and trainings, and do the real-car tests.
- **The 3D modeler** designs the custom parts and is the first power user of the construct editor and custom-part import.
- **Everyone with a LiDAR iPhone/iPad** scans with TrackScout.

**7. Python controller SDK for Java/C#/JS developers** (ready before the car team starts):
- Fully typed API (type hints + pyright) that feels like C#/Java: classes, interfaces/protocols and enums.
- Controller templates (wall-follow, centering, state machine) with comments that map to Java/C# concepts.
- A one-page "Python for Java/C# devs" cheat sheet, plus `AGENTS.md` rules so Copilot and Claude write controllers that follow the SDK.
- `raceforge sim --controller my_ctrl.py` hot-reloads the controller and runs instantly in the sim; the same file runs unchanged on the car.

## Answer: one software, not three tools
The three tools form one pipeline over the same data:
`assembly (construct) → 3D sim (physics + sensors + corridor + opponents) → training → export to car → real logs → back into the sim`.

The car you build is the simulator's input, and training is just the simulator running headless many times. Three separate tools would still need a shared format and would drift apart. So the plan is **one repository and one application** with:
- a desktop UI with tabs *Construct / Track / Simulate / Train / Deploy / Live / History*;
- a CLI for long headless training jobs;
- an **MCP server** for AI assistants.

All three front-ends (UI, CLI and MCP) are thin layers over one Python **service API** (`raceforge.api`), so every feature is implemented once and shows up everywhere.

Two key design rules:
1. **The same controller code runs in the simulator and on the real car.** It goes through one `RobotIO` interface, with a `SimIO` implementation and a `RealIO` implementation.
2. **The construction assembly is the single source of truth.** Mass, centre of gravity, joints, sensor poses, collision shape and budget are *derived* from the assembly, never typed in twice.

## Tech stack
| Area | Choice | Why |
|---|---|---|
| Core, sim and training | Python 3.12, `uv`, pyproject | One language for the tool and the car (RPi + ev3dev) |
| Physics and sensors | **MuJoCo** | Native on macOS arm64, Windows and Linux; fast enough for RL; rangefinder/IMU/encoder/camera sensors; MJCF generated from the assembly |
| Data model | Pydantic v2 + YAML/JSON | Schemas for parts, assemblies, tracks, controllers, runs and telemetry |
| Backend for the UI | FastAPI + WebSocket (over `raceforge.api`) | Serves the front-end and streams sim, training and live telemetry |
| **Front-end** | **TypeScript + React + three.js (React Three Fiber)**, light/dark theme following the system (switchable), **colour-blind-safe palettes by default** (viridis-style heatmaps, patterns in addition to colour), responsive Live and Results views for tablets and phones | A full 3D brick editor needs custom 3D interaction (snapping, gizmos, picking). three.js includes an **LDrawLoader**. |
| **Desktop shell** | **Electron** (Chromium on every OS → identical WebGL/WebGPU), with the Python engine as a sidecar process | Consistent 3D performance on Windows, macOS and Linux |
| Python engine install | Small installer; on first start **uv** installs the pinned Python + packages, including CUDA PyTorch when an NVIDIA GPU is detected | Small downloads; the same mechanism as the workers |
| Scripting | `raceforge` Python client SDK (same API as MCP) + example Jupyter notebooks for log/run analysis | Custom analyses |
| LEGO part data | **LDraw** official parts library (geometry), **LDCad shadow library** (connection/snap info: pins, axles, studs), BrickLink catalogue weights | Open, complete and cross-platform |
| Custom-part import | trimesh (STL/OBJ/3MF/PLY) + OCP/CadQuery (STEP) | Covers Fusion, Onshape, FreeCAD and Tinkercad exports |
| Collision shapes | CoACD / V-HACD convex decomposition | MuJoCo needs convex collision geometry |
| Capture processing | Open3D (TSDF fusion, RANSAC, ICP, DBSCAN), trimesh, `usd-core` (USDZ/RoomPlan), PyAV/ffmpeg (video), pye57 | Mesh, point cloud and RGB-D fusion and geometric segmentation |
| Semantic segmentation | PyTorch + `transformers` (SegFormer/Mask2Former), **SAM 2** + Grounding DINO (open-vocabulary) | Runs on CUDA, Apple MPS or CPU (slower); models downloaded on first use |
| Mobile capture app | Swift + SwiftUI, ARKit (scene depth, mesh classification), RoomPlan, Network.framework | Best-quality, single-pass capture in our own format |
| Large-data storage | Content-addressed blobs in MinIO on the backend + local cache | Raw scans, videos and models |
| Point cloud rendering | three.js Points with octree LOD (Potree-style) + downsampling | Smooth editing of multi-million-point scans |
| RL / imitation / tuning | Gymnasium + Stable-Baselines3 + PyTorch / `imitation` / Optuna | Standard tools |
| Policy export | ONNX → onnxruntime on the RPi | No PyTorch needed on the car |
| Logs and telemetry | msgpack frames live → **MCAP** files (Parquet export for analysis) | One format for sim, live and race logs; opens directly in **Foxglove Studio** |
| Robotics interop | **URDF export** of the car, optional **ROS 2 bridge** in car_runtime (publishes `/scan`, `/imu`, `/odom`, `/tf`, `/camera`) | Use ROS tools and other simulators if needed |
| Car links (test mode) | Pluggable transports: **Wi-Fi** (WebSocket), **Bluetooth** (RFCOMM serial + BLE via `bleak`), **long-range serial radio** (HC-12 433 MHz / SiK / LoRa via `pyserial`), optional **ESP-NOW** bridge (ESP32 USB dongles), cable (USB/Ethernet) | One telemetry protocol, many links; auto-failover |
| AI integration | MCP Python SDK (FastMCP), stdio + Streamable HTTP | Claude Desktop/Code, Cursor, Perplexity |
| Backend | FastAPI + PostgreSQL + MinIO + Redis + Yjs sync on Proxmox (Docker Compose), Caddy, Tailscale + Cloudflare Tunnel/Access | Central data, versions, jobs, collaboration and telemetry relay (see §9) |
| Local store | SQLite + blob cache in each desktop app | Offline-first editing and simulation |
| Compute workers | `raceforge worker` (uv-pinned envs, CUDA/MPS/CPU) | Training and processing on any team machine |
| UI language | i18n (react-i18next) with **English + German**, switchable; code and docs in English | Team and teachers |
| CI | GitHub Actions matrix (mac/win/linux), pytest, Vitest/Playwright for the front-end | Proves it runs on all 3 OSes |

## Repository layout
```
construction-and-simulation-tool/
  pyproject.toml
  src/raceforge/
    core/        # pydantic schemas: Part, Connector, Assembly, Joint, Track, Controller, RunLog, TelemetryFrame; units; rules
    parts/       # LDraw + LDCad-shadow loader, curated part catalogue (Technic + EV3), weights/prices, custom-part import & processing
    construct/   # assembly logic: connection graph, rigid-group detection, joints, mass/CoG/inertia, collisions, BOM, budget, rule checker, parametric quick-start
    capture/     # importers (RoomPlan USDZ, mesh/point cloud, Record3D/3d Scanner RGB-D+poses, video/photos), raw store, registration
    perception/  # processing pipeline: fusion, cleanup, geometric + image segmentation, 2D↔3D label projection, dataset export, onboard model training
    track/       # track model, edit layers (labels, geometry, race setup, objects, surfaces), validation, sim-geometry/2D-map/centreline, procedural generator
    sim/         # assembly→MJCF builder, sensor models (noise/latency), opponents, SimIO, recorder
    control/     # RobotIO, state machine, PID, policy runner (shared with the car)
    train/       # Gym env, domain randomisation, Optuna tuner, SB3 RL, imitation (BC/DAgger)
    deploy/      # export bundles, log import, sim calibration
    workspace/   # local store (SQLite + blob cache), sync client, versions, run registry, .raceforge bundles
    reports/     # PDF/Excel templates: budget/BOM, test & race reports, version changelog
    backend/     # server-side: auth (own accounts, invites, 2FA, API tokens), version store (Postgres), blob store (MinIO), job scheduler, locks/presence, telemetry relay
    worker/      # compute worker agent: registration, heartbeats, job runner (uv envs), blob fetch, checkpoint upload/resume
    telemetry/   # frame schema, bandwidth tiers, transports/ (wifi_ws, bt_rfcomm, ble, serial_radio, espnow, cable), link manager + failover, replay
    api/         # service layer + async job manager
    server/      # FastAPI + WebSocket endpoints for the front-end
    cli/         # `raceforge` CLI (ui, train, bench, export, mcp)
    mcp/         # MCP server over api
  frontend/      # React + three.js app (editor, sim viewer, live dashboard, history)
  car_runtime/   # RPi package: RealIO (EV3 via USB serial), control loop, logging, telemetry server
  trackscout_ios/   # "TrackScout" iOS app (SwiftUI, ARKit, RoomPlan) → .tscan files, Bonjour upload to the desktop tool
  ev3_side/      # ev3dev program: motors + LEGO sensors ↔ serial; status on the EV3 LCD and LEDs
  tests/
```

## Module details

### 1. Construct: full 3D editor (LEGO + custom 3D-printed parts)
**Part library**
- **LEGO:** LDraw geometry plus LDCad shadow-library connectors.
  - The **curated catalogue** has about 300–500 verified Technic and EV3 parts (beams, axles, pins, gears, steering parts, wheels/tyres, EV3 brick, motors and sensors), each with snapping info, weight and colour.
  - The **rest of the LDraw library is searchable** too, marked "unverified" (possibly without weight or snap info, which can be added on use).
- **Inventory:** "our EV3 box" kit inventories (school-provided parts cost 0 €). The exact EV3 set is unknown, so the inventory can be **entered manually** or **imported by set number** (Rebrickable part lists, for example 45544/45560/31313) once known. Own parts are added the same way. The editor warns when a design uses more of a part than the team owns.
- **Custom 3D-printed parts:**
  - Import STL/3MF/OBJ/STEP from the team's tools: **Blender, Tinkercad** (STL/OBJ/3MF) and **FreeCAD, SolidWorks, Inventor** (STEP preferred, STL as fallback).
  - A **watch folder** re-imports a part automatically when the modeler re-exports it, creating a new part version and keeping the connector definitions if the geometry still fits.
  - Set units and orientation.
  - **Define connectors** by clicking a hole or face and choosing a type (Technic pin hole, axle hole, stud, anti-stud, screw hole, generic fixed mount). This lets custom parts snap to LEGO.
  - Set material (PLA/PETG/TPU) and infill %. Mass is calculated from volume × density × infill, or the measured weight is entered instead.
  - Filament cost and slicer print time feed into the budget.
  - Each custom part is versioned (`sensor-mount@v3`) in the shared parts library on the backend. Assemblies reference an exact part version, and the editor offers "update to latest".
  - Non-LEGO electronics (RPi, battery, ToF/LiDAR sensor, external motor, camera) are custom parts with an extra "device" role: sensor type, motor spec and power draw.

**Editor features**
- Part browser with search, categories, recently used parts and inventory counts.
- **Navigation presets** that can be switched: **Blender** (MMB orbit, Shift+MMB pan, G/R/S), **BrickLink Studio** and **Fusion**. **Units:** mm, plus the LEGO grid (studs/holes, 1 stud = 8 mm) where useful.
- **Overlaps:** interpenetrating parts are allowed but **highlighted in red** and listed by the rule checker.
- **Saving:** **autosave of a draft** continuously, plus an explicit **"Create version"** with a message, like a git commit. Only explicit versions appear in the history and lineage.
- **Buildability check:** when generating building instructions, each part is checked for whether it can be inserted freely along its connection direction at its step; problems are flagged.
- **Colours:** coloured **by function** by default (steering, drive, sensors, electronics, chassis, printed parts), switchable to real LEGO colours.
- **Snapping, two modes that can be switched:**
  - **quick:** auto-snap to the nearest compatible connector; Tab cycles through candidates; Alt places freely;
  - **precision:** explicitly pick connector A on the part, then target connector B.
- **Submodels:** created manually, with **suggestions** from the connection graph (for example "these 23 parts form a rigid steering assembly").
- **Linked instances, including mirroring:** for example, the left and right suspension are one submodel; editing it updates both.
- **Gears:** meshing gears are **detected automatically** (axle distance and orientation), ratios are computed along the drivetrain, the drivetrain can be animated, and blocked or conflicting gear trains are flagged.
- Placement with **connector snapping** (pin/axle/stud), 90° rotation shortcuts, a free gizmo, and a LEGO grid (LDU) when nothing snaps.
- Multi-select, copy/mirror (cars are symmetric), groups and **submodels** (chassis, steering assembly, drive, sensor mast), and undo/redo.
- Overlap/collision check between parts, a measure tool, cut/section view, hide/show and colour modes (by part type, LEGO vs printed, or submodel).
- **Mechanics:**
  - Axle connections are marked as rotating joints.
  - Joints are tagged by role: steering pivot, wheel axle, drive motor, steering motor, or gear mesh with its ratio.
  - A **kinematics preview** turns the steering motor and shows wheel angles, Ackermann error and turning radius.
- **Printed-part strength hints:** a wall-thickness check, layer orientation vs main load direction (from the connection graph), and warnings for thin pins and snap features. There is no FEM.
- **Measured values override calculated ones:** real weight (kitchen scale) and centre of gravity (tilt/balance test) can be entered per car or submodel. The panel shows measured vs calculated, and the sim uses the measured value.
- **Derived data panel:** total mass, CoG (shown in 3D), wheelbase and track width, turning radius, gear ratios → top speed and torque, BOM, budget.
- **Cable routing:**
  - EV3 cables (fixed LEGO lengths), USB, LiDAR and battery leads are drawn as 3D paths between ports, with length checks.
  - A **collision check against moving parts** sweeps the full steering angle and wheel rotation and flags cables that could get caught in wheels or steering.
  - A port-assignment table shows which device is on which EV3/board port.
- **Building instructions:** step-by-step instructions are generated from the submodels (build order from the connection graph, a parts list per step, 3D views per step, printed parts marked). They are shown interactively in the app and exported as PDF, so the car or a spare car can be rebuilt exactly.
- **Design optimisation (worker job):** pick parameters to vary (wheelbase, track width, gear ratio, sensor positions and angles, battery position) and their ranges. Variants × the current controller are simulated on workers with the race benchmark score, and the output is a ranked list of suggestions with reasons, sensitivity plots and a one-click "apply as new version".
- **Rule checker** (live):
  - wheels and steering mechanism are LEGO;
  - **the steering system submodel consists only of LEGO parts**, with the steering motor exempt (it may be non-LEGO). 3D-printed parts are allowed everywhere else;
  - the EV3 has at least one driving function;
  - budget ≤ 200 €;
  - no enabled radio module in the race config;
  - **size/weight limits:** unknown so far, so they are configurable rules (max length, width, height and mass) that stay empty until the teacher defines them. A built-in check compares car width with the narrowest corridor spot.
- **Prices:** entered manually per part, with an eBay/Willhaben link and date. The tool reminds you when a price is older than 30 days. 3D-print cost is **estimated** as volume × infill × density × price per kg.
- **Parametric quick-start:** the layout (rear-wheel drive, all-wheel drive or front-wheel drive, always with LEGO Ackermann front steering, since the team's chassis idea is still open), wheelbase, track width, wheels, motor type and sensor positions generate a starter assembly that can then be edited.
  - **Baseline car:** until editor v1 exists (early November), the real car built at the end of October is represented with the quick-start using measured dimensions.
  - The **3D modeler then rebuilds it in the editor** as the versioned baseline. This also lets the sim and training work begin before the full editor is finished.
- **Import/Export:**
  - import LDraw `.ldr/.mpd` and BrickLink Studio `.io` files;
  - export `.mpd` (custom parts included as LDraw-converted parts);
  - export STL/3MF of custom parts for printing;
  - export a BOM/budget CSV;
  - build steps (stretch goal).

**Assembly → simulation:** the tool builds a connection graph, merges rigidly connected parts into bodies, and turns marked axle connections into MuJoCo joints. Mass and inertia are summed per body. Collision geometry uses simplified boxes or convex hulls per body, and the detailed mesh is used for display only. Sensors and motors come from device-role parts and their poses.

### 2. Track: capture → process → edit (iPhone 17 Pro 3D, video and image data)
The pipeline turns phone captures into an editable, labelled, versioned track. That track is used for simulation and as AI training data.

**a) Capture and import.** One import wizard accepts every format below, and several captures can be combined into one track, for example scanning the corridor in parts.
- **RoomPlan USDZ:** parametric walls, doors, windows and objects with categories.
- **LiDAR mesh / point cloud:** OBJ, PLY, USDZ, E57, optionally with colour.
- **RGB-D sequences with camera poses** from free apps (3d Scanner App "all data" export, Scaniverse). Formats of paid apps (Record3D `.r3d`, Polycam raw) are supported optionally but never required.
- **Plain video (MOV/MP4) and photos:** used for textures and the image dataset; when poses are missing they are registered against the mesh.
- **Primary source from February: our own iOS app "TrackScout"** (see **2f**).

The raw capture is stored **immutable** and content-hashed. All later steps are reproducible processing plus **non-destructive edit layers**.

**a2) Multiple scans per track: passes, merging and coverage guidance**
- **A track is built from any number of scans ("passes")** taken from different heights and viewpoints. The car-height view is one *optional* pass type, not the basis. Pass types:
  - **walkthrough** (handheld, chest height; the main geometry);
  - **high/overview** (phone held up, sees over objects and the floor layout);
  - **low/car-height** (cart or stick at 10–15 cm; camera-like training frames and under-object detail);
  - **detail** (close-ups of doors, glass, corners, obstacles);
  - **gap-fill** (targeted re-scans from the coverage guide).

  Each pass is tagged with its type, time, device and conditions (lights on/off, doors open/closed).
- **Registration of scans into one coordinate frame**, with these options in order:
  1. **Same-anchor sessions:** the app saves an `ARWorldMap` and later passes relocalise into it, so they are aligned from the start. This is the best option.
  2. **Automatic global registration:** FPFH features + RANSAC, then point-to-plane ICP refinement, plus multi-scan pose-graph optimisation to spread the error evenly.
  3. **Manual fallback:** pick 3+ matching points or use shared markers (printed AprilTags/ArUco placed in the corridor while scanning; the app and the tool detect them automatically).

  The UI shows the residual alignment error per scan pair, and alignments can be accepted, nudged or rejected.
- **Quality-weighted fusion ("best data wins"):** every depth sample is weighted by:
  - LiDAR confidence;
  - distance (closer is better; LiDAR gets noisy beyond about 3–4 m);
  - viewing angle (head-on is better than grazing);
  - motion blur and tracking quality;
  - pass priority (detail > gap-fill > walkthrough).

  Fusion is a weighted TSDF over all passes. For each surface patch, the texture comes from the sharpest, most head-on frame.
- **Conflict policy:** when a new pass contradicts an older one (for example a moved cabinet), the **user decides every time** from a conflict list (keep new / keep old / make a variant), with both shown side by side.
- **Conflict handling:**
  - Geometry that appears in some passes but not others (people, a moved bin, a door that is open in one pass and closed in another) is detected by cross-pass consistency.
  - It is either removed as transient or turned into a **variant** (door open/closed). Variants are reused directly as domain randomisation in training.
- **Scan layers in the editor:** toggle passes on and off, view per-pass contribution, exclude a bad pass, and re-merge. Edits stay because they are anchored to track coordinates, not to a scan.
- **Coverage and quality analysis (the "scan more here" guide).** For each voxel and surface patch the tool computes:
  - observation count;
  - viewing-angle diversity;
  - best distance and mean confidence;
  - holes/unobserved space next to known surfaces;
  - texture sharpness;
  - label uncertainty from segmentation.

  These become a **quality score**, which the tool turns into:
  - a 3D heatmap and 2D plan overlay (red = needs data, yellow = weak, green = good), with special emphasis on the **drivable area and the wall band at sensor height (0–30 cm)**, since that is what matters for the car;
  - a ranked **"scan tasks" list** ("wall left of door B: no data below 20 cm", "glass section: low confidence, re-scan at a 45° angle", "corner C: hole") with suggested viewpoints, heights and pass types;
  - a **scan mission file** sent to the phone app. The app relocalises in the saved world map and shows the gaps **as an AR overlay** with arrows, so the user walks there and fills them. The new pass merges incrementally and the heatmap updates.
- **Merge report:** total coverage % of the drivable area and the sensor-height wall band, alignment errors, removed transients and remaining tasks. The track validator can require, for example, ≥ 95 % "good" coverage of the drivable corridor before a track is marked "race-ready".

**b) Processing pipeline** (background job with progress and per-step cache; it can be re-run without losing edits):
1. **Ingest and check:** validate the files, check units and scale, gravity-align (using ARKit gravity), and report coverage gaps and holes.
2. **Registration and fusion:** all passes are aligned and fused with quality weighting into a coloured mesh and point cloud (see **a2**), and coverage is computed.
3. **Clean-up:** remove noise (people walking through, reflection ghosts on glass, flying points), crop to the region of interest, decimate.
4. **Automatic segmentation, labelled per point and per face with a confidence value.** Three steps are combined:
   - **Geometric:** RANSAC/region growing finds planes. The floor is the largest low horizontal plane, walls are vertical planes, and the ceiling is the high horizontal plane; the remaining clusters are objects (DBSCAN).
   - **RoomPlan categories** are used where available (door, window, table, chair, storage …).
   - **Image-based:** semantic segmentation on the video frames (SegFormer/Mask2Former) plus open-vocabulary detection with masks (Grounded-SAM 2, with prompts like "door", "glass", "trash bin", "bench", "radiator", "pillar"). The 2D labels are **projected into 3D** using the camera poses and fused by confidence-weighted voting.
5. **Classes:** floor, wall, ceiling, door (open/closed), glass/window, pillar, step/stairs, static object (bench, bin, cabinet …), dynamic/ignore (people), unknown. Each class has a material/surface attribute (tiles, linoleum, glass, dark, shiny) that drives sim friction and sensor error models. **Users can define their own classes** (for example "radiator", "fire extinguisher", "school bag") with material and sensor properties. The class name is fed to the open-vocabulary detector automatically.
6. **Sim geometry:**
   - floor → plane/heightfield;
   - walls → fitted wall polylines → boxes;
   - objects → convex hulls or primitive boxes;
   - the detailed textured mesh is used only for visuals and the camera simulation.
7. **2D map and centreline:** top-down occupancy grid at sensor height, drivable area, auto-extracted centreline (medial axis) and corridor width profile, used for the reward function, progress and the Live map.

**c) Track editor (Track tab, 3D view + synchronised 2D top-down plan)**
- **Layout that can be switched:** 2D plan + 3D side by side, 3D only, or 2D only.
- **Label review:** labels with **> 90 % confidence are accepted automatically**; everything below goes into a **review list**, sorted by impact (drivable area first).
- **Publishing:** new passes and edits land in a **draft**; **"Publish version"** makes the track available for sim and training.
- **View modes:** photo-textured mesh, point cloud coloured by label, confidence heatmap (shows the regions the user should review first), 2D plan, and the original video frame at the clicked position.
- **Label correction / manual mapping:**
  - Selection tools: click a segment, brush, lasso, box, and "grow similar".
  - Assign a class, or split and merge segments.
  - A **segment → class mapping table** to fix whole categories at once.
  - Label in 2D on a video frame, which is propagated into 3D. The **fully manual fallback** is drawing the floor polygon and wall polylines directly in the 2D plan.
  - Every correction is stored as an edit operation, so it survives re-processing and is also collected as **training data to improve the auto-segmentation** (fine-tuning on our corrected scans).
- **Geometry editing:** move, add or delete wall segments; close holes; set door variants (open/closed/random); delete scan artefacts.
- **Race setup:**
  - **start line and finish line** (drawn in 2D or 3D; the same line for laps), driving direction, lap count;
  - **start grid** positions for N cars;
  - checkpoints/sectors for timing and reward;
  - no-go zones;
  - manual adjustment of the centreline.
- **Objects:**
  - place objects from a library (box, cone, bin, bench, opponent LEGO car, door leaf, custom imported mesh) or promote scanned clusters to objects;
  - mark objects static or movable;
  - set **randomisation ranges** (position, rotation, size, present/absent) for domain randomisation in training.
- **Surface properties per region:** friction, plus reflectivity/material for ultrasonic, ToF and camera models.
- **Accuracy target: ±1–2 cm.** Users enter a few **tape-measure check distances** (for example corridor width at 3 spots, distance between two markers), and the validator compares them with the scan and reports the error.
- **Validation:** start and finish exist, the track is closed/connected along the centreline, the corridor is wider than the car everywhere (narrow spots are flagged), there is no floating geometry, and there are no unlabelled regions in the drivable area.

**d) Image and video data for AI training** (a dataset versioned with the track)
- Frames + poses + depth are kept as a dataset. Corrected 3D labels are **re-projected back into every frame**, which gives automatically labelled segmentation masks (floor, wall, obstacle …) at no extra labelling cost. Users can fix single frames, and those fixes flow back into 3D.
- **Uses:**
  - train a small **onboard perception model** (floor/wall/obstacle segmentation or free-space estimation) for the RPi camera, exported to ONNX and benchmarked for speed on the RPi;
  - realistic sim textures (camera domain randomisation from real photos);
  - validation of the camera simulation (real frame vs rendered frame from the same pose).
- **Car videos:** recordings from the car's own camera during test drives are imported, time-synced with telemetry logs, and can be labelled the same way.
- **Export formats:** semantic masks (PNG), COCO, and point cloud labels.

**e0) Quick tracks without the full scan pipeline** (home, classroom or cardboard courses):
- **2D drawing in the track editor:** draw walls, obstacles and the start/finish line, or type in measurements; extruded to 3D and simulated immediately.
- **Quick scan with TrackScout:** one pass, geometric segmentation only, ready in minutes.
- **Mapped by the car (2D LiDAR SLAM):**
  - The real car drives the course (teleop or reactive mode) and builds an occupancy map with scan-matching SLAM (Python implementation in `raceforge.control`, using the same LiDAR driver).
  - The map uploads as a new track version that can then be edited like any other (start/finish, objects).
  - It also serves as a fallback map source for the school corridor.

**e) Procedural corridors:** the generator uses statistics from the real scans (widths, door spacing, object types) to generate realistic random training tracks. The edited real scan is the validation track.

**Unknown corridor length → designed for long corridors (> 100 m):**
- Whether markers may stay up permanently is unknown, so they are planned as **temporary**: A4 prints put up for scanning and removed afterwards. Nothing in the car's driving depends on them.
- Long, repetitive corridors make ARKit drift and confuse relocalisation. Printed **AprilTag markers every ~5–10 m** are therefore the default and act as shared anchors across passes.
- Each pass is also split into **segments of about 20–30 m**, each with its own `ARWorldMap`, and segments are chained via the markers. Pose-graph optimisation closes loops if the corridor is a loop.
- The desktop pipeline processes tracks in **spatial tiles**, and the viewer streams with LOD, so a 200 m corridor stays editable on a MacBook Air.

**f) Custom iOS capture app "TrackScout" (iPhone 17 Pro, and any LiDAR iPhone/iPad Pro).** Verdict: **yes, worth it, built in phases.**

*Why:* no existing app records everything we need in one pass and one format, supports multi-pass relocalisation, and shows our coverage guide in AR. Our app can:
- **Record everything in one session:**
  - RoomPlan parametric walls, doors, windows and objects;
  - an ARKit scene-reconstruction mesh **with Apple's built-in per-face classification** (floor, wall, ceiling, door, window, seat, table): free, good-quality first labels that feed the segmentation step directly;
  - LiDAR depth + confidence maps, RGB frames, camera poses + intrinsics, gravity and IMU.

  (iOS 17+ RoomPlan can share one `ARSession` with our own recording.)
- **Collaborative scanning:** 2–3 devices scan different corridor sections at the same time in a **shared coordinate system** (ARKit collaboration session via MultipeerConnectivity while scanning, before any race), with a shared live coverage map. Passes are merged automatically.
- **UI:** German + English; iPhone layout (iPads use the scaled iPhone UI).
- **Tests:** XCTest unit tests in CI (no LiDAR there) plus a **manual device checklist** before every app release.
- **Multi-pass projects:** create a track project, record pass after pass (walkthrough, high, low/car-height, detail, gap-fill), and **relocalise each new pass into the saved `ARWorldMap`** so all passes share one coordinate frame. It also detects AprilTag/ArUco markers.
- **Live coverage while scanning:** a coloured mesh overlay shows what is well, weakly or not yet captured (observation count, distance, angle, confidence), plus warnings for moving too fast, poor light, tracking loss or being too far from the wall.
- **Scan missions from the desktop tool:** the tool's gap list and heatmap are loaded into the app, shown in AR with arrows and suggested height and angle, and ticked off as they are filled.
- **Recording quality chosen per pass:**
  - **Maximum:** depth at 60 fps + RGB 4K@30, about 3–4 GB/min; for detail and camera/splat passes.
  - **High:** RGB 1080p, about 1–1.5 GB/min; for overview passes.
  - **Economy:** about 0.3 GB/min.

  The app shows free storage and the remaining minutes at the chosen quality.
- **Uploads over mobile data:** the app **asks** before uploading over cellular; on Wi-Fi it uploads automatically and resumably.
- **On-phone review before upload:** a 3D preview of the pass; bad segments can be discarded before uploading.
- **Pause / Continue recording:**
  - While paused, ARKit tracking keeps running but nothing is recorded, so you can wait for people to pass or step around objects without losing alignment.
  - Each continue starts a new segment within the same pass.
  - There is also a "discard last N seconds" button.
- **Supported devices:** LiDAR devices only (iPhone 17 Pro, iPhone 16 Pro, iPhone 15 Pro, iPad Pro M5). On other devices the app shows "LiDAR required" and does not record.
- **On-phone annotation:** tap to mark the start/finish line, driving direction, special obstacles and surfaces (glass, carpet). These become the first edit layer in the tool.
- **Our own format** (`.tscan`: a zip with manifest, mesh, depth, frames, poses and annotations), sent straight to the desktop tool by **local Wi-Fi upload (Bonjour discovery) or USB/AirDrop**. There is no format conversion or paid export.
- **Optional extra:** a test-drive "follow cam" mode that films the real car with poses, so videos come with ground-truth track positions for sim-to-real comparison.

*Completely free; this is a hard requirement:*
- **The app uses only free Apple frameworks:** ARKit, RoomPlan, SwiftUI, ModelIO, Network.framework and Vision. There are **no paid SDKs, no accounts, no cloud services, no subscriptions and no ads**. All data stays local and goes to the desktop tool over the local network or USB.
- **Install without the paid Apple Developer Program:** build from Xcode with a **free Apple ID (Personal Team)**. All needed capabilities (camera, ARKit/LiDAR, local network/Bonjour, file sharing) work with free provisioning.
- **Free signing limits and how we handle them:**
  - Apps signed with a free account expire after **7 days**; re-running from Xcode re-signs them in about a minute, and the README has a one-page guide.
  - There is a limit of **3 sideloaded apps per device**.
  - TestFlight and App Store distribution are not available, so every teammate who wants the app on their own iPhone builds it from the repo with their own free Apple ID on a Mac.
  - Captures can also be done with your iPhone 17 Pro alone.
- **Free fallback if the app is not installed:** the desktop tool also imports the free 3d Scanner App and Scaniverse exports. Multi-pass merging and the desktop coverage heatmap work with those too (registration via markers or ICP); only the AR mission overlay requires our app.
- **Other limits:** needs a Mac with Xcode (available). LiDAR can only be tested on the real phone, not the iOS Simulator. Effort is about 4–6 weeks AI-assisted for v1 and v2.

*Phases:*
1. **Week 1:** scan the corridor with free apps (3d Scanner App, Scaniverse) in several passes. This defines the needed data and gives test fixtures for desktop merging.
2. **Weeks 2–5: app v1:** recording, pause/continue, multi-pass + `ARWorldMap` relocalisation, RoomPlan, `.tscan` export, Wi-Fi upload.
3. **Weeks 14–15: app v2:** live coverage overlay, AR scan missions, marker detection, on-phone annotation.
4. **Week 16:** follow-cam mode.

The desktop importer supports both the `.tscan` format and the third-party formats, so the app is never a blocker.

*Stack:* Swift + SwiftUI, ARKit, RoomPlan, ModelIO (mesh export), Network.framework (Bonjour upload); `trackscout_ios/` in the same repo, with the same AI-dev rules, XCTest, and a CI build on a macOS runner.

Tracks, labels, edits and datasets are versioned like cars. **Large raw captures** (often several GB) are stored as content-addressed blobs in MinIO on the backend and cached locally on demand.

### 3. Simulate (MuJoCo)
- The vehicle is generated from the assembly (see above).
- **LEGO mechanics:** parts within a rigid group are rigid, but there is **steering play** (dead band), **gear backlash**, axle friction and tyre grip (LEGO rubber on school floor). Each is a calibratable parameter, fitted from real logs.
- **Sensors (priority order):**
  1. **EV3 ultrasonic** (multi-ray cone, 3–250 cm, dropout on soft or angled surfaces, crosstalk between several ultrasonic sensors) and the **EV3 gyro** (drift, rate limit);
  2. **2D LiDAR** (LD06/LD19/RPLidar: 360° at 5–15 Hz, angular resolution, range noise, dropouts on glass and black surfaces, motion distortion while driving);
  3. **camera** (model undecided; a **camera catalogue** with FOV, resolution, rolling shutter and distortion includes Pi Camera Module 3 / 3 Wide and generic UVC, and custom intrinsics can be set from a calibration checkerboard. Rendered from the splat or the textured scan mesh, with exposure, noise, motion blur and lens-distortion randomisation).

  Later: ToF, encoders (built in with the drive motor) and bumper. Every sensor has noise, latency, update-rate and failure models that are calibrated from real logs. The car runtime ships drivers for exactly these sensors: EV3 sensors via ev3dev, the LiDAR over USB-UART, and the camera via libcamera/OpenCV.
- **Drive (undecided, so both are supported):**
  - **EV3 Large/Medium motor** (driven by the EV3, built-in encoder, torque/speed curve from the datasheet);
  - **external DC motor + driver** (driven from the onboard board via PWM, a quadrature encoder required, parameters from the motor datasheet + gearing).

  The construct editor's device parts carry these motor models, and calibration fits them from real logs.
- **Opponents:** **older versions of our own car**, each with its own assembly version and controller, plus configurable generic opponents (slow/fast, cautious/aggressive, randomised). All start together on the start grid, and collisions are simulated. The other teams' cars are unknown, so generic opponents get **randomised physics**: size 15–40 cm, mass 0.5–2.5 kg, random speed and behaviour.
- **Unknown top speed → designed for up to 3 m/s:**
  - physics timestep 2 ms;
  - control loop configurable from 20 to 100 Hz;
  - sensor latency and LiDAR motion distortion are modelled.
  - A **speed-safety check** compares stopping distance and reaction time at the target speed against the sensor range and update rates, and warns, for example: "at 2.5 m/s the EV3 ultrasonic at 20 Hz sees obstacles too late".
- **Photorealistic camera simulation (Gaussian Splatting):**
  - A **3D Gaussian Splatting** model of the corridor is trained from TrackScout frames + poses (gsplat / nerfstudio "splatfacto", as a GPU worker job; CUDA on the Windows RTX machines).
  - Physics stays in MuJoCo; the **camera image at the car's pose is rendered from the splat** and combined with depth-correct rendering of dynamic objects (opponent cars, placed objects) from MuJoCo.
  - For RL speed, camera images are rendered at low resolution on the GPU worker in batches, with a cache of pre-rendered views along the racing line plus augmentation.
  - The browser and desktop show the splat with a three.js Gaussian-splat viewer.
  - Fallback: textured-mesh rendering when no splat exists.
- **Hardware-in-the-loop (HIL) mode:**
  - The real board + EV3 + motors run (car on a stand) while the sim provides **virtual sensor data** (LiDAR, ultrasonic, gyro, camera) over the same `RealIO` channels.
  - Real motor and encoder feedback flows back into the sim.
  - This measures real latencies, loop rates, serial timing and controller behaviour before the car touches the floor.
  - There is also a **software-in-the-loop** variant: the car_runtime runs on the real board with fully simulated I/O.
- **Viewer:** 3D view in the front-end (desktop or browser; MuJoCo WASM for interactive runs, worker-streamed for batch replays), time scaling, `RunLog` recording and replay with plots.

### 4. Train (one Gym environment `RaceForgeEnv`, three methods)
- **Observation (all four on by default, each can be toggled per job):**
  1. LiDAR in 36 sectors of 10°, giving the minimum distance per sector;
  2. localisation + racing line: lateral offset, heading error and a preview of the curvature over the next 0.5/1/2 m, plus the localisation confidence;
  3. a small camera input (64×64 image or floor/wall mask, CNN encoder);
  4. own state: speed, steering angle, yaw rate and last action.
- **Action:** steering angle and target speed.
- **AI role, compared on the benchmark:**
  - **residual RL** (the classic controller drives and the policy learns corrections, bounded);
  - **end-to-end** policies (the policy directly controls steering and throttle).
- **RL algorithms: PPO and SAC are trained in parallel** for every RL job (same observations, reward and seeds), and the benchmark picks the better one. PPO runs on many parallel CPU envs, SAC with a replay buffer on the GPU.
- **Smoothness:** a penalty for large action changes between steps plus a **steering-rate limit** matching the real steering motor. This protects the LEGO gears and transfers better to the real car.
- **Stopping:** **early stopping** after 30 min (configurable) without benchmark improvement, plus a **maximum runtime per job** (default 12 h).
- **Real-car A/B sessions:** guided sessions in the Live tab. The tool says which bundle to deploy next, alternates controllers (for example 5 laps each, randomised order), collects lap times and incidents, and shows the winner with statistical confidence.
- **Network architecture configurable per job:** MLP, with an optional CNN encoder for the camera and optional LSTM/GRU memory.
- **Sim-to-real strategy:** **calibrate first** (system identification from real logs and the wizards), then randomise **around the calibrated values**. Offline RL on real logs adds fine-tuning.
- **Reward, matching the race format (the first car to complete N laps wins):**
  - centreline progress per time, a big bonus for finishing N laps fast;
  - heavy penalties for getting stuck/DNF and for situations that would need a manual intervention;
  - smaller penalties for wall contact and collisions.

  The weights are configurable per driving-style profile.
- **Benchmark score:** expected time to N laps, including the probability of DNF/interventions (Monte Carlo over randomised conditions), with **3–5 opponents** (4–6 cars in the field) and a crowded start grid.
- **Domain randomisation:** track, friction, mass, sensor noise, latency and opponents, set through **presets (light/medium/strong) + an expert mode** for single parameters.
- **Benchmark set:** **50 held-out procedural corridors + the scanned corridor + its race variants**. Sim races default to **3 laps**.
- **Procedural corridors:** starting values are **derived from the scans**, adjustable with sliders (width, curvature, door density, object density, niches).
- **Classic tuning:** Optuna optimises state-machine and PID parameters. The output is params JSON, which also works as an EV3-only fallback.
- **RL:** PPO/SAC via SB3 with vectorised environments, checkpoints and TensorBoard; exported to ONNX.
- **Training speed, two tiers:**
  - **Standard:** CPU-parallel MuJoCo environments on the workers (overnight runs of 4–12 h are fine).
  - **Fast option:** GPU-parallel physics (**MuJoCo Warp/MJX** on the RTX workers, thousands of envs) for sub-hour iterations. It uses the same MJCF; non-camera sensors only at first.
  - The GPU tier comes after the CPU tier works and is chosen per job in the launch dialog. MJX/JAX GPU on Windows needs WSL2, so the worker supports a WSL2 backend for this job type.
- **Imitation:** demonstrations from the sim (keyboard/gamepad), the tuned controller or real drives; behaviour cloning and DAgger; exported to ONNX.
- **Offline RL:** IQL/CQL trained on **all** real and sim logs, including bad drives, so the daily test drives keep producing training data. It is exported to ONNX like the others.
- **Curriculum:** difficulty levels (track complexity, number and aggressiveness of opponents, sensor noise) are **chosen manually** per training job; there is no automatic progression.
- **Ghost opponents:** recorded real drives can be replayed as opponent cars (time-indexed trajectories with collision bodies) alongside simulated opponents.
- **One strategy per bundle:** no strategy selection on the car at the start.
- **Benchmark:** every controller runs on the same held-out tracks and the scanned corridor, and results go to the leaderboard.

### 5. Deploy and sim-to-real
- **Export bundle:** controller + params/ONNX + derived vehicle data + version hashes.
- **Board setup, both ways supported:**
  - **a prebuilt SD image** (RaceForge CI builds it with OS, car_runtime, drivers and race-mode hardening; flash it with Raspberry Pi Imager or balenaEtcher) for race and spare cars;
  - **a `raceforge-car` install script** on standard Raspberry Pi OS/Armbian for tinkering boards.

  There is no boot-time requirement.
- **Recording:** every drive records **compressed H.264 camera video** (low resolution, time-synced to the MCAP log) in addition to sensor data.
- **Automatic upload:** after a test drive or the race, as soon as the car is back in test mode with network access, logs and videos upload to the backend automatically, resumable and with progress shown in the Live tab.
- **No EV3-only fallback:** if the board fails, the spare car is used instead.
- **Runtime architecture: a Rust core + Python controllers.**
  - The time-critical **core is written in Rust**: sensor drivers/reading (EV3 serial, LiDAR UART, camera capture), the timing loop, the watchdog, motor output, race-mode enforcement, emergency stop/speed limit, logging and the telemetry server.
  - **Controllers stay in Python** (team SDK), embedded via PyO3 or connected via shared-memory IPC with a strict per-tick deadline.
  - You are not a Rust reviewer yet, so the Rust core gets **extra safeguards**: property-based tests, a HIL test suite, fuzzing of the serial/LiDAR parsers, and `clippy -D warnings` + `cargo deny` in CI. It is always in the "your approval required" path.
- **Watchdog:** if the controller crashes or misses its deadline (hang), the Rust core **stops the car immediately** (motors off, brake), logs it, and shows the fault on the EV3 display.
- **Sensor failure:** the behaviour is **configurable per sensor**: *critical* (stop) or *optional* (continue degraded at reduced speed with the remaining sensors), with the fault on the display and in the log.
- **Logging on the car:** **reduced resolution** (LiDAR as compact sectors/decimated points, low-resolution H.264). **Logs are kept until their upload is confirmed**, with a warning when the SD card is getting full.
- **Session recording starts automatically** when driving begins (movement or start button) and ends at standstill.
- **Getting the bundle onto the car, both ways supported:**
  - **One-click wireless deploy** in test mode: the car's board is on Tailscale or the team hotspot; RaceForge pushes the bundle, restarts the runtime and verifies the hash.
  - **USB cable / SD card fallback:** `raceforge deploy --usb` or a copy to the SD card, with the hash verified at runtime start.
  - Any bundle can be deployed at any time. Arming race mode disables all radios.
- **Car runtime:** the onboard board runs `raceforge.control` through `RealIO`. The EV3 boots ev3dev from microSD (the team may choose its firmware) and handles the steering motor and LEGO sensors over **USB serial**. Everything is logged on the car.
- **One or two EV3 bricks** (the number is undecided): the assembly, the sim and the runtime all support 1–2 EV3s. Each device is mapped to a brick and port in the construct editor, and the runtime runs one serial link per brick with time-synchronised readings.
- **Guided calibration wizards** (Live tab, results stored per car, used by both sim and runtime):
  1. **Steering:** drive 2 m straight, measure drift and correct the steering zero point; full lock left and right to measure turning radius, steering ratio and play.
  2. **Sensors:** place the car at a known distance (for example 30 cm) in front of a wall to get the offset per distance sensor; parallel to the wall to get the LiDAR yaw offset; at standstill to get the gyro bias.

  Everything else (rolling friction, motor curves, camera intrinsics fallback) is estimated automatically from normal drive logs.
- **Log import and calibration:** a real log is replayed against the sim with the same commands, and Optuna fits friction, motor curve, steering play and sensor noise.
- **Real demonstrations:** in **test mode**, the car can be driven with an **Xbox controller** (browser Gamepad API) or a **touch joystick** on a tablet or phone from the Live tab (over Wi-Fi/BT, with dead-man switch and speed limit). All sensors and commands are recorded as demonstrations for imitation learning. Teleop is **technically blocked in race mode**: the runtime refuses any command input once race mode is armed.

### 5a. Controller code (written by the car team)
- Controllers are **versioned objects in the RaceForge backend** (lineage, deploy and benchmarks reference them) **and** mirrored to a **separate private GitHub repo** for the car team, so other teams can't see the driving strategy (normal git workflow, PRs and Copilot).
- **Two-way sync:**
  - pushes/merges to the repo's main branch create a new controller version in the backend (via a webhook or polling job);
  - edits made in RaceForge's built-in code editor (Monaco) are committed to the repo on a branch with a PR.
- Each controller version stores its commit hash.
- The car team's repo uses the same gates: ruff, pyright, controller unit tests, and an automatic sim smoke run on PRs via GitHub Actions.

### 5b. Driving stack on the car (shared by sim and car, in `raceforge.control`)
- **Layered architecture:**
  1. **Safety and reactive layer:** wall, obstacle and opponent avoidance from live LiDAR, ultrasonic and camera data. It **always has priority**.
  2. **Localisation:** a particle filter (AMCL-style) matches the 2D LiDAR scan + gyro + wheel odometry against the occupancy map of the scanned and edited track. It reports position, heading and **confidence**.
  3. **Planning:** a **racing line** (minimum-curvature line from the centreline and width profile, computed in RaceForge and shipped in the deploy bundle) plus local re-planning around obstacles and opponents.
  4. **Control:** a path follower (pure pursuit or Stanley) + speed profile, or an RL/imitation policy that gets the same inputs.
- **Fallback:** if localisation confidence drops (unknown changes, kidnapped robot), the car switches automatically to **purely reactive driving** (wall following/centering) until it is confidently relocalised. It still drives on live sensor data alone, which keeps the rule "decisions based on current sensor data" safely satisfied.
- **Interventions and recovery:**
  - **Resume button** (EV3 key/touch sensor): after a manual repositioning, it resets the state machine and continues.
  - **Automatic relocalisation:** after being picked up or moved (detected by the IMU), a global particle filter re-initialisation over the map, with a confidence threshold before speeding up again.
  - **Auto-recovery:** detects being stuck or crashed (encoder vs commanded speed, bumper, IMU) and runs back up → re-orient → avoid before a human needs to step in.
  - **The sim includes all of these:** random kidnapping/repositioning, stuck situations and collisions, so tuned controllers and RL/imitation policies learn to handle them.
- **Configurable driving style:** a style profile (defensive ↔ aggressive) sets overtaking margins, minimum distances, blocking behaviour, and RL reward weights for opponent interaction. The benchmark compares profiles against opponent sets made from older car versions and generic opponents, including adversarial ones.

- **Race-day changes (bags, open doors, moved bins):** handled by **robustness through training**, without a re-scan.
  - Heavy randomisation of objects and doors on the track during training.
  - Localisation tolerates unmapped objects (outlier-robust scan matching that ignores unexplained LiDAR returns).
  - The reactive layer handles anything in the way.
  - The benchmark includes "changed corridor" variants of the scanned track.

### 5d. Safety during test drives (people in the school corridor)
- **Hardware emergency stop on the car:** an easy-to-reach switch cuts motor power. The runtime detects it through a GPIO/EV3 sensor, logs it and refuses to drive until reset.
- **Software stop** in the Live tab plus a **dead-man switch** during teleop (test mode only).
- **Test-mode speed limit:** configurable per session. The **default is the car's maximum (no limit)**, as decided, including for teleop. The hardware emergency stop, software stop and teleop dead-man switch are always active.

### 5e. Battery and power model
- Battery parts in the construct editor carry capacity, nominal voltage, internal resistance and discharge curve.
- The sim models **voltage sag under load → lower motor power**, **runtime/remaining capacity** over a race, and **brownout risk** for the onboard board (warning if peak motor current could pull the board's supply below its limit).
- The Live tab shows real battery curves, and calibration fits the model to them.

### 5f. Multiple physical cars (fleet)
- RaceForge manages several physical cars. Each has an ID, a name, the assembly version it is built as, its own calibration (sensor offsets, motor curves, steering play) and its own logs.
- Deploy targets a selected car. Benchmarks and the sim can use per-car calibrated models, so a spare car or a variant can be compared fairly.

### 5c. Race start and "Race Control" (start signal from the teacher)
- **Start methods on the car (all configurable, several can be active at once):**
  1. **Button on the car** + configurable countdown.
  2. **Start signal detection:** the car recognises an optical signal (start light) with the camera or EV3 colour/light sensor and/or an acoustic signal (tone sequence) with a microphone. False starts are filtered by requiring a specific pattern.
  3. **External start interface for a teacher-operated system:** see the *Race Control* app below.
- **No-radio constraint:** the external start signal must not use wireless links, so the interface is **optical and/or acoustic, plus an optional wired trigger**. The wired trigger is a pull-away plug at the start grid (like a model-rocket launch cable): all plugs connect to one start box and release at the same moment.
  - **Ask the teacher** whether optical or acoustic signals count as "wireless". Optical/IR could be read as wireless, so the wired trigger is the safe default (**offen**).
- **"Race Control" app:** part of RaceForge, opened in a browser or the desktop app by the teacher.
  - Start sequence (countdown, start light shown full-screen on a laptop/beamer and/or on an external LED lamp, start tone through speakers).
  - Optional hardware start box (ESP32/Arduino with LED lamp, buzzer and wired trigger outputs, about 10–20 €).
  - Race timer, lap counter (manual tap or via the start/finish camera) and incident log.
  - **False-start detection:** the car runtime logs its start time vs the signal time, and Race Control marks false starts.
- **Race-day kit and flow:**
  - a laptop with RaceForge (pre-checks; logs read out by cable after the race);
  - a **guided race-day checklist** in the app: batteries charged and measured, sensor self-test and calibration, bundle hash verified (warning if untagged), radios off/removed, start mode armed, emergency stop tested;
  - a **spare car** (fleet) prepared with the same bundle;
  - an **iPad as Race Control** (tablet-optimised Race Control view: start sequence, timer, lap counting).
- **Race Control is for our team only** (tests, timing, our own rankings). The car-side **external start interface** is documented as a simple specification (light colour/pattern, tone frequencies/sequence, wired trigger: dry contact opens = start), so the teacher's system, or our start box operated by the teacher, can start our car together with the others.
- **Sim:** the start is simulated: all cars react to the start event with their individual detection latency, so start reactions can be tuned.

### 6. Versioning and sharing (team workspace)
- **Workspace:** one team workspace on the backend (§9), holding parts, assemblies, tracks, captures, controllers, datasets, runs, models and logs. Each desktop app keeps a local offline copy of what it uses.
- **Versions:**
  - Creating a version of an assembly, custom part, track or controller makes it immutable, with a content hash, a **semantic version + optional name** (for example `car-a 1.4.0 "lighter steering"`), the author, the time and a message.
  - **Variants can be merged submodel by submodel:** for each submodel, pick which variant it comes from; the 3D diff shows the result before you confirm.
  - **Collaboration lock timeout:** **5 min** of inactivity, with a warning 1 min before.
  - **Deleting** moves items to a **trash for 30 days** (restorable) before they are deleted for good (admin only).
  - Versions can be tagged (for example `race-day`) and branched into variants.
- **History tab:**
  - version timeline per object;
  - **visual 3D diff** of two car versions (added parts green, removed parts red, moved parts yellow), plus a diff of the derived values (mass, CoG, budget);
  - restore.
- **Lineage:** every run, training job, benchmark and real log records the exact assembly, part, track, controller and code versions, so results are reproducible and never mixed up.
- **Built-in experiment tracking:**
  - every run stores hyperparameters, reward weights, lineage, learning curves (TensorBoard event data, ingested by the backend) and final metrics;
  - a **compare view** overlays curves of selected runs and diffs their settings;
  - the **leaderboard** ranks controllers × car version × track;
  - everything is self-hosted, with no MLflow or W&B.
- **Sharing:**
  - Everything is shared automatically through the backend: live presence, submodel locks, and background sync with an online/offline status.
  - `.raceforge` bundles (zip containing the car with all referenced custom parts, optionally the track, controller and results) for sharing outside the backend, such as with teachers or other teams.
  - Copy-able references of the form `car-a@v12`.
- **Deploy policy:**
  - **any version can be deployed to any car at any time, by every member**; there are no deploy gates;
  - the bundle records exactly which versions it contains;
  - the race-day checklist **warns** if the race bundle is not tagged.
  - Runtime safety (emergency stop, test-mode speed limit, race-mode radio check) always stays active regardless.
- **Multiple workspaces:** any number of workspaces (for example "Season 1", "Experiments", "Race"). Objects can be copied or promoted between workspaces with their lineage, and members can access all workspaces of the team.
- **Project archive export:** one click exports a workspace at a cut-off date (all versions, reports, videos, logs, models) as a folder/ZIP with a browsable HTML index for the school documentation. After the race the project is **archived**, so the code-quality bar stays "clean and tested", without long-term maintenance features.

### 7. Live screen (real car)
- **Car side:** `car_runtime` streams telemetry frames over the active link (see *Wireless links* below), at up to 20 Hz on Wi-Fi. Each frame contains:
  - mode, state-machine state and faults;
  - all sensor readings, and commanded vs measured steering and speed;
  - IMU heading and odometry pose;
  - controller output and confidence, loop rate and latency;
  - EV3 and RPi battery voltages, CPU temperature and load;
  - log status, and the bundle and car versions.
- **Live tab:**
  - health indicators;
  - top-down view with live distance rays on the scanned track map, with position estimate and trail;
  - rolling plots, state timeline, lap timer and event list;
  - telemetry rate **configurable per session** (default 20 Hz);
  - **live camera stream with overlays** in test mode (WebRTC with **adaptive** resolution and frame rate, MJPEG fallback): floor/wall segmentation, detected obstacles and opponents, racing line, localisation confidence;
  - a stop button (test mode only);
  - an optional sim "ghost" running the same controller;
  - **policy explainability** (live and in replay):
    - per-input saliency (which LiDAR sectors and sensors drove the decision);
    - policy outputs with uncertainty;
    - active state and layer (reactive / localised / fallback);
    - side-by-side "what would the classic controller do".
- **Wireless links (test mode only):** one `Transport` interface; the link manager connects over whatever link is available, shows link quality (RSSI, latency, packet loss, active link) and **fails over automatically** in this order: cable → Wi-Fi → ESP-NOW → Bluetooth.

  | Link | Practical range (indoors) | Bandwidth | What it carries | Extra hardware |
  |---|---|---|---|---|
  | Cable (USB/Ethernet) | 1–5 m | very high | everything | none |
  | **Wi-Fi** (RPi built-in; car hotspot or a team travel router placed in the corridor) | 20–50 m per access point; extend with a 2nd AP/repeater | very high | full 20 Hz frames, camera preview, file transfer, deploy | optional travel router (~20–30 € used) |
  | **Bluetooth Classic / BLE** (RPi built-in; EV3 BT for a direct EV3 status link) | ~10 m (BT 5 up to ~30 m) | low–medium | reduced frames ~5 Hz, commands; EV3-direct fallback if the RPi crashes | school BT dongle for the laptop |
  | **ESP-NOW** (ESP32 on the car via USB ↔ ESP32 on the laptop) | 100–200 m | ~250 kB/s | near-full frames at 10–20 Hz without a router | 2× ESP32 (~5 € each) |
  **Decision: Wi-Fi + Bluetooth + ESP-NOW** (+ cable). HC-12/SiK/LoRa and LTE are **dropped**.

  **School Wi-Fi has a captive portal** (login page at `http://10.10.0.2/captiveportal/`, success page `…/cp_logon_done.html`):
  - The car image includes a **captive-portal login daemon**. It detects the portal, logs in with credentials from the boot partition config (never in the repo), re-authenticates when the session expires, and shows the portal status on the EV3 display.
  - School Wi-Fi often **blocks traffic between clients** (client isolation). The laptop then can't reach the car directly, and the **< 100 ms live latency target** would only be possible over the backend relay (slower).
  - For low-latency tests the plan therefore uses the **team travel router** (it logs into the portal itself and gives car and laptops their own LAN) or **ESP-NOW**. School Wi-Fi directly is for uploads and deploys.
- **Bandwidth tiers:** frames are encoded in three tiers. The link manager picks the tier that fits the link automatically:
  - **full:** all fields at 20 Hz;
  - **reduced:** key sensors, state and battery at 5 Hz;
  - **minimal:** state, faults, battery and lap at 1 Hz.
- **Commands:** stop and mode switch work on every link, with acknowledgement and retries.
- **Race constraint:**
  - **Test mode** streams over any of the links above.
  - **Race mode** turns the radios off and only logs on the car. The EV3 LCD and LEDs show the state locally.
  - **Race-mode radio check:** `rfkill` blocks Wi-Fi and Bluetooth on the RPi, and EV3 Bluetooth is turned off. The runtime **refuses to start the race** if any wireless interface is up or a radio USB device (ESP32, HC-12/SiK/LoRa adapter, BT dongle) is still plugged in, and shows the reason on the EV3 LCD. The add-on radio modules go on removable plugs so they can be physically pulled off before the race.
  - After the race, the log opens in the same screen in **replay mode** with a timeline scrubber.
- **Customisable widget layout:** plots, map, camera, status and state diagram can be arranged freely, and layouts are saved (for example "Teleop", "Analysis").
- **Live state-machine diagram:** generated from the classic controller code; the active state lights up and transitions animate.
- **Run comparison in replay:** two or more drives synchronised, with a ghost car on the map, overlaid plots and **per-sector time deltas** as in motorsport.
- **Test-drive notes:** text notes and tags are set on the timeline during a drive (for example "scrapes wall at door 3"), shown in replay and reports, and searchable.
- One car per Live view; there is no multi-car live screen.
- MCP gets read-only `live_status` and `live_snapshot` tools.

### 8. MCP server (AI access)
- **First-class clients:** **Claude Code / Claude Desktop** and **Perplexity** (Mac app, local MCP) are tested in CI or by hand on every release. Cursor and VS Code/Copilot should work through the standard protocol but are not release-tested.
- **Start it with** `raceforge mcp` (stdio) or `raceforge mcp --http --port 8765` (localhost by default). Config snippets for Claude Desktop/Code, Cursor and Perplexity go in the README.
- **Tools:**
  - *Parts and construct:* `search_parts`, `get_part`, `import_custom_part`, `list_assemblies`, `get_assembly` (summary + derived data), `add_part` / `connect_parts` / `remove_part` / `move_part`, `apply_quickstart`, `validate_assembly` (rules + budget), `get_bom`, `render_assembly` (PNG).
  - *Capture and track:* `list_tracks`, `import_capture`, `process_capture` (job), `list_segments` (with class and confidence), `relabel_segments`, `set_start_finish`, `place_object`, `set_surface`, `validate_track`, `render_track` (PNG, 2D/3D), `export_dataset`, `generate_corridor`.
  - *Simulate:* `run_simulation` returns metrics, a log ID and an optional image.
  - *Train:* `start_training`, `job_status`, `stop_job`, `run_benchmark`.
  - *Logs:* `get_run_summary`, `compare_runs`, `calibrate_from_log`.
  - *Versioning:* `list_versions`, `diff_versions`, `tag_version`, `export_share_bundle`.
  - *Deploy:* `export_bundle` writes to disk only.
  - *Live:* `live_status`, `live_snapshot` (read-only).
- **Resources and prompts:** resources include `raceforge://assemblies/{id}`, `raceforge://parts/{id}`, `raceforge://tracks/{id}`, `raceforge://runs/{id}/summary` and `raceforge://rules`. Prompts include "design a car within budget" and "diagnose the crash in curve X".
- **Long jobs:** run asynchronously with job IDs and progress notifications.
- **Controller code via AI:** with an *edit*-scope token, MCP may **open PRs in the private controller repo** (new controller or changes). A human always merges.
- **Autonomous overnight AI experiments:**
  - An AI agent (Claude via MCP, started by a scheduled task) may form hypotheses, start **sim/training/benchmark/design-optimisation jobs** on idle workers, and post a morning report to Discord (what was tried, results, recommended next steps).
  - **Hard limits enforced by the backend:** a dedicated "AI agent" API token with a nightly GPU-hours budget, a maximum number of concurrent jobs, allowed job types only (no deploy, no deletes, no tags), and every action recorded in the audit log.
- **Safety:** the server never pushes to the real car or controls it. There is a `--read-only` flag, and every call is logged and recorded in history with the author "AI via <client>".

### 9. Backend (hybrid: central server + local engines + distributed compute workers)
**Hardware:**
- **Proxmox server** (2× Xeon E5-2680, 32 threads, 180 GB RAM, fast SSDs) runs the backend.
- **Worker pool:**
  - **RTX 4070 laptop** (32 GB): main GPU worker;
  - **RTX 3070 PC**: second GPU worker, always-on candidate;
  - **2× gaming laptops**: GPU workers when available;
  - **2× MacBook Air M2**: Apple MPS for small and imitation training and segmentation (fanless, so lower sustained power); they are also the build machines for TrackScout;
  - **ThinkPad**: CPU sims and benchmarks.
- **The backend never computes.** Training, simulation batches, benchmarks and scan processing/merging run **only on workers**. Jobs wait in the queue until a matching worker is available.

**Services on Proxmox** (one Debian VM with Docker Compose; snapshots and backups through Proxmox):
| Service | Tech | Purpose |
|---|---|---|
| API server | FastAPI (same `raceforge.api` code) | REST + WebSocket for desktop apps, TrackScout, workers and MCP |
| Database | PostgreSQL | Users, objects, immutable versions, lineage, runs/leaderboard, jobs, workers |
| Object storage | MinIO (S3-compatible), content-addressed | Scans, videos, meshes, datasets, checkpoints, ONNX models, logs (TBs on the SSDs) |
| Queue / pub-sub | Redis (or NATS) | Job queue, worker heartbeats, live-telemetry fan-out, collaboration presence |
| Collaboration | Yjs/CRDT sync server (`pycrdt-websocket`) | Live multi-user editing of cars and tracks |
| Remote MCP | Streamable HTTP MCP endpoint with token auth | AIs can use the team's shared data |
| Reverse proxy | Caddy (TLS) | Single entry point |

**Compute workers ("train locally or on a selected remote machine"):**
- **The worker is built into the desktop app** (one installer; turn it on in the settings and it runs as an opt-in background service). The same worker is also available as a headless CLI from the same release.
- **Install and register:** `raceforge worker` installs on any machine (Windows, Linux or macOS) and registers with the backend using a token. It reports CPU/RAM, GPU/VRAM (CUDA or Apple MPS), OS and installed versions, and sends heartbeats. The owner sets availability (always / only when idle / schedule / paused) and resource limits.
- **Connection:** workers **connect out** to the backend (pull model), so laptops behind home or school NAT need no port forwarding.
- **Launch dialog:** choose **This machine**, a **specific worker** (for example "RTX-4070-Laptop"), or **Auto** (best free machine that meets the requirements such as GPU ≥ 8 GB). The dialog shows the queue, ETA and live load of each worker.
- **Job spec:** references the exact versions (car, track, dataset, controller config, code version) by hash. The worker downloads **only missing blobs** from MinIO into a local content-addressed cache, so the training data is hosted on the backend and fetched when needed.
- **Failure handling:** a failed job (crash, laptop closed) is **automatically retried up to 2×** from the last checkpoint, preferring another worker. After that it is marked failed and a Discord notification goes out.
- **Tokens:** API tokens are valid **until revoked**, with an optional expiry; the admin sees and can revoke all of them. **MCP tokens have scopes:** *read*, *sim + training*, *edit*. Delete, tag and deploy are never possible via MCP.
- **Isolation:** jobs run as an **isolated process**: a separate OS user/work directory, no access to the owner's home directory, network limited to the backend, and only code from authenticated team members.
- **Local cache:** content-addressed with LRU eviction; the size is configurable (**default 50 GB**).
- **Priorities:** users choose normal or high. "Race-critical" priority and reordering other users' jobs are **admin only**.
- **API:** REST (OpenAPI → generated TS and Python clients) + WebSocket for live telemetry, job logs and collaboration.
- **Reproducible environment:** the worker runs the job in an environment pinned to the job's code version (uv lockfile; Docker+NVIDIA on Linux as an option).
- **During and after the job:**
  - logs, metrics and TensorBoard scalars stream live to the backend;
  - checkpoints upload periodically, so a job **resumes on another worker** if a laptop goes offline;
  - final artifacts (models, ONNX, reports) are uploaded and lineage is recorded.
- **Local jobs** use the same pipeline and are registered in the backend too, so results always end up in the shared registry.

**Browser access: full app without installing.**
- The backend also serves the same React front-end at the Cloudflare/Tailscale URL, so teammates can use RaceForge in any browser.
- **Interactive simulation in the browser** runs client-side with MuJoCo's WebAssembly build, using the same MJCF the Python side generates.
- Editing, track editing, live screen, history, reports and job launching all work in the browser.
- Light request work in the backend (validation, derived values, rule checks, report rendering) counts as API handling, not compute jobs. **Heavy work still always goes to workers** (training, batch sims, benchmarks, scan processing).
- The desktop app (Electron) adds offline mode, the local engine and local jobs.

**Local-first desktop:** each app has a local SQLite + blob cache, so it can edit, simulate and run small trainings offline. It syncs versions when online, and CRDT edits merge on reconnect.

**Versioning moves into the backend:** immutable versions live in Postgres + MinIO, replacing git/LFS for data. Git/GitHub stays for **source code only**. `.raceforge` bundles remain for export.

**Live telemetry relay:** the laptop connected to the car (or the car itself via Wi-Fi) forwards frames to the backend. Every teammate, and AI via MCP, can watch remotely, and the stream is stored as a log automatically.

**TrackScout** uploads scans straight to the backend (MinIO). Processing and merge jobs run on a worker; GPU workers are preferred for SAM 2 / segmentation.

**VM sizing and operations:**
- **One Debian VM with Docker Compose** gets **20 vCPU, 100 GB RAM and 1–2 TB SSD**.
- **Updates are manual:** `raceforge-admin update <version>` takes an automatic Proxmox snapshot, pulls the pinned ghcr.io images, runs DB migrations and does a health check, with one-command rollback.
- **Retention: keep everything.** Disk-usage warnings fire at 70/85/95 % (status page + Discord); expand the disk or prune manually when needed.
- **Monitoring:** a **status page in RaceForge** (admin) shows service health, CPU/RAM/disk of the VM, DB/MinIO size, queue length, and per-worker online state, GPU utilisation and current job. There is no separate Grafana.
- **Email (SMTP)** for invites, password reset and optional notification digests, through a mailbox on your domain or a free SMTP relay.

**Notifications:**
- **In-app notification centre** (desktop and browser).
- A **Discord** webhook/bot posting to the team channel. Example messages: "PPO run #42 done: 96 % completion on corridor@v7", "Worker RTX-4070 offline", "Test drive: collision at sector 3", "Scan pass processed: coverage 91 %".
- Each user picks which events they want.

**School network:** Tailscale and HTTPS work on the school Wi-Fi with internet in the corridor, so test days run online. Offline mode stays as a safety net.

**Availability:** the server is online 24/7 with ≥ 50 Mbit/s upload, so remote use via Tailscale or Cloudflare is the normal mode. Workers still cache datasets locally so each blob crosses the internet only once.

**Raw capture storage ("in the database"):** PostgreSQL is the single source of truth (captures, passes, segments, metadata, versions, references, checksums). The bytes live in MinIO on the same Proxmox server and are fully managed by the backend; users and clients never deal with MinIO directly. Uploads are chunked and resumable, and every blob is checksum-verified.

**Network access: Tailscale *and* Cloudflare Tunnel at the same time (yes, this setup works):**
- **Tailscale** (free Personal plan, **only your account**) is the private, fast path for **your devices**: your laptops/PC, the RTX workers you own, admin/SSH and large blob transfers. No ports are opened.
- **Teammates** (and workers on their machines, TrackScout on their phones) always use the Cloudflare path. All large transfers are chunked and resumable, so that is fine with ≥ 50 Mbit/s upload.
- **Cloudflare Tunnel** (`cloudflared` in the VM, free) is the **public HTTPS path** on your existing domain (for example `raceforge.<domain>`) for teammates without Tailscale, the browser, and remote MCP clients. Cloudflare WAF and rate limiting sit in front.
- **Login uses our own backend accounts:**
  - you, as admin, invite users with one-time invite links;
  - passwords are hashed with argon2;
  - **TOTP 2FA** is optional and recommended because the backend is public via Cloudflare;
  - sessions use refresh tokens;
  - roles: **admin and member.** Members can edit, train, scan and run tests. Only the admin can tag race versions, delete data, and manage users, workers and backups. Every change is recorded in an **audit log**.
- Workers, TrackScout, the CLI and MCP clients use **revocable per-device API tokens** created in the account settings.
- Cloudflare Access can be enabled later as an extra outer layer.
- **The same backend sits behind both;** Caddy routes `raceforge.<tailnet>.ts.net` and the public hostname to the same API. The desktop app, worker and TrackScout **pick the endpoint automatically**: Tailscale if reachable, otherwise Cloudflare. Endpoint health shows in the status bar.
- **Cloudflare limits handled by design:**
  - The free plan caps a single request at **100 MB**, so all uploads use **chunked/S3-multipart uploads (≤ 50 MB parts)**.
  - Large downloads (datasets, scans) prefer Tailscale; through Cloudflare they are resumable ranged downloads.
  - WebSockets (collaboration, telemetry, job logs) work through the tunnel.
- **Domain:** the team's existing domain, managed in Cloudflare DNS.

**Collaboration model: live presence + per-submodel locks:**
- Everyone sees changes live: other users' selections, viewport position and the edits stream in.
- Starting to edit a **submodel** (car: steering, chassis, drive, sensor mast; track: an area or layer such as labels, race setup or objects) takes a **lock**. Locked submodels show who is editing and are read-only for others.
- Locks expire on inactivity or disconnect and can be force-released by an admin.
- Offline edits are saved as a **branch version**. On reconnect they merge automatically if no one else touched the same submodels; otherwise a side-by-side 3D diff decides.
- The CRDT layer (Yjs) handles only presence and live streaming of the locked owner's edits. This removes the hard concurrent-3D-merge problems.

**Worker environment (RTX 4070 laptop: Windows, native):**
- The worker runs as a tray app or Windows service. Each job runs in a **uv-managed, lockfile-pinned venv per code version** (cached and reused) with CUDA PyTorch.
- No Docker is required. Linux and macOS workers use the same mechanism, with Docker as an optional mode on Linux.
- **Default policy: opt-in per machine, idle only.**
  - The owner explicitly enables the worker.
  - Jobs start only when the machine is idle and on AC power.
  - Jobs are **paused (checkpoint) as soon as the user is active** or a game or other app uses the GPU, and continue on the same or another worker.
  - The owner can override this with "always", "schedule" or "paused".

**Backups: Proxmox snapshots + offsite:**
- Nightly Proxmox backup of the VM (Proxmox Backup Server or vzdump).
- **restic** sends encrypted, deduplicated backups of a Postgres dump + MinIO buckets offsite to a **teammate's PC** (restic REST server or SFTP over Tailscale), every night. Missed backups trigger a notification.
- Policy: keep 7 daily, 4 weekly and 6 monthly backups.
- Large regenerable artifacts (intermediate checkpoints, caches) are excluded; raw scans, versions, final models and race logs are always included.
- A monthly **restore test** goes into a scratch VM.

**Known risk:** the Xeon E5-2680 (Sandy Bridge) has AVX but **no AVX2**. Some current binary wheels (parts of PyTorch, Open3D and possibly MuJoCo builds) assume AVX2. Check this in week 1 on the Proxmox VM. GPU and ML work goes to the RTX laptop anyway; the server mainly needs Postgres, MinIO, API and MuJoCo CPU sims.

### 9b. Reports (for the school project)
These are generated from backend data as **PDF** (WeasyPrint, from HTML templates) and **Excel/CSV** (openpyxl), in English or German:
- **Budget/BOM report:**
  - every part of a tagged car version with source (school kit / own / bought), quantity, used price with its source (eBay/Willhaben link and date), 3D-print filament cost, and the total vs **200 €**;
  - school-provided EV3 parts are listed at 0 €.
- **Test and race reports:** one per sim benchmark, real test drive or race log, containing:
  - lap times, completion, collisions and incidents;
  - trajectory on the track map, and sensor/steering/speed plots;
  - the state-machine timeline;
  - sim-vs-real comparison where available;
  - the exact versions used (lineage).
- **Version changelog:** what changed between two versions of a car, track or controller (added/removed/moved parts with a 3D diff snapshot, derived-value deltas, parameter changes, linked runs), and a full history for the project documentation.
- **Branding:** team name, logo and school, set in the workspace settings, appear on every report.
- Reports can be created from the UI, the CLI (`raceforge report …`) and MCP (`generate_report`), and are stored as versioned artifacts.

### 9c. Additional features and details
- **Car network in test mode:** the school Wi-Fi type is unknown, so the SD image ships **three network profiles**: the team travel router in the corridor, a phone hotspot, or school Wi-Fi directly (WPA2-Personal/Enterprise). They are switchable from the EV3 menu or a config file on the boot partition.
- **Time on the car:**
  - an optional **RTC module** (Pi 5 RTC header + battery, ~5 €) when present;
  - otherwise a monotonic clock, with timestamps **corrected automatically** at the next connection (sync offset stored in the log).
- **Lighting randomisation for the camera:**
  - constant artificial light + **daylight through windows** (back light, sun patches, time of day);
  - **glass doors/reflections** as a surface class that disturbs both LiDAR (dropouts, ghost points) and camera (reflections) in the sim.
- **Comments:** comments anchored in 3D on parts, submodels and track locations, with threads, @-mentions and notifications (in-app + Discord).
- **3D printing:** STL/3MF export per part version only; there is no print queue.
- **Shopping list:** hardware to buy (board, LiDAR, batteries …) with link, price, who buys it and status (planned/ordered/arrived). Arrived items flow into the budget/BOM automatically.
- **Race variants per track:** one geometry with several race setups (driving direction, start/finish positions, doors open/closed, lap count). Training and benchmarks can run over all variants.
- **Track export:** mesh/point cloud (OBJ/PLY/E57), 2D plan (SVG/PDF/PNG with start/finish and racing line), and a ROS map (PGM + YAML).
- **Presentation video export:** any replay (sim or real) renders to **MP4** with telemetry overlay, map, camera image and notes, as a worker job.
- **Public 3D showcase link:** a read-only interactive viewer of a car version (rotate, exploded view, submodels) shared via Cloudflare **without login**. It is an explicit per-version share with a revocable link; nothing else becomes public.
- **Dashboard (start page):**
  - the current best setup (car × controller × track + score);
  - running jobs and worker availability;
  - latest test drives with notes and incidents;
  - countdown to the race and budget status.
- **Controller editing:** RaceForge opens controller files in **VS Code** (with Copilot); the sim hot-reloads on save. The in-app Monaco editor is only for quick edits.
- **On-car feedback:**
  - the **EV3 display** shows state and faults, localisation ok/uncertain, battery voltage, bundle version, car name, a large READY/countdown and race-mode status;
  - the **EV3 LEDs + sounds** are configurable per state and event.
- **Alarms:** configurable thresholds per telemetry signal (for example loop rate < 40 Hz, battery < 7 V, CPU > 85 °C) raise a live warning + sound in the Live tab and a Discord message; sensible defaults are pre-set.

### 9d. Non-functional targets (acceptance criteria for specs and tests)
| Area | Target |
|---|---|
| Editor size | Assemblies up to **~2,000 parts** edit smoothly at 60 FPS (instancing, LOD, BVH picking) |
| Minimum hardware | **MacBook Air M2 / ThinkPad** run the editor, track editor, interactive sim and live screen smoothly; training and processing go to workers |
| Scan processing | No time limit, but the job is **cancellable and re-prioritisable** at any time, with progress shown per step |
| Live latency | **< 100 ms** car → screen in test mode on the local network (WebSocket/WebRTC, no Cloudflare detour when on the same LAN or Tailscale). Teleop shows a latency warning above 100 ms |
| Data loss (RPO) | **≤ 24 h** (nightly backup + offsite) |
| Backend availability | **Nearly no downtime:** **blue-green deploys** of the API containers behind Caddy (start new → health check → switch → stop old). DB migrations follow the expand/contract pattern so old and new versions run in parallel; Postgres and MinIO stay up. The local-first mode bridges any remaining interruption |
| Deploy to car | **< 60 s** from click to the car running the new bundle |
| Battery | Editor/sim warns if the expected test runtime is **< 30 min** |
| 2FA | **Mandatory for the admin**, optional for members |
| UI language | Defaults to the **system language** (DE/EN), switchable |
| Onboarding | **< 15 min** from installer to the first sim run with your own controller (guided first-start wizard + example controller) |
| Interactive sim speed | No hard target. **Correctness first**; the speed is shown so users know |

### 10. Onboard computer options (target-agnostic deploy)
The tool's deploy and benchmark layer works with any **aarch64/x86 Linux board + ONNX Runtime**, with optional accelerator back-ends. A *Targets* view benchmarks each exported model (latency, FPS, CPU load) on every connected board, so the choice can be made with data.

Prices are rough estimates; check eBay and Willhaben for current used prices.

| Board | Approx. price | Compute | Pros | Cons |
|---|---|---|---|---|
| **Raspberry Pi 5 (4/8 GB)** | ~55–90 € new, less used | 4× A76 @ 2.4 GHz | Best ecosystem, CSI cameras, ev3dev-style USB serial is easy, many guides | No NPU; camera nets limited to small models (~10–30 FPS at low resolution) |
| Raspberry Pi 4 (4 GB) | ~30–45 € used | 4× A72 | Cheapest, often already owned | About 2–3× slower than the Pi 5 |
| **Orange Pi 5 / Radxa Rock 5C** (RK3588S) | ~70–110 € | 4× A76 + 4× A55, **6 TOPS NPU** | More CPU than the Pi 5 plus an NPU for camera models at a similar price | NPU needs RKNN model conversion; weaker docs and community |
| Pi 5 + **AI HAT+** (Hailo-8L 13 TOPS / Hailo-8 26 TOPS) | +~70–110 € | Pi 5 + NPU | Strong camera AI, official Pi support | Uses a large part of the 200 € budget |
| Raspberry Pi **AI Camera** (Sony IMX500) | ~70 € | NPU in the camera | Runs small vision models on the sensor, so the Pi CPU stays free | Small models only, special toolchain |
| Used Jetson Nano 4 GB | ~60–100 € used | 128-core GPU | CUDA on the car | Old software (JetPack 4 / Ubuntu 18.04), end of life; risky |
| Jetson Orin Nano Super | ~250 € | 67 TOPS | Very strong | **Over budget** |
| Used mini-PC (Intel N100) | ~100–130 € used | x86 | Strong CPU, runs everything | Heavy and large, needs a 12 V supply of about 15 W; poor fit for a LEGO car |

**Decision: choose later by benchmark.**
- Until then, the tool ships deploy back-ends for generic aarch64 Linux + ONNX Runtime (Pi 4/5, Orange Pi CPU), with RKNN (Orange Pi NPU) and Hailo (AI HAT) as optional plug-ins.
- The training **sim models the onboard compute latency per target**, so controllers are trained for realistic loop rates.
- **Decision point in mid-January (weeks 14–15):** once the first RL/imitation/camera models exist, benchmark them on the candidate boards (borrowed or bought used) and compare on accuracy vs latency vs price.

## Milestones (Oct 7 → race in late February, ~19 weeks, full scope)
The schedule runs **4 parallel tracks**, each worked by its own AI agent sessions:
- **A:** core/sim/training/car
- **B:** construct editor/front-end
- **C:** capture/TrackScout/perception
- **D:** backend/workers/collaboration/live/MCP

Inside every track, **features that make the real car drive come first** ("car wins race").

| Weeks | Dates | A: Core · Sim · Train · Car | B: Construct editor | C: TrackScout · Scan pipeline | D: Backend · Workers · Live · MCP |
|---|---|---|---|---|---|
| 1 | Oct 7–13 | **Foundation** (all tracks): monorepo scaffold, AGENTS.md/CLAUDE.md, ADRs, specs for core schemas, CI gates on 3 OSes, branch protection, release pipeline skeleton | – | Scan the corridor with free apps as test fixtures | Proxmox VM + Docker Compose skeleton, Tailscale + Cloudflare Tunnel/Access, **AVX2 check** |
| 2–3 | Oct 14–27 | **Car arrives end of October, so this is pulled forward:** core schemas, parametric quick-start → MJCF, MuJoCo car + procedural corridor, sensor models (EV3 ultrasonic/gyro, 2D LiDAR), `RobotIO`/SimIO, **car_runtime + EV3 serial bridge (ev3dev) + RealIO**, on-car MCAP logging, **safety (hardware emergency stop, test speed limit, software stop)**, USB deploy, **controller SDK + docs for Java/C# devs**, classic controller template | Electron + React + three.js shell, uv engine bootstrap, i18n (EN/DE), LDraw loader + shadow-library connectors, part browser | TrackScout skeleton: LiDAR recording (depth, confidence, RGB, poses, ARKit mesh + classification), **pause/continue**, `.tscan` export | Auth + tokens, Postgres version store, MinIO blob store, chunked uploads, local SQLite cache + sync v1, backups |
| 4–5 | Oct 28–Nov 10 | **The car team starts.** Teleop + demonstration recording, Wi-Fi deploy, minimal live view (Wi-Fi), 2D-drawn quick tracks, opponents (own car versions), RunLog/replay, Gym env, Optuna tuning, private controller repo + two-way sync | Editor v1: place, snap, rotate, undo, submodels, derived data panel, rule checker, budget | Multi-pass projects + `ARWorldMap` relocalisation, **collaborative multi-device scanning**, RoomPlan capture, Wi-Fi upload; desktop `.tscan` importer. **The team starts scanning with TrackScout.** | **Workers v1** (register, idle-only policy, launch dialog local/specific/auto, blob cache, live logs) |
| 6–7 | Nov 11–24 | Race mode + radio check, deploy bundles + SD image build, fleet (multiple cars, per-car calibration), battery model, first log-based calibration from daily corridor tests | Custom-part import (STL/3MF/STEP), connector definition, material → mass, convex decomposition | Registration (world map + FPFH/ICP + markers), quality-weighted TSDF merge, geometric segmentation + ARKit labels | **Live tab** + telemetry relay, Wi-Fi + Bluetooth transports, link manager/failover, race-log replay |
| 8–9 | Nov 25–Dec 8 | PPO RL on workers + ONNX export, sim compute-latency model per target, **localisation (particle filter on the LiDAR map) + racing line + reactive fallback, resume/recovery** | Joints/kinematics preview, assembly → MJCF (editor cars drive in the sim) | **Track editor v1**: relabel, mapping table, 2D walls/floor, **start/finish**, start grid, objects, validation, sim geometry + centreline | Collaboration: presence + submodel locks, History tab + 3D diff, `.raceforge` bundles |
| 10–11 | Dec 9–22 | Imitation learning (BC + DAgger, sim and real demonstrations), log import + **sim calibration**, driving-style profiles, start methods (button, light/tone detection, wired trigger) + **Race Control app** | Editor polish: mirror, section view, inventory warnings, BOM export, **cable routing + collision check** | Coverage heatmap + scan-task list, conflict/variant detection, surface properties, object randomisation | **MCP server** (all tools), checkpoint resume across workers, job priorities between users |
| 12–13 | Dec 23–Jan 5 | *Buffer / holidays:* catch-up, bug bash, docs | ← | ← | ← |
| 14–15 | Jan 6–19 | Benchmark leaderboard, **onboard board benchmark → board decision**, camera perception model, **Gaussian-splat camera sim**, HIL/SIL mode, car-driven SLAM quick tracks | LDraw/Studio import, `.mpd`/STL export | TrackScout v2: live coverage overlay, **AR scan missions**, on-phone annotation; image segmentation (SegFormer + Grounded-SAM 2), 2D↔3D label fusion | ESP-NOW transport, captive-portal hardening, remote MCP via Cloudflare |
| 16 | Jan 20–26 | Dataset export, frame label editing, car-video import, procedural generator from scan statistics, **reports (budget/BOM, test/race, changelog)** | **Building instructions** (steps, PDF), **design optimisation job** | Follow-cam mode | Hardening, monitoring, restore test, **autonomous overnight AI experiments** (budget-limited agent token) |
| 17 | Jan 27–Feb 2 | **Feature freeze (Feb 2).** From here on: only fixes, tests, performance and docs. | ← | ← | ← |
| 18–19+ | Feb 3–race | Race prep: final scans, final training on the scanned corridor, pre-start calibration, race-day checklist (radios off, tagged bundle), dry runs on the real corridor | ← | ← | ← |

**Dependencies on the car team:**
- The first drivable car exists at the **end of October**, and the team has **daily corridor access**.
- The **controller SDK, sim, car_runtime, safety features and USB deploy** must therefore be ready by **Oct 27** (end of week 3). This is the tightest point of the plan, so track A gets the most parallel agent sessions in weeks 2–3.
- With daily real tests, real logs feed calibration and imitation learning continuously, starting in November.

### Risk management (full scope, solo developer, fixed race date)
- **Capacity risk (accepted on purpose):** Claude Pro allows about 1–2 parallel sessions and its usage limits will be hit regularly. The full scope stays; the mitigations are:
  - **maximum offloading to the GitHub Copilot coding agent:** every issue is written so precisely (spec link, acceptance tests, files in scope) that Copilot can implement it; Claude handles the complex and core work;
  - Claude sessions are scheduled around the usage windows (cloud sessions for long tasks, overnight);
  - small, well-cut issues that keep context short, which saves usage.
- **Weekly burn-down:** every Sunday, compare actual vs plan per track. If a track is **more than 1 week behind twice in a row**, its lowest-priority remaining item moves after the freeze, *only with your explicit decision*. **Must-haves before the race, never moved:** design optimisation, building instructions, AR scan missions, overnight AI experiments, the 3D showcase link and presentation video export, plus all core modules. Order of the items that can move: 3D comments → Gaussian-splat camera sim (fallback: textured-mesh camera rendering) → follow-cam → LoRa/radio transports → frame label editing → AR scan missions → camera perception model.
- **Critical path (protected):** sim + controller SDK → car_runtime + race mode → track editor (start/finish) → classic tuning on the scanned corridor. These never wait for anything else.
- **Fallbacks so the race never depends on unfinished features:**
  - parametric quick-start car if the editor is late;
  - free-app scan import if TrackScout is late;
  - a local-only mode if the backend is down;
  - the classic tuned controller if RL/imitation is not better on the benchmark.
- **Biggest technical risks, each tested early:**
  - AVX2 support on the Xeon (week 1);
  - ARWorldMap relocalisation in a long, repetitive corridor (week 4; markers as fallback);
  - LDraw shadow-library snapping (week 3);
  - EV3↔board serial latency (week 6);
  - MuJoCo Ackermann realism vs the real car (week 9 calibration).

## Still open (does not block the start)
- Whether the race rules allow only *disabled* or *physically removed* WLAN/BT on the RPi/EV3.
- Where large raw captures live: a school NAS, a shared drive, or cloud storage.
- The git host for **source code** (GitHub by default). Data no longer lives in git or LFS.
- **Ask the teacher:** is map-based localisation (live LiDAR matched against the scanned map) allowed as part of autonomous navigation? The reactive fallback always exists either way.
- **Ask the teacher:** do optical or acoustic start signals count as "wireless"? Would the teacher operate the Race Control start, and may all teams use the shared wired start box?
- **Ask the teacher:** may the parts connecting a non-LEGO steering motor to the LEGO steering (motor mount, shaft coupler) be 3D-printed? This is a configurable rule in the checker; the default is that the motor mount may be printed but the coupling into the steering must be LEGO.
- The **controller repo** is created by a teammate (not you). RaceForge then needs access to it for the two-way sync, through a GitHub App or fine-grained token that the repo owner grants.
- Which teammate's PC hosts the offsite backup, and how much disk it has (≥ 2 TB recommended).
- **After the race:** a built-in AI assistant panel in RaceForge (Claude API, user-supplied key). The architecture already prepares for it, because the panel would use the same tool layer as MCP.
- Whether a remote stop during test drives is acceptable to the teachers.
- Whether add-on radio modules (ESP32, HC-12, LoRa, travel router) count toward the 200 € budget if they are removed for the race.
- Whether 433/868 MHz radios are acceptable at school. They are licence-free ISM bands in Austria/EU within duty-cycle limits, but the teachers should confirm.

## Verification
- **Development process:**
  - A deliberately broken PR (failing test, type error, boundary violation, changed MCP schema) is blocked by CI.
  - Every merged PR links an issue/spec and has a human approval.
  - A fresh clone followed by one setup command gives green tests on macOS, Windows 11 and Linux.
- **pytest:**
  - schema round-trips;
  - connector snapping math (pin into hole gives the correct pose);
  - rigid-group detection and joint extraction on sample assemblies;
  - mass/CoG against a hand-calculated reference model;
  - custom-part volume → mass;
  - budget and rule checker (including the printed-part-in-steering case);
  - MJCF generation;
  - serial protocol.
- **Front-end:** Vitest for editor logic (undo, snap, selection). Playwright e2e builds a small chassis, adds a custom STL part with a connector, saves it, and the version appears in History.
- **CI:** tests plus a 10-second headless sim smoke test on macOS, Windows and Linux; the Electron app launch and first-start engine bootstrap are smoke-tested per OS.
- **Construct → sim:** a quick-start car and an editor-built copy of the same car give matching derived values and matching sim turning radius (±10 %). The steering kinematics preview agrees with the sim.
- **Capture → track:**
  - The test fixture scan (small, in the repo) goes through the full pipeline in CI on CPU, using the geometric-only mode with ML steps mocked.
  - On a hand-labelled reference scan of the real corridor, floor/wall mIoU ≥ 0.9 and object recall ≥ 0.8.
  - Manual relabels and drawn walls survive re-processing.
  - 3D→2D re-projected masks line up with the frames (pixel error check on known points).
  - Track validation flags a deliberately too-narrow passage and a missing finish line.
  - The sim car spawns on the start grid and lap timing triggers at the finish line.
  - **Multi-scan merge:**
    - Three passes of the fixture (walk, high, low) with known synthetic offsets are re-aligned to < 1 cm / 0.5° error.
    - Pose-graph merge of 5+ passes has no visible seams.
    - A pass containing a person walking through yields a merged mesh with the person removed.
    - A door open in one pass and closed in another becomes a door variant.
    - The quality-weighted fusion of a near and a far pass keeps the near-pass detail (measured against a reference).
  - **Coverage guide:**
    - Deleting a region from the fixture makes it red in the heatmap and the top item in the scan-task list.
    - After importing a gap-fill pass the region turns green and coverage % rises.
    - A scan mission exported to the app relocalises and shows the gap at the correct real-world place (on-device test).
  - **iOS app:**
    - The app builds and installs with a free Personal Team Apple ID; there are no paid dependencies and no network calls besides local upload (checked by an entitlement and dependency check in CI).
    - XCTest for `.tscan` writer/manifest;
    - the CI build runs on a macOS runner;
    - an on-device test scan of the corridor uploads over Wi-Fi and opens in the tool;
    - poses, depth and RGB line up (re-projection check);
    - ARKit classification labels appear as initial segments;
    - a second pass relocalises into the first pass's world map;
    - an optional car-height pass produces frames comparable to the car camera.
  - Playwright e2e: import fixture → relabel a segment → place start/finish → save → new version in History.
- **Driving stack:**
  - In the sim, localisation error is below 5 cm / 3° on the scanned corridor.
  - After a simulated kidnapping, the car relocalises within 3 seconds or falls back to reactive driving and never hits a wall.
  - Stuck detection triggers recovery in a test scenario.
  - The resume button works on the real car.
  - Reactive fallback alone completes the corridor.
- **Race start:**
  - The button start, the light/tone start (Race Control on a laptop) and the wired start box each start 3 cars at the same time (sim) and the real car, with measured reaction latency.
  - False-start patterns (a random flash or noise) do not trigger.
- **Training:** the tuned controller and PPO each complete ≥ 90 % of held-out corridors and the scanned corridor.
- **Versioning and sharing:**
  - versions are immutable and restore gives an identical hash;
  - the 3D diff is correct for added, removed and moved parts;
  - two clones sync each other's changes;
  - a `.raceforge` export/import across OSes is identical, including custom parts;
  - lineage reproduces the same sim metrics.
- **Live:**
  - a fake-car publisher drives the Live tab at 20 Hz;
  - on disconnect the indicator turns red within 1 second;
  - race logs replay identically;
  - race mode refuses sockets.
  - Every transport passes the same conformance test suite (frame round-trip, commands with acknowledgement, reconnect). Loopback/fake transports run in CI, and real Wi-Fi, BT and radio links are tested on hardware.
  - Pulling the Wi-Fi link during a test drive fails over to BT or radio within 2 seconds without losing commands.
  - Race mode refuses to start while any radio interface is up or a radio USB device is present.
- **Backend:**
  - `docker compose up` on a fresh Debian VM brings up all services and they pass health checks.
  - The desktop app reaches the backend via Tailscale and, with Tailscale off, falls back to Cloudflare automatically.
  - A 2 GB scan uploads through Cloudflare in chunks and resumes after a disconnect.
  - Workers authenticate with service tokens.
  - Offline edits sync on reconnect; when two users edit different submodels both edits survive; a lock blocks a second editor and expires after disconnect.
  - A training job targeted at "RTX-4070-Laptop" downloads only the missing blobs, streams metrics live and uploads ONNX + lineage. Killing the laptop mid-job resumes it from the last checkpoint on the RTX 3070 PC. With no worker online, the job waits in the queue and starts as soon as a matching worker becomes idle.
  - The "This machine" path produces the same registry entry.
  - A restore test from the restic offsite backup into a scratch VM gives identical versions and blobs.
- **Reports:**
  - The budget report for a reference car matches a hand-calculated total.
  - The test report from a recorded log contains the correct lap times and plots.
  - The changelog between two versions lists exactly the changed parts and parameters.
  - All reports render in English and German.
- **MCP:**
  - SDK in-memory client tests for every tool;
  - check with MCP Inspector;
  - e2e from Claude Code and Perplexity: "create a car from quick-start, add our sensor mount, validate, simulate on a generated corridor"; the result appears in the UI and History.
- **Sim-to-real:** the calibrated sim stays within the RMSE threshold of real logs, and the ONNX policy runs at ≥ 20 Hz on the RPi.
