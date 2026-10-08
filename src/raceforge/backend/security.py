"""Password hashing (argon2id), opaque tokens (stored as SHA-256), TOTP (RFC 6238)."""

import hashlib
import secrets
from datetime import datetime

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


class Passwords:
    def __init__(self, fast: bool = False) -> None:
        self._ph = (
            PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
            if fast
            else (PasswordHasher())
        )

    def hash(self, password: str) -> str:
        return self._ph.hash(password)

    def verify(self, hashed: str, password: str) -> bool:
        try:
            return self._ph.verify(hashed, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False


def new_secret(prefix: str) -> str:
    """Random token like ``rfa_…``; the prefix shows the token type in logs and secret scanners."""
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def token_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name="RaceForge")


def totp_ok(secret: str, code: str, now: datetime) -> bool:
    return pyotp.TOTP(secret).verify(code.strip(), for_time=now, valid_window=1)


def totp_now(secret: str, now: datetime) -> str:
    return pyotp.TOTP(secret).at(now)
