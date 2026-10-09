"""Engine API models (spec 0008) — the contract between the UI and the Python engine.

Changing these models changes the OpenAPI schema (snapshot-tested): needs an approved spec change.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from raceforge.car.deploy import InstallResult
from raceforge.construct.quickstart import QuickStartParams
from raceforge.track.edit import CheckResult, ValidationReport
from raceforge.track.procedural import CorridorParams

V3 = tuple[float, float, float]
Q4 = tuple[float, float, float, float]  # w, x, y, z


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Health(ApiModel):
    version: str
    ldraw_available: bool
    ldraw_dir: str


class LDrawPart(ApiModel):
    """A part of the LDraw library that is not in the catalogue yet (spec 0018)."""

    ldraw_id: str
    title: str
    category: str | None
    in_catalogue: bool = False


class LocalPartRequest(ApiModel):
    """Add an LDraw part to the team's local catalogue (unverified). Holes/length create the
    standard pin-hole / pin-or-axle connectors like the curated beams, pins and axles."""

    ldraw_id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")
    name: str | None = Field(default=None, max_length=120)
    category: str
    mass_g: float = Field(gt=0, le=5000)
    holes: int | None = Field(default=None, ge=1, le=40)
    length_studs: int | None = Field(default=None, ge=1, le=40)
    color: int | None = Field(default=None, ge=0)


class PrintedImportRequest(ApiModel):
    """Import a 3D-printed part (spec 0019) from a mesh file on this computer."""

    path: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=80)
    units: Literal["mm", "cm", "m", "in"] = "mm"
    up: Literal["z", "y"] = "z"
    material: Literal["PLA", "PETG", "TPU"] = "PLA"
    infill_pct: float = Field(default=20.0, ge=0, le=100)
    measured_mass_g: float | None = Field(default=None, gt=0)  # kitchen scale beats the estimate


class PrintedPreview(ApiModel):
    volume_cm3: float
    watertight: bool  # False: volume from the convex hull (estimate too high)
    size_mm: V3
    faces: int
    mass_estimate_g: float
    cost_eur: float


class PartSummary(ApiModel):
    key: str
    ldraw_id: str | None
    name: str
    category: str
    mass_g: float
    connectors: int
    device: str | None
    verified: bool
    color: int | None = None  # usual LDraw colour (approximate); None for non-LEGO parts
    origin: Literal["curated", "local"] = "curated"  # local: added by the team (spec 0018)
    mesh_url: str | None = None  # 3D-printed part: its mesh (binary STL, metres, spec 0019)


class Primitive(ApiModel):
    kind: Literal["box", "cylinder", "plane"]
    pos: V3
    quat: Q4
    size: V3  # box: half-extents; cylinder: radius, half-height, 0; plane: half-x, half-y, 0
    color: str  # "#rrggbb"
    opacity: float = 1.0
    surface: str = ""


class ScenePart(ApiModel):
    key: str
    ldraw_id: str | None
    category: str
    pos: V3  # relative to the body
    quat: Q4
    color: int  # LDraw colour code: function colour (by submodel role)
    # LDraw colour code: the part's own colour (instance, else catalogue default)
    real_color: int = 16
    mesh_url: str | None = None  # 3D-printed part: its mesh (spec 0019)
    bbox_lo: V3
    bbox_hi: V3


class SceneBody(ApiModel):
    name: str
    parts: list[ScenePart]


class CarScene(ApiModel):
    name: str
    bodies: list[SceneBody]


class Warning(ApiModel):
    code: str
    message: str


class QuickstartResponse(ApiModel):
    assembly: dict[str, Any]
    derived: dict[str, Any]
    warnings: list[Warning]
    car: CarScene


class QuickstartSchema(ApiModel):
    json_schema: dict[str, Any]
    defaults: dict[str, Any]
    options: dict[str, list[Any]]


class ControllerInfo(ApiModel):
    name: str
    path: str
    template: bool


class CorridorResponse(ApiModel):
    track: dict[str, Any]
    primitives: list[Primitive]
    centreline: list[tuple[float, float]]


class ReplayRequest(ApiModel):
    path: str


class ReplaySummary(ApiModel):
    frames: int
    duration_s: float
    t: list[float]
    steering_cmd: list[float]
    speed_cmd: list[float]
    speed_meas: list[float | None]
    states: list[str]
    truth_xy: list[tuple[float, float]]
    channels: dict[str, list[float | None]]


class SimStart(ApiModel):
    type: Literal["start"] = "start"
    controller: (
        str  # path of a controller file, or "none" (stand still, drive by teleop; spec 0010)
    )
    params_path: str | None = None
    corridor: CorridorParams = Field(default_factory=CorridorParams)
    laps: int = Field(default=1, ge=1, le=20)
    opponents: int = Field(default=0, ge=0, le=5)
    seed: int = 0
    speed: float = Field(default=1.0, gt=0)  # 0 < speed; values >= 100 mean "as fast as possible"
    quickstart: QuickStartParams | None = None
    record_path: str | None = None
    quick_track: str | None = None  # name of a saved quick track (spec 0014) instead of `corridor`
    battery: bool = False  # model the motor battery: sag, charge, brownout (spec 0026)
    assembly: dict[str, Any] | None = None  # edited car (Construct editor, spec 0015) for "ego"


class SimControl(ApiModel):
    """``speed``: time factor for "speed", m/s for "teleop" (spec 0010, same JSON as the car)."""

    type: Literal["pause", "resume", "stop", "speed", "teleop", "teleop_release", "stop_car"]
    speed: float | None = None
    steer: float | None = None  # rad, "teleop" only


class UltrasonicView(ApiModel):
    value: float | None
    origin: V3
    direction: V3


class EgoView(ApiModel):
    ultrasonic: dict[str, UltrasonicView]
    lidar_points: list[tuple[float, float]]
    steering_cmd: float
    speed_cmd: float
    state: str
    channels: dict[str, float | int | bool | str]
    distance_m: float
    laps: int
    lap_times_s: list[float]
    finished: bool


class EventView(ApiModel):
    t: float
    car: str
    kind: str
    other: str


class SceneMessage(ApiModel):
    type: Literal["scene"] = "scene"
    primitives: list[Primitive]
    cars: list[CarScene]
    centreline: list[tuple[float, float]]


class FrameMessage(ApiModel):
    type: Literal["frame"] = "frame"
    t: float
    bodies: dict[str, dict[str, tuple[V3, Q4]]]  # car -> body -> (pos, quat)
    ego: EgoView
    events: list[EventView]


class ResultMessage(ApiModel):
    type: Literal["result"] = "result"
    finished: bool
    laps: int
    lap_times_s: list[float]
    sim_time_s: float
    wall_contacts: int
    problems: list[str]
    record_path: str | None


class ErrorMessage(ApiModel):
    type: Literal["error"] = "error"
    message: str


class SimProtocol(ApiModel):
    """Documents the /api/v1/sim WebSocket messages so they appear in OpenAPI and the TS client."""

    client_start: SimStart | None = None
    client_control: SimControl | None = None
    server_scene: SceneMessage | None = None
    server_frame: FrameMessage | None = None
    server_result: ResultMessage | None = None
    server_error: ErrorMessage | None = None


# ---- Team workspace (spec 0006): the engine proxies the backend and keeps working offline.
class WorkspaceLogin(ApiModel):
    server_url: str = Field(min_length=1)
    username: str
    password: str
    totp: str | None = None


class WorkspaceRegister(ApiModel):
    server_url: str = Field(min_length=1)
    invite: str = Field(min_length=1, description="invite link or token")
    username: str
    display_name: str
    password: str


class WorkspaceName(ApiModel):
    name: str = Field(min_length=1, max_length=128)


class WorkspaceSelect(ApiModel):
    workspace_id: str


class SaveQuickstart(ApiModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    params: QuickStartParams
    message: str = ""
    name: str | None = None


class SaveAssembly(ApiModel):
    """An edited assembly (Construct editor, spec 0015) as a new version of an `assembly` object."""

    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    assembly: dict[str, Any]
    message: str = ""
    name: str | None = None


class SaveFiles(ApiModel):
    kind: Literal["controller", "dataset", "model", "capture", "bundle"] = "controller"
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    paths: list[str] = Field(min_length=1)
    message: str = ""


class InviteRequest(ApiModel):
    role: Literal["member", "admin"] = "member"


class TrackScoutPairing(ApiModel):
    """TrackScout pairing QR (spec 0007): server, new `trackscout` API token + laptop key."""

    url: str
    qr_svg: str
    token_id: str
    laptop_name: str
    workspace_id: str | None
    warning: Literal["loopback"] | None = None  # server address only reachable on this computer


class TokenRequest(ApiModel):
    name: str = Field(min_length=1, max_length=128)
    scopes: list[Literal["read", "sim_train", "edit", "admin"]] = Field(min_length=1)
    client: str = "desktop"


class InboxAction(ApiModel):
    """Team tab choice for a pass that waits on this laptop (spec 0007 scope 9)."""

    action: Literal["upload_now", "when_faster", "keep_local"]


class ReceiveRequest(ApiModel):
    """Pull finished passes from a paired phone: by cable (USB) or Bluetooth LE."""

    source: Literal["usb", "bluetooth"]


# ------------------------------------------------------------------ scans (spec 0009)
class ScanPassRef(ApiModel):
    """One TrackScout pass the engine can open, identified by its SHA-256."""

    sha256: str
    name: str
    size: int
    source: Literal["workspace", "laptop", "file"]
    pass_type: str = ""
    created_at: str = ""
    error: str | None = None


class ScanTrack(ApiModel):
    """Passes of one track (same coordinate frame): a workspace capture object or a project name."""

    name: str
    slug: str | None = None
    version: str | None = None
    passes: list[ScanPassRef]


class ScanSegment(ApiModel):
    index: int
    start_s: float
    end_s: float
    frames: int
    discarded: list[tuple[float, float]]


class ScanDetail(ApiModel):
    sha256: str
    summary: dict[str, Any]
    segments: list[ScanSegment]
    trajectory: list[tuple[float, float, float]]  # camera positions, RaceForge frame (Z up, metres)
    trajectory_kept: list[bool]
    trajectory_segment: list[int]
    bounds: tuple[tuple[float, float, float], tuple[float, float, float]]  # min, max


class ScanMesh(ApiModel):
    """All segment meshes of a pass, little-endian arrays as base64 (spec 0009)."""

    sha256: str
    vertices: int
    faces: int
    total_faces: int  # before subsampling
    positions_b64: str  # float32[3·vertices]
    indices_b64: str  # uint32[3·faces]
    classes_b64: str  # uint8[faces], index into classes
    classes: list[str]


class ScanOpen(ApiModel):
    path: str


class CarPairingRequest(ApiModel):
    """Car address + token to hand to TrackScout's drive mode (spec 0010 C)."""

    url: str
    token: str | None = None


