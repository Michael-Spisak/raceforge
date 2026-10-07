# Spec 0001: Core schemas (`raceforge.core`)

- **Status:** approved (2026-10-07) — implementing
- **Owner:** @Michael-Spisak
- **Plan section:** docs/PLAN.md §1 (Construct), §2 (Track), §5 (Deploy), §7 (Live), §6 (Versioning)
- **Related ADRs:** ADR-0001, ADR-0002, ADR-0003, ADR-0005

## Purpose
`raceforge.core` defines the data contracts shared by every module: the car assembly (parts, connectors,
submodels, devices), tracks, versioned object metadata, run logs and telemetry frames. All other modules
import these models; `core` imports nothing else from `raceforge` (enforced by import-linter).

## Scope
- In scope: Pydantic v2 models, enums, unit/frame conventions, JSON (+ YAML for human-edited configs)
  serialisation, schema versioning + migration framework, validation rules, JSON Schema export.
- Out of scope: derived computations (mass/CoG/joints → `construct`), MJCF generation (`sim`),
  storage/DB mapping (`backend`), LDraw parsing (`parts`). These consume the models defined here.

## Conventions (binding for all modules)
| Topic | Decision |
|---|---|
| Frame | Right-handed, **Z-up**. Vehicle frame: X forward, Y left, Z up (ROS REP-103 style). Conversion to LDraw (−Y up, LDU) and three.js (Y-up) happens only at those boundaries. |
| Units | **SI** everywhere in data: m, kg, s, rad, N, V, A, Wh. UI converts to mm/studs/degrees. |
| Rotation | Unit **quaternion** `(w, x, y, z)`, normalised on validation (tolerance 1e-6, else error). |
| Pose | `Pose { position: Vec3, orientation: Quat }`, always relative to a named parent frame. |
| IDs | **UUIDv7** (`ObjectId`) for identity + human **slug** (`^[a-z0-9][a-z0-9-]{1,62}$`) unique per workspace and kind. |
| Versions | Immutable `VersionRef { object_id, semver, content_hash }`; `content_hash` = SHA-256 of canonical JSON. Optional human `name`. |
| Money | `Money { cents: int ≥ 0, currency: "EUR" }` (ISO 4217, default EUR). |
| Time | `Timestamp { mono_ns: int, wall_offset_ns: int \| None }`; wall time = mono_ns + offset when known. |
| Serialisation | Canonical **JSON** (sorted keys, UTF-8, no NaN) for API/DB/bundles/hashing; **YAML** only for human-edited configs (controller/training). |
| Strictness | `extra="forbid"`, frozen models for versioned content, range checks (e.g. mass > 0). |
| Schema evolution | Every top-level document carries `schema: "<kind>"` and `schema_version: int`. A migration registry upgrades old documents step by step on load. |

## Interfaces (contracts)

### Primitives
`Vec3`, `Quat`, `Pose`, `Money`, `Timestamp`, `ObjectId`, `Slug`, `SemVer`, `ContentHash`, `VersionRef`.

### Versioned object envelope
```text
ObjectMeta { id: ObjectId, slug: Slug, kind: ObjectKind, workspace_id: ObjectId,
             created_by: str, created_at: datetime(UTC), tags: list[str] }
VersionMeta { ref: VersionRef, parent_versions: list[VersionRef], message: str,
              author: str, created_at: datetime(UTC) }
ObjectKind = part | assembly | track | controller | dataset | run | bundle | capture | model
```

### Parts and connectors
```text
Part { schema="part", schema_version, source: lego_ldraw | printed | device | other,
       ldraw_id?: str, name, mass_kg?: float>0, mass_measured_kg?: float>0,
       material?: Material, price?: PriceInfo, mesh_ref?: BlobRef,
       connectors: list[Connector], device?: Device, verified: bool }
Connector { id: str, type: pin_hole | axle_hole | pin | axle | stud | anti_stud | screw_hole | fixed_mount,
            pose: Pose (in part frame), axis: Vec3 (unit), gender: male | female | neutral, length_m?: float }
Material { kind: PLA | PETG | TPU | ABS | other, density_kg_m3: float, infill: 0..1 }
PriceInfo { price: Money, source_url?: str, observed_on: date }
BlobRef { sha256: str, size_bytes: int, media_type: str }
```

### Devices (part + device role)
```text
Device = discriminated union on "type":
  Ev3Brick | Ev3UltrasonicSensor | Ev3GyroSensor | Ev3TouchSensor | Ev3ColorSensor
  | Ev3LargeMotor | Ev3MediumMotor | DcMotor | Encoder | Lidar2D | Camera | TofSensor
  | ImuSensor | Battery | ComputeBoard | EmergencyStop | RadioModule
Common: { type, model: str, port?: PortAssignment, params: <type-specific datasheet params> }
PortAssignment { host: ev3:<n> | board, port: str }   # e.g. ev3:1 / "B", board / "/dev/ttyUSB0"
```
Type-specific params, e.g. `Lidar2D { rate_hz, angular_res_rad, range_min_m, range_max_m, noise_std_m }`,
`Camera { width_px, height_px, fov_h_rad, rolling_shutter: bool, intrinsics?: CameraIntrinsics }`,
`Battery { nominal_v, capacity_wh, internal_resistance_ohm, max_current_a }`,
`DcMotor { nominal_v, stall_torque_nm, no_load_speed_rad_s, gear_ratio }`.

