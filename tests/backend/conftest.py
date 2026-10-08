"""Backend fixtures. SQLite + a local blob directory by default; set RF_TEST_DATABASE_URL
(PostgreSQL) and RF_TEST_S3_ENDPOINT to run the same tests against the real services (CI)."""

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from raceforge.backend.app import create_backend_app
from raceforge.backend.blobs import BlobStore, FsBlobStore, S3BlobStore
from raceforge.backend.db import Base, Database
from raceforge.backend.security import totp_now
from raceforge.backend.service import Backend
from raceforge.backend.settings import Settings

ADMIN_PW = "admin-password-1"
MEMBER_PW = "member-password-1"


@dataclass
class Clock:
    now: datetime = field(default_factory=lambda: datetime(2026, 10, 8, 12, 0, tzinfo=UTC))

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


@dataclass
class Env:
    backend: Backend
    client: TestClient
    clock: Clock

    def login(self, username: str, password: str, totp_secret: str | None = None) -> dict[str, str]:
        body: dict[str, str] = {"username": username, "password": password, "client": "test"}
        if totp_secret:
            body["totp"] = totp_now(totp_secret, self.clock())
        r = self.client.post("/api/v1/auth/login", json=body)
        assert r.status_code == 200, r.text
        self.client.cookies.clear()  # tests authenticate with bearer tokens only
        return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def clock() -> Clock:
    return Clock()


def _store(tmp_path: Path, settings: Settings) -> BlobStore:
    if os.environ.get("RF_TEST_S3_ENDPOINT"):
        return S3BlobStore(settings)
    return FsBlobStore(tmp_path / "blobs")


@pytest.fixture
def env(tmp_path: Path, clock: Clock) -> Iterator[Env]:
    url = os.environ.get("RF_TEST_DATABASE_URL", "sqlite://")
    settings = Settings(
        database_url=url,
        blob_dir=tmp_path / "blobs",
        fast_password_hash=True,
        secure_cookies=False,
        max_part_bytes=1024,
        data_path=tmp_path,
        s3_endpoint=os.environ.get("RF_TEST_S3_ENDPOINT", "localhost:9000"),
        s3_access_key=os.environ.get("RF_TEST_S3_ACCESS_KEY", "rftest"),
        s3_secret_key=os.environ.get("RF_TEST_S3_SECRET_KEY", "rftest"),
        s3_bucket=f"rf-test-{os.getpid()}",
    )
    database = Database(url)
    Base.metadata.drop_all(database.engine)
    Base.metadata.create_all(database.engine)
    backend = Backend(settings, database, _store(tmp_path, settings), clock)
    with TestClient(create_backend_app(backend)) as client:
        yield Env(backend, client, clock)
    Base.metadata.drop_all(database.engine)
    database.engine.dispose()


@dataclass
class Team:
    admin: dict[str, str]
    member: dict[str, str]
    admin_totp: str
    ws: str


@pytest.fixture
def team(env: Env) -> Team:
    """Admin with TOTP, one invited member, one workspace."""
    env.backend.bootstrap_admin("admin", ADMIN_PW)
    h = env.login("admin", ADMIN_PW)
    secret = env.client.post("/api/v1/auth/totp/setup", headers=h).json()["secret"]
    r = env.client.post(
        "/api/v1/auth/totp/verify", headers=h, json={"code": totp_now(secret, env.clock())}
    )
    assert r.status_code == 200, r.text
    invite = env.client.post("/api/v1/invites", headers=h, json={}).json()
    r = env.client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": invite["token"],
            "username": "anna",
            "display_name": "Anna",
            "password": MEMBER_PW,
        },
    )
    assert r.status_code == 201, r.text
    member = env.login("anna", MEMBER_PW)
    ws = env.client.post("/api/v1/workspaces", headers=member, json={"name": "Season 1"}).json()
    return Team(admin=h, member=member, admin_totp=secret, ws=ws["id"])