class CarPairingCode(ApiModel):
    code: str  # raceforge://car?v=1&d=<base64url(JSON)>
    qr_svg: str


# ------------------------------------------------------------------ deploy (spec 0012)
class BundleRequest(ApiModel):
    """Build a bundle: controller file + optional params + car config (spec 0012).

    ``race`` (spec 0030): a race-mode bundle — arms only when every radio is off, no live view or
    teleop, no test speed limit."""

    controller: str = Field(min_length=1)
    params: str | None = None
    car_config: str = Field(min_length=1)
    name: str | None = None
    race: bool = False


class BundleInfo(ApiModel):
    path: str
    name: str
    digest: str
    controller: str
    params: str | None = None
    car_name: str
    mode: str
    speed_limit_m_s: float | None = None
    warnings: list[str] = []


class DeployRequest(ApiModel):
    bundle: str = Field(min_length=1)
    target: Literal["ssh", "usb"]
    host: str | None = None  # [user@]host, "ssh"
    stick: str | None = None  # mounted stick folder, "usb"


class DeployResponse(ApiModel):
    result: InstallResult | None = None  # what the board reported ("ssh")
    usb_path: str | None = None  # where the bundle was written ("usb")


# ------------------------------------------------------------------ training (spec 0013)
class TrainRace(ApiModel):
    """Race settings shared by benchmark and tuning (held-out corridors from seed 1000)."""

    tracks: int = Field(default=5, ge=1, le=100)
    length_m: float = Field(default=25.0, ge=20.0, le=120.0)
    laps: int = Field(default=1, ge=1, le=10)
    opponents: int = Field(default=0, ge=0, le=5)
    max_time_s: float = Field(default=240.0, ge=10.0, le=3600.0)
    quick_track: str | None = None  # race on this saved quick track (spec 0014): `tracks` = runs


