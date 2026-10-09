"""Backend REST contract (spec 0006; human-owned, OpenAPI snapshot-tested)."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from raceforge.core.meta import ObjectKind
from raceforge.core.primitives import ObjectId, SemVer, Sha256, Slug


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Role(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


class Scope(StrEnum):
    READ = "read"
    SIM_TRAIN = "sim_train"
    EDIT = "edit"
    ADMIN = "admin"
    WORKER = "worker"  # only worker tokens (spec 0020): fetch jobs, report progress and results


Username = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,31}$")
Password = Field(min_length=10, max_length=256)


class UserInfo(Model):
    id: str
    username: str
    display_name: str
    role: Role
    totp_enabled: bool


class LoginRequest(Model):
    username: str
    password: str
    totp: str | None = None
    client: str = Field(default="web", max_length=32)


class TokenPair(Model):
    access_token: str
    refresh_token: str
    expires_in: int
    user: UserInfo
    totp_verified: bool


class RefreshRequest(Model):
    refresh_token: str | None = None  # browser: taken from the HTTP-only cookie


class TotpSetup(Model):
    secret: str
    uri: str


class TotpCode(Model):
    code: str = Field(min_length=6, max_length=8)


class InviteCreate(Model):
    role: Role = Role.MEMBER
    email: str | None = None


class InviteInfo(Model):
    id: str
    role: Role
    email: str | None
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    token: str | None = None  # only in the response that creates the invite
    link: str | None = None


class RegisterRequest(Model):
    invite_token: str
    username: str = Username
    display_name: str = Field(min_length=1, max_length=128)
    password: str = Password


class ApiTokenCreate(Model):
    name: str = Field(min_length=1, max_length=128)
    scopes: list[Scope] = Field(min_length=1)
    client: str = Field(
        default="desktop", max_length=32
    )  # desktop | cli | worker | mcp | trackscout
    expires_at: datetime | None = None


class ApiTokenInfo(Model):
    id: str
    name: str
    scopes: list[Scope]
    client: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked: bool
    user: str
    token: str | None = None  # only in the response that creates the token


class WorkspaceCreate(Model):
    name: str = Field(min_length=1, max_length=128)


class WorkspaceInfo(Model):
    id: str
    name: str
    created_at: datetime


class VersionInfo(Model):
    id: str
    object_id: str
    semver: SemVer
    name: str | None
    message: str
    author: str
    created_at: datetime
    content_hash: Sha256
    parents: list[str]
    branch: bool


class VersionContent(VersionInfo):
    content: dict[str, Any]


class ObjectCreate(Model):
    id: ObjectId | None = None  # client-generated UUIDv7 (offline-first sync)
    kind: ObjectKind
    slug: Slug
    tags: list[str] = Field(default_factory=list[str])


class ObjectUpdate(Model):
    slug: Slug | None = None
    tags: list[str] | None = None


class ObjectInfo(Model):
    id: str
    workspace_id: str
    kind: ObjectKind
    slug: str
    tags: list[str]
    created_by: str
    created_at: datetime
    copied_from: str | None
    deleted_at: datetime | None
    latest: VersionInfo | None


class ObjectCopy(Model):
    workspace_id: str
    slug: Slug | None = None


class VersionCreate(Model):
    id: ObjectId | None = None  # client-generated; re-sending the same id + content is a no-op
    content: dict[str, Any]
    semver: SemVer | None = None  # default: next free patch version after the parent
    name: str | None = Field(default=None, max_length=128)
    message: str = Field(default="", max_length=10_000)
    parents: list[str] | None = None  # default: the latest version


class DraftIn(Model):
    content: dict[str, Any]


class DraftOut(Model):
    content: dict[str, Any]
    updated_at: datetime


class FileEntry(Model):
    path: str = Field(min_length=1, max_length=255, pattern=r"^[^/\\][^\\]*$")
    sha256: Sha256
    size: int = Field(ge=0)


class FileSetContent(Model):
    """Content for kinds without a core document (controllers, models, …): files as blobs."""

    schema_: Literal["fileset"] = Field(default="fileset", alias="schema")
    schema_version: Literal[1] = 1
    files: list[FileEntry] = Field(min_length=1)
    entry: str | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class UploadCreate(Model):
    sha256: Sha256
    size: int = Field(ge=0)


class UploadInfo(Model):
    id: str | None
    sha256: str
    size: int
    part_size: int
    parts: int
    received: list[int]
    status: Literal["exists", "open", "complete"]


class AuditInfo(Model):
    id: int
    at: datetime
    user: str | None
    token_id: str | None
    client: str
    action: str
    target: str
    detail: dict[str, Any]


class Status(Model):
    version: str
    database: bool
    blobs: bool
    disk_used: float
    disk_level: Literal["ok", "warn", "high", "critical"]


class Problem(Model):
    detail: str


# ------------------------------------------------------------------ workers & jobs (spec 0020)
JobKind = Literal["benchmark", "tune", "rl"]
JobStatus = Literal["queued", "running", "done", "error", "cancelled"]
MAX_SOURCE_BYTES = 512 * 1024


class WorkerRegister(Model):
    """Register a team computer as a worker of a workspace; returns its ``worker`` token once."""

    workspace_id: ObjectId
    name: str = Field(min_length=1, max_length=128)


class WorkerInfo(Model):
    id: str
    workspace_id: str
    name: str
    created_by: str
    created_at: datetime
    last_seen_at: datetime | None
    online: bool  # seen within the last 2 minutes
    busy: bool
    info: dict[str, Any]


class WorkerRegistration(Model):
    worker: WorkerInfo
    token: str  # rfw_…, shown once; only valid for the /worker endpoints


class JobCreate(Model):
    kind: JobKind
    request: dict[str, Any]  # TrainBenchRequest / TrainTuneRequest race settings (spec 0013)
    controller_name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+\.py$")
    controller_source: str = Field(min_length=1, max_length=MAX_SOURCE_BYTES)
    params_yaml: str | None = Field(default=None, max_length=64 * 1024)


class JobInfo(Model):
    id: str
    workspace_id: str
    kind: JobKind
    status: JobStatus
    controller_name: str
    created_by: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    worker_id: str | None
    worker_name: str | None
    progress: dict[str, Any]
    result: dict[str, Any] | None
    error: str
    log_tail: list[str]
    cancel_requested: bool


class WorkerJob(Model):
    """What a worker gets when it claims a job: everything needed to run it."""

    id: str
    kind: JobKind
    request: dict[str, Any]
    controller_name: str
    controller_source: str
    params_yaml: str | None


class WorkerHeartbeat(Model):
    info: dict[str, Any] = Field(default_factory=dict[str, Any])


class JobProgress(Model):
    progress: dict[str, Any] = Field(default_factory=dict[str, Any])
    log: list[str] = Field(default_factory=list[str], max_length=500)


class JobProgressAck(Model):
    cancel: bool  # the user cancelled: stop after the current step and finish as "cancelled"


class JobFinish(Model):
    status: Literal["done", "error", "cancelled"]
    result: dict[str, Any] | None = None
    error: str = Field(default="", max_length=10_000)
