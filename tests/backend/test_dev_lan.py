"""`raceforge backend dev --lan`: a test backend that phones in the Wi-Fi (TrackScout) can reach."""

import argparse
from pathlib import Path

import pytest

from raceforge import cli


def _args(tmp_path: Path, **kw: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "data": str(tmp_path),
        "port": 8080,
        "host": "127.0.0.1",
        "lan": False,
    }
    return argparse.Namespace(**(base | kw))


def test_default_stays_on_this_machine(tmp_path: Path) -> None:
    args = _args(tmp_path)
    assert cli._dev_host(args) == "127.0.0.1"
    assert cli._backend_settings(args).public_url == "http://127.0.0.1:8080"


def test_lan_binds_everywhere_and_advertises_the_lan_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "lan_ip", lambda: "192.168.1.20")
    args = _args(tmp_path, lan=True)
    assert cli._dev_host(args) == "0.0.0.0"
    assert cli._backend_settings(args).public_url == "http://192.168.1.20:8080"


def test_explicit_host(tmp_path: Path) -> None:
    args = _args(tmp_path, host="10.0.0.5")
    assert cli._dev_host(args) == "10.0.0.5"
    assert cli._backend_settings(args).public_url == "http://10.0.0.5:8080"


def test_lan_ip_is_an_ipv4_address() -> None:
    parts = cli.lan_ip().split(".")
    assert len(parts) == 4 and all(p.isdigit() for p in parts)


def test_parser_accepts_lan(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[argparse.Namespace] = []
    monkeypatch.setattr(cli, "_cmd_backend", lambda a: seen.append(a) or 0)
    assert cli.main(["backend", "dev", "--lan", "--port", "8090"]) == 0
    assert seen[0].lan and seen[0].port == 8090


def test_dev_admin_resets_an_existing_account(tmp_path: Path) -> None:
    """Re-running `backend dev --admin USER:PW` makes the test account usable again."""
    from raceforge.backend.app import make_backend
    from raceforge.backend.migrate import upgrade

    settings = cli._backend_settings(_args(tmp_path))
    upgrade(settings.database_url)
    backend = make_backend(settings)
    backend.bootstrap_admin("admin", "old-password-123")
    cli._dev_reset_admin(backend, "admin", "admin")
    from sqlalchemy import select

    from raceforge.backend import db

    with backend.db.session() as s:
        user = s.scalars(select(db.User).where(db.User.username == "admin")).one()
        assert backend.passwords.verify(user.password_hash, "admin")
        assert not user.totp_enabled and user.role == "admin"