class TrainBenchRequest(ApiModel):
    controller: str = Field(min_length=1)
    params: str | None = None
    race: TrainRace = TrainRace()


class TrainTuneRequest(ApiModel):
    controller: str = Field(min_length=1)
    trials: int = Field(default=30, ge=1, le=1000)
    train_tracks: int = Field(default=3, ge=1, le=50)
    timeout_s: float | None = Field(default=None, gt=0)
    out: str | None = None  # params YAML (default: <controller>.tuned.yaml)
    race: TrainRace = TrainRace()


class TrainRLRequest(ApiModel):
    """PPO training (spec 0022); the policy goes into a params YAML for onnx_policy.py."""

    steps: int = Field(default=200_000, ge=256, le=50_000_000)
    train_tracks: int = Field(default=8, ge=1, le=200)
    out: str | None = None  # params YAML (default: engine data folder / policies)
    race: "TrainRace" = Field(default_factory=lambda: TrainRace())


class TrainBCRequest(ApiModel):
    """Behaviour cloning from recorded drives (spec 0023)."""

    recordings: list[str] = []  # MCAP files; empty: every recording in the engine's runs folder
    epochs: int = Field(default=60, ge=1, le=2000)
    all_states: bool = False  # False: only teleop frames (demonstrations)
    out: str | None = None
    race: "TrainRace" = Field(default_factory=lambda: TrainRace())


