"""Spec 0006 AC9 (code part): migrations build exactly the ORM schema and run forward on an
existing database without touching its data."""

import os
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from raceforge.backend.db import Base
from raceforge.backend.migrate import upgrade


def _url(tmp_path: Path) -> str:
    return os.environ.get("RF_TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'm.db'}"


def test_migrations_match_models_and_are_rerunnable(tmp_path: Path) -> None:
    url = _url(tmp_path)
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    upgrade(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, username, display_name, password_hash, role,"
                " totp_enabled, disabled, created_at) VALUES ('u1', 'kept', 'Kept', 'x', 'member',"
                " false, false, CURRENT_TIMESTAMP)"
            )
        )
    upgrade(url)  # forward run on an existing, populated database is a no-op
    with engine.connect() as conn:
        assert conn.execute(text("SELECT username FROM users")).scalar_one() == "kept"
    assert "versions" in inspect(engine).get_table_names()
    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    engine.dispose()
