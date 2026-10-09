"""Engine API models (spec 0008) — the contract between the UI and the Python engine.

Changing these models changes the OpenAPI schema (snapshot-tested): needs an approved spec change.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from raceforge.car.deploy import InstallResult
from raceforge.construct.quickstart import QuickStartParams
from raceforge.track.procedural import CorridorParams

V3 = tuple[float, float, float]
Q4 = tuple[float, float, float, float]  # w, x, y, z


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Health(ApiModel):
    version: str
    ldraw_available: bool
    ldraw_dir: str


class PartSummary(ApiModel):
    key: str
    ldraw_id: str | None
    name: str
    category: str
    mass_g: float
    connectors: int
    device: str | None
    verified: bool


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
    color: int  # LDraw colour code (function colours)
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
    """Build a test-mode bundle: controller file + optional params + car config (spec 0012)."""

    controller: str = Field(min_length=1)
    params: str | None = None
    car_config: str = Field(min_length=1)
    name: str | None = None


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