class RecordingInfo(ApiModel):
    path: str
    name: str
    frames: int
    demo_frames: int  # teleop frames
    duration_s: float
    modified: float  # unix time
    error: str = ""


Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
HhMm = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class ScheduleWindow(ApiModel):
    """A weekly time window; ``end`` before ``start`` crosses midnight (days = start days)."""

    days: list[Weekday] = Field(min_length=1)
    start: str = HhMm
    end: str = HhMm


class WorkerPolicy(ApiModel):
    """When this computer takes team jobs (spec 0020 part C)."""

    mode: Literal["always", "idle", "schedule", "paused"] = "idle"
    idle_minutes: float = Field(default=10.0, ge=1.0, le=240.0)  # no keyboard/mouse input
    schedule: list[ScheduleWindow] = Field(default_factory=list[ScheduleWindow])
    processes: int = Field(default=0, ge=0, le=256)  # parallel races; 0: CPU count - 1


class TeamJobRequest(ApiModel):
    """Run a training job on a team worker (spec 0020): exactly one of bench/tune/rl."""

    bench: "TrainBenchRequest | None" = None
    tune: "TrainTuneRequest | None" = None
    rl: "TrainRLRequest | None" = None
    target_worker_id: str | None = None  # part C: only this worker; None = any team worker
    priority: Literal["normal", "high", "critical"] = "normal"  # critical: admins only


class LocalWorkerUpdate(ApiModel):
    """Switch this computer's team worker on/off and set when it takes jobs (spec 0020 C)."""

    enabled: bool
    policy: WorkerPolicy = Field(default_factory=lambda: WorkerPolicy())


class LocalWorkerStatus(ApiModel):
    enabled: bool  # the worker loop runs in this engine
    registered: bool  # in the current workspace
    name: str | None = None
    worker_id: str | None = None
    policy: WorkerPolicy
    available: bool  # the policy allows jobs right now
    reason: str = ""  # paused | outside_schedule | on_battery | user_active | idle_unknown
    log: list[str] = Field(default_factory=list[str])


