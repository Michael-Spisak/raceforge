"""Database schema (SQLAlchemy 2). Changes need an Alembic migration (expand/contract, ADR-0019)."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Dialect, Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

ID = String(36)


class UtcDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC timestamps on every database (SQLite drops the offset)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("naive datetime")
        return value.astimezone(UTC) if value is not None else None

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))  # admin | member
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class Invite(Base):
    __tablename__ = "invites"
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    role: Mapped[str] = mapped_column(String(16))
    email: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[str] = mapped_column(ID, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    used_by: Mapped[str | None] = mapped_column(ID, ForeignKey("users.id"))


class Token(Base):
    """Access, refresh and API tokens; only SHA-256 hashes of the secrets are stored."""

    __tablename__ = "tokens"
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # access | refresh | api
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    scopes: Mapped[list[str]] = mapped_column(JSON)
    client: Mapped[str] = mapped_column(String(32), default="")
    totp_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    session_id: Mapped[str | None] = mapped_column(ID)  # links access + refresh of one login
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    created_by: Mapped[str] = mapped_column(ID, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class Object(Base):
    __tablename__ = "objects"
    __table_args__ = (UniqueConstraint("workspace_id", "slug"),)
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ID, ForeignKey("workspaces.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    slug: Mapped[str] = mapped_column(String(64))
    tags: Mapped[list[str]] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(ID, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    copied_from: Mapped[str | None] = mapped_column(ID)  # version id (lineage across workspaces)
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, index=True)
    deleted_by: Mapped[str | None] = mapped_column(ID)


class Version(Base):
    """Immutable: rows are only ever inserted (and purged together with their object)."""

    __tablename__ = "versions"
    __table_args__ = (UniqueConstraint("object_id", "semver"),)
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    object_id: Mapped[str] = mapped_column(ID, ForeignKey("objects.id"), index=True)
    semver: Mapped[str] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(128))
    message: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(ID, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    content_hash: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)  # canonical JSON (spec 0001)
    parents: Mapped[list[str]] = mapped_column(JSON)
    branch: Mapped[bool] = mapped_column(Boolean, default=False)
    token_id: Mapped[str | None] = mapped_column(ID)


class Draft(Base):
    __tablename__ = "drafts"
    object_id: Mapped[str] = mapped_column(ID, ForeignKey("objects.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id"), primary_key=True)
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)


class Blob(Base):
    __tablename__ = "blobs"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    size: Mapped[int] = mapped_column(BigInteger)
    created_by: Mapped[str] = mapped_column(ID, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class Upload(Base):
    __tablename__ = "uploads"
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(BigInteger)
    part_size: Mapped[int] = mapped_column(BigInteger)
    created_by: Mapped[str] = mapped_column(ID, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class UploadPart(Base):
    __tablename__ = "upload_parts"
    upload_id: Mapped[str] = mapped_column(ID, ForeignKey("uploads.id"), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))


class Worker(Base):
    """A team computer that runs training/benchmark jobs (spec 0020); one ``worker`` token each."""

    __tablename__ = "workers"
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ID, ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    token_id: Mapped[str] = mapped_column(ID, ForeignKey("tokens.id"), unique=True)
    created_by: Mapped[str] = mapped_column(ID, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    last_seen_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    info: Mapped[dict[str, Any]] = mapped_column(JSON)  # cpu cores, gpu, os, version
    removed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class Job(Base):
    """A queued benchmark/tuning job (spec 0020); inputs and results travel inline (JSON)."""

    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ID, ForeignKey("workspaces.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # benchmark | tune
    status: Mapped[str] = mapped_column(
        String(16), index=True
    )  # queued|running|done|error|cancelled
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    progress: Mapped[dict[str, Any]] = mapped_column(JSON)
    log: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    worker_id: Mapped[str | None] = mapped_column(ID, ForeignKey("workers.id"))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(ID, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    # part C: claim order, targeting, version matching, leases
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # 0 normal … 2
    target_worker_id: Mapped[str | None] = mapped_column(ID)
    raceforge_version: Mapped[str | None] = mapped_column(String(32))
    attempt: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    queued_at: Mapped[datetime | None] = mapped_column(UtcDateTime)  # last (re)queue time


class AuditEntry(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    user_id: Mapped[str | None] = mapped_column(ID)
    token_id: Mapped[str | None] = mapped_column(ID)
    client: Mapped[str] = mapped_column(String(32), default="")
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(128), default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSON)


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        from sqlalchemy.pool import StaticPool

        kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
        engine = create_engine(url, **kwargs)

        @event.listens_for(engine, "connect")
        def _fk(conn: Any, _record: Any) -> None:  # pyright: ignore[reportUnusedFunction]
            conn.execute("PRAGMA foreign_keys=ON")

        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20)


class Database:
    def __init__(self, url: str) -> None:
        self.engine = make_engine(url)
        self._factory = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._factory() as s:
            try:
                yield s
                s.commit()
            except BaseException:
                s.rollback()
                raise

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