### Assembly (tree of submodels)
```text
Assembly { schema="assembly", schema_version, root: SubmodelId,
           submodels: dict[SubmodelId, Submodel], connections: list[Connection],
           joints: list[JointRole], rules: RuleProfileRef?, measured: MeasuredOverrides? }
Submodel { id, name, role?: steering | drive | chassis | sensor_mast | electronics | other,
           items: list[PartInstance | SubmodelInstance] }
PartInstance { id: InstanceId, part: VersionRef, pose: Pose (in submodel frame), color?: int }
SubmodelInstance { id: InstanceId, submodel: SubmodelId, pose: Pose, mirrored: Axis | None }
Connection { a: ConnectorPath, b: ConnectorPath }        # path = instance chain + connector id
JointRole { connection: Connection index, role: steering_pivot | wheel_axle | drive_motor
            | steering_motor | gear_mesh, gear_ratio?: float }
MeasuredOverrides { mass_kg?: float, cog?: Vec3 }
```
Invariants: submodel graph is acyclic; every `ConnectorPath` resolves; connected connector types are
compatible (compatibility table in `core.connectors`); instance IDs unique within a submodel.

### Track (layered)
```text
Track { schema="track", schema_version, frame_origin_note: str,
        floor: Polygon2D, walls: list[Polyline2D(+height_m)], mesh?: BlobRef, splat?: BlobRef,
        occupancy?: OccupancyGridRef, labels?: LabelLayerRef, objects: list[TrackObject],
        surfaces: list[SurfaceRegion], race_setups: list[RaceSetup],
        check_measurements: list[CheckMeasurement], classes: list[ClassDef] }
RaceSetup { id, name, start_line: Segment2D, finish_line: Segment2D, direction: Vec2,
            laps: int ≥ 1 (default 3), start_grid: list[Pose2D], checkpoints: list[Segment2D],
            no_go_zones: list[Polygon2D], door_states: dict[str, open | closed | random] }
TrackObject { id, class_id, pose: Pose, size: Vec3, static: bool, randomisation?: RandomRange }
SurfaceRegion { polygon: Polygon2D, friction: float, material: str, lidar_reflectivity: 0..1 }
ClassDef { id, name, builtin: bool, material?: str, lidar_reflectivity?: float, camera_hint?: str }
CheckMeasurement { a: Vec2, b: Vec2, measured_m: float }
```

### Run logs and telemetry
```text
TelemetryFrame { schema="telemetry", schema_version, t: Timestamp, seq: int,
                 mode: test | race | sim | hil, state: str, faults: list[str],
                 cmd: Command, meas: Measured, pose_est?: Pose2DEstimate,
                 power: PowerStatus, loop: LoopStats,
                 channels: dict[str, float | int | bool | str] }   # free channels from controllers
Command { steering_rad, speed_m_s }
Measured { steering_rad?, speed_m_s?, yaw_rate_rad_s?, sensors: dict[str, SensorReading] }
SensorReading = Range | RangeArray (lidar sectors/points) | ImuSample | Bool | CameraFrameRef
RunLog { schema="runlog", schema_version, id, kind: sim | real | hil, car?: ObjectId,
         assembly: VersionRef, track?: VersionRef, race_setup_id?, controller: VersionRef,
         bundle?: VersionRef, started: Timestamp, notes: list[Note], file: BlobRef (MCAP) }
```
Free `channels` keys must match `^[a-z][a-z0-9_.]{0,63}$`; max 64 channels per frame.

## Behaviour
- `core.io.load(data)` detects `schema` + `schema_version`, runs migrations up to current, validates strictly.
- `core.io.dump(model)` emits canonical JSON; `content_hash(model)` hashes canonical JSON excluding `VersionMeta`.
- `core.export_json_schemas(path)` writes JSON Schema for every top-level document (used by frontend TS
  type generation and contract snapshot tests).
- Quaternions are normalised if within tolerance, rejected otherwise; zero-length axes rejected.

## Non-functional targets
- Loading + validating a 2,000-part assembly: < 200 ms on a MacBook Air M2.
- Serialising a telemetry frame (no camera): < 50 µs; canonical JSON round-trip is lossless.

## Acceptance criteria (→ tests)
- [ ] AC1: Every top-level model round-trips `dump → load` to an equal object (property-based tests, Hypothesis).
- [ ] AC2: `content_hash` is stable across runs, OSes and key order; any field change changes it.
- [ ] AC3: Unknown fields, negative mass, non-normalisable quaternion, bad slug, unresolved `ConnectorPath`,
      cyclic submodels and incompatible connector pairs are rejected with clear error messages.
- [ ] AC4: A v1 document fixture is migrated to the current version by the migration registry (framework test
      with a dummy v1→v2 migration).
- [ ] AC5: JSON Schemas are exported for all top-level documents; a snapshot test fails on any contract change.
- [ ] AC6: Mirrored `SubmodelInstance` and linked instances resolve to correct world poses (Z-up) in a
      helper `core.frames.world_pose()`; LDraw (−Y up, LDU) ↔ core conversion helper round-trips.
- [ ] AC7: Telemetry free-channel key and count limits are enforced.
- [ ] AC8: Performance targets above are met in a benchmark test (skipped on CI if slower runner).
- [ ] AC9: `pyright --strict` passes for `raceforge.core`; import-linter contract "core depends on nothing" kept.

## Open questions
- None blocking. Device param lists will be extended per sensor spec (0003) without breaking changes (new
  optional fields only, or migrations).