class TrainRun(ApiModel):
    seed: int
    finished: bool
    time_s: float
    fraction: float
    wall_contacts: int
    error: str = ""


class TrainTrial(ApiModel):
    number: int  # -1: the default params
    score: float
    best: float
    params: dict[str, Any]


class TrainJob(ApiModel):
    id: str
    kind: Literal["benchmark", "tune", "rl", "bc"]
    controller: str
    state: Literal["running", "done", "error", "cancelled"]
    started_at: float
    finished_at: float | None = None
    total: int  # runs (benchmark), trials (tune) or environment steps (rl)
    steps_done: int = 0  # rl
    mean_reward: float | None = None  # rl: mean episode reward of the last rollout
    val_loss: float | None = None  # bc: validation loss of the last epoch
    runs: list[TrainRun] = []
    trials: list[TrainTrial] = []
    score: float | None = None  # benchmark score / held-out score of the tuned params
    default_score: float | None = None  # tune: held-out score of the defaults
    finished_rate: float | None = None
    out: str | None = None  # tune: written params YAML
    best_params: dict[str, Any] | None = None
    error: str = ""


# ------------------------------------------------------------------ quick tracks (spec 0014)
class QuickTrackInfo(ApiModel):
    name: str
    length_m: float | None = None
    loop: bool
    error: str | None = None


class QuickTrackObject(ApiModel):
    kind: str
    x: float
    y: float
    yaw: float
    size_x: float
    size_y: float


class QuickTrackPreview(ApiModel):
    """2D view of a drawn track as the simulator will build it (or why it cannot)."""

    ok: bool
    error: str | None = None
    length_m: float = 0.0
    centreline: list[tuple[float, float]] = []
    walls: list[list[tuple[float, float]]] = []
    start_line: tuple[tuple[float, float], tuple[float, float]] | None = None
    direction: tuple[float, float] | None = None
    objects: list[QuickTrackObject] = []
    # Track editor (spec 0025).
    finish_line: tuple[tuple[float, float], tuple[float, float]] | None = None
    start_grid: list[tuple[float, float, float]] = []  # x, y, yaw (rad)
    checkpoints: list[tuple[tuple[float, float], tuple[float, float]]] = []
    no_go_zones: list[list[tuple[float, float]]] = []
    surfaces: list[list[tuple[float, float]]] = []  # drawn regions (not the base floor)
    checks: list[CheckResult] = []
    validation: ValidationReport = ValidationReport()


# ------------------------------------------------------------------ assembly editor (spec 0015)
class EditOp(ApiModel):
    """One edit; ``none`` only evaluates. Moves and turns use world axes (m, quarter turns)."""

    kind: Literal[
        "none", "move", "rotate", "delete", "add", "snap", "duplicate", "mirror", "attach"
    ] = "none"
    path: list[str] = []
    paths: list[
        list[str]
    ] = []  # a selection of several parts (move/rotate/delete/duplicate/mirror)
    delta: V3 = (0.0, 0.0, 0.0)
    axis: Literal["x", "y", "z"] = "z"
    turns: int = Field(default=1, ge=-3, le=3)
    key: str | None = None  # catalogue part to add / attach
    candidate: int = Field(default=0, ge=0)  # attach: which docking position (cycles)
    position: V3 = (0.0, 0.0, 0.05)


class AssemblyEditRequest(ApiModel):
    assembly: dict[str, Any]
    quickstart: QuickStartParams = Field(default_factory=QuickStartParams)  # drives, steering
    op: EditOp = EditOp()
    snap: bool = True  # after move/add: snap to a compatible connector nearby


class AssemblyExportRequest(ApiModel):
    assembly: dict[str, Any]
    quickstart: QuickStartParams = Field(default_factory=QuickStartParams)  # drives for MJCF


class EditorConnector(ApiModel):
    id: str
    type: str
    pos: V3
    axis: V3


class EditorPartView(ApiModel):
    path: list[str]
    key: str
    name: str
    ldraw_id: str | None
    category: str
    color: int  # function colour (by submodel role)
    real_color: int = 16  # the part's own LEGO colour
    mesh_url: str | None = None  # 3D-printed part: its mesh (spec 0019)
    pos: V3
    quat: Q4
    bbox_lo: V3
    bbox_hi: V3
    mirrored: bool
    linked: bool
    connectors: list[EditorConnector]


