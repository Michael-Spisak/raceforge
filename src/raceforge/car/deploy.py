"""Laptop side of `raceforge deploy` (spec 0005 "Deploy"): get a bundle onto the car.

- ``deploy_ssh``: checks the bundle, streams it as tar through the system ``ssh`` (Windows 10+,
  macOS, Linux; the team's own keys and ``~/.ssh/config``) to the board's installer and checks
  that the board reports the same digest. The deploy user's key runs only the installer.
- ``deploy_usb``: writes a verified copy to ``STICK/raceforge/bundle/``; the board installs it
  when the stick is plugged in and writes ``raceforge/result.json`` back (``usb_result``).

Only the files the manifest lists are transferred (no ``__pycache__`` or editor files).
"""

import io
import os
import shutil
import subprocess
import sys
import tarfile
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from raceforge.car.bundle import bundle_digest, bundle_files, load_manifest, verify_bundle

DEFAULT_USER = "raceforge-deploy"
REMOTE_COMMAND = "sudo -n /opt/raceforge/bin/raceforge-install-bundle --stdin"
USB_DIR = "raceforge"
# How the runtime is doing after an install ("waiting": retrying until the EV3/LiDAR answer).
Service = Literal["running", "waiting", "failed", "unknown"]


class InstallResult(BaseModel):
    """What the board's installer reports (one JSON line; ``result.json`` on a USB stick)."""

    ok: bool
    name: str | None = None
    digest: str | None = None
    previous: str | None = None
    service: Service | None = None
    rolled_back: bool = False
    detail: str = ""


class DeployError(Exception):
    pass


def _checked(bundle_dir: Path) -> list[str]:
    """The bundle's files after a local check (so a broken bundle never leaves the laptop)."""
    if not (bundle_dir / "bundle.json").is_file():
        raise DeployError(f"{bundle_dir}: not a bundle (no bundle.json)")
    try:
        problems = verify_bundle(bundle_dir)
        files = bundle_files(load_manifest(bundle_dir))
    except (ValidationError, ValueError) as e:
        raise DeployError(f"{bundle_dir}: invalid manifest: {e}".splitlines()[0]) from e
    if problems:
        raise DeployError(f"{bundle_dir}: " + "; ".join(problems))
    return files


def pack_bundle(bundle_dir: Path) -> bytes:
    """The bundle as an uncompressed tar of exactly its manifest's files (stable metadata)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for name in _checked(bundle_dir):
            data = (bundle_dir / name).read_bytes()
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def deploy_ssh(
    bundle_dir: Path,
    target: str,
    ssh: Sequence[str] = ("ssh",),
    timeout_s: float = 120.0,
) -> InstallResult:
    """Install the bundle on ``[user@]host`` (default user ``raceforge-deploy``)."""
    data = pack_bundle(bundle_dir)
    digest = bundle_digest(bundle_dir)
    dest = target if "@" in target else f"{DEFAULT_USER}@{target}"
    cmd = [*ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", dest, REMOTE_COMMAND]
    try:
        r = subprocess.run(cmd, input=data, capture_output=True, timeout=timeout_s, check=False)
    except FileNotFoundError as e:
        raise DeployError(f"{cmd[0]} not found: install OpenSSH") from e
    except subprocess.TimeoutExpired as e:
        raise DeployError(f"no answer from {dest} within {timeout_s:.0f} s") from e
    lines = [line for line in r.stdout.decode(errors="replace").splitlines() if line.strip()]
    try:
        result = InstallResult.model_validate_json(lines[-1])
    except (IndexError, ValidationError) as e:
        err = r.stderr.decode(errors="replace").strip() or "no output"
        raise DeployError(f"ssh {dest} failed (exit {r.returncode}): {err}") from e
    if result.ok and result.digest != digest:
        raise DeployError(
            f"the board installed a different bundle ({result.digest}) than sent ({digest})"
        )
    return result


def deploy_usb(bundle_dir: Path, stick: Path) -> Path:
    """Write a verified copy of the bundle to ``stick/raceforge/bundle`` and return its path."""
    files = _checked(bundle_dir)
    if not stick.is_dir():
        raise DeployError(f"{stick}: not a directory (is the stick mounted?)")
    root = stick / USB_DIR
    tmp, dest = root / ".bundle.tmp", root / "bundle"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    for name in files:
        (tmp / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bundle_dir / name, tmp / name)
    problems = verify_bundle(tmp)
    if problems:
        shutil.rmtree(tmp, ignore_errors=True)
        raise DeployError(f"copy on the stick is broken: {'; '.join(problems)}")
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    (root / "result.json").unlink(missing_ok=True)
    if sys.platform != "win32":  # flush to the stick (Windows writes removable media through)
        os.sync()
    return dest


def usb_result(stick: Path) -> InstallResult | None:
    """The result the car wrote to the stick, or None if it was not plugged into a car yet."""
    path = stick / USB_DIR / "result.json"
    if not path.is_file():
        return None
    return InstallResult.model_validate_json(path.read_text(encoding="utf-8"))
