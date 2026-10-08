"""Backend configuration from ``RF_*`` environment variables (never from the repo; ADR-0009)."""

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class Settings(BaseModel):
    database_url: str = "sqlite:///./raceforge-backend.db"
    blob_backend: Literal["fs", "s3"] = "fs"
    blob_dir: Path = Path("./raceforge-blobs")
    s3_endpoint: str = "s3:8333"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "raceforge"
    s3_secure: bool = False
    public_url: str = "http://localhost:8080"
    access_ttl_s: int = 15 * 60
    refresh_ttl_s: int = 30 * 24 * 3600
    invite_ttl_s: int = 7 * 24 * 3600
    trash_days: int = 30
    max_part_bytes: int = 50 * 1024 * 1024
    login_max_failures: int = 5
    login_window_s: int = 300
    data_path: Path = Path("/")
    upload_tmp_dir: Path | None = None
    fast_password_hash: bool = False  # tests only: cheap argon2 parameters
    secure_cookies: bool = True

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "Settings":
        env = os.environ if environ is None else environ
        values: dict[str, str] = {}
        for name in cls.model_fields:
            key = f"RF_{name.upper()}"
            if key in env:
                values[name] = env[key]
        return cls.model_validate(values)