class SnapInfo(ApiModel):
    connector: str
    target: list[str]
    target_connector: str
    distance_m: float


class AssemblyEditResponse(ApiModel):
    assembly: dict[str, Any]
    parts: list[EditorPartView]
    derived: dict[str, Any]
    warnings: list[Warning]
    problems: list[str]
    selected: list[str] | None = None
    selected_many: list[list[str]] = []  # the selection after a group operation
    candidates: int = 0  # attach: number of docking positions for the part
    snapped: SnapInfo | None = None
    rules: list["RuleCheck"] = []
    overlaps: list[list[list[str]]] = []  # pairs of part paths (spec 0016)
    budget: "BudgetView | None" = None


# ------------------------------------------------------------------ rules & budget (spec 0016)
class PriceEntry(ApiModel):
    eur: float = Field(ge=0)
    link: str = ""  # eBay/Willhaben/shop link
    date: str = ""  # ISO date the price was checked


class ConstructLimits(ApiModel):
    """Size/weight limits from the race rules; empty until the teacher defines them."""

    max_length_m: float | None = Field(default=None, gt=0)
    max_width_m: float | None = Field(default=None, gt=0)
    max_height_m: float | None = Field(default=None, gt=0)
    max_mass_kg: float | None = Field(default=None, gt=0)


class ConstructSettings(ApiModel):
    prices: dict[
        str, PriceEntry
    ] = {}  # catalogue key -> price (LEGO school-kit parts default to 0)
    limits: ConstructLimits = ConstructLimits()
    budget_eur: float = Field(default=200.0, gt=0)
    filament_eur_per_kg: float = Field(default=25.0, ge=0)  # print cost of 3D-printed parts


class RuleCheck(ApiModel):
    id: str  # wheels_steering_lego, steering_submodel_lego, ev3_drives, budget, max_*, overlaps
    ok: bool | None  # None: not checked (no limit set)
    params: dict[str, str | float] = {}
    paths: list[list[str]] = []


class BudgetLine(ApiModel):
    key: str
    name: str
    count: int
    unit_eur: float | None  # None: price unknown
    stale: bool = False  # price older than 30 days


class BudgetView(ApiModel):
    total_eur: float
    limit_eur: float
    missing: list[str]
    items: list[BudgetLine]


AssemblyEditResponse.model_rebuild()


# ------------------------------------------------------------------ part connectors (spec 0021)
class ConnectorDef(ApiModel):
    """A connector in the part's own frame (core frame, metres)."""

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    type: Literal[
        "pin_hole", "axle_hole", "pin", "axle", "stud", "anti_stud", "screw_hole", "fixed_mount"
    ]
    pos: V3
    axis: V3 = (0.0, 0.0, 1.0)


# ------------------------------------------------------------------ scan floor plan (spec 0024)
class FloorplanResponse(ApiModel):
    """Car-height occupancy of a scan pass as an image (row 0 = highest y), to draw tracks on."""

    sha256: str
    origin: tuple[float, float]  # world (x, y) of the image's lower-left corner, metres
    resolution: float  # metres per pixel
    width: int
    height: int
    png_b64: str
    floor_z: float
    trajectory: list[tuple[float, float]]  # camera path (x, y), for orientation


class CorridorWidthRequest(ApiModel):
    points: list[tuple[float, float]] = Field(min_length=2, max_length=500)


class CorridorWidth(ApiModel):
    median_m: float | None  # None: no closed cross-section along the line
    min_m: float | None
    samples: int


# ------------------------------------------------------------------ race control (spec 0031)
class RaceResult(ApiModel):
    """One race timed in Race Control: start signal time, laps per car, incidents, standings."""

    laps: int = Field(ge=1, le=50)
    started_at_ms: float = Field(gt=0)  # epoch ms of the start signal (lights out)
    cars: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    standings: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])


class RaceSaved(ApiModel):
    run: str  # slug of the `run` object in the workspace
