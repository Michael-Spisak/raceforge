"""Board-side bundle installer (spec 0005 "Deploy"): ``raceforge-install-bundle``.

Runs as root on the car's board, started by the deploy user's forced SSH command (``--stdin``, tar
stream) or by the USB auto-install service (``--from DIR``). It never trusts its input:

1. take the bundle into a staging directory: regular files only, no links or ``..`` paths, size
   limits; from a directory only the files the manifest lists are read (a stick written on a Mac
   also holds ``._*`` files);
2. check the manifest and every file hash; a bad bundle is refused and the running one stays;
3. store it in ``bundles/<digest[:12]>-<name>/`` (root-owned, read-only for the runtime), swap the
   ``bundle`` symlink atomically, restart ``rf-runtime.service`` and watch it for a few seconds;
   a bundle the runtime refuses (exit 4) is rolled back to the previous one;
4. keep the current and two previous bundles, print one JSON line with the result.

Linux only (symlinks, ``flock``); never imported by the laptop side.
"""

import argparse
import fcntl
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from pydantic import ValidationError

from raceforge.car.bundle import (
    MANIFEST,
    BundleManifest,
    bundle_digest,
    bundle_files,
    load_manifest,
    verify_bundle,
)
from raceforge.car.deploy import InstallResult, Service

if sys.platform == "win32":  # the board is Linux; also tells type checkers the rest is POSIX-only
    raise ImportError("raceforge.car.install runs on the Linux board only")

__all__ = ["InstallResult", "Paths", "install", "load_manifest", "main"]

UNIT = "rf-runtime.service"
MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 64
KEEP = 3  # the current bundle and two previous ones
WAIT_S = 5.0  # how long the runtime is watched after a restart
# rf-runtime exit codes (car_runtime/rf-runtime/src/main.rs).
EXIT_RETRY, EXIT_USAGE, EXIT_FAULT, EXIT_CONFIG, EXIT_NOT_ARMED = 1, 2, 3, 4, 5

Systemctl = Callable[..., str]


class InstallError(Exception):
    pass


@dataclass(frozen=True)
class Paths:
    """Board layout below ``root`` (``/opt/raceforge``)."""

    root: Path

    @property
    def bundles(self) -> Path:
        return self.root / "bundles"

    @property
    def current(self) -> Path:
        return self.root / "bundle"

    @property
    def lock(self) -> Path:
        return self.root / ".install.lock"


def default_paths() -> Paths:
    # Test override only for an unprivileged user: as root (via the deploy user's sudo rule) the
    # installer always works on /opt/raceforge.
    prefix = os.environ.get("RACEFORGE_INSTALL_PREFIX")
    if prefix and os.geteuid() != 0:
        return Paths(Path(prefix))
    return Paths(Path("/opt/raceforge"))


def default_systemctl() -> Systemctl:
    exe = os.environ.get("RACEFORGE_SYSTEMCTL") if os.geteuid() != 0 else None

    def run(*args: str) -> str:
        r = subprocess.run(
            [exe or "systemctl", *args], capture_output=True, text=True, check=False, timeout=30
        )
        if r.returncode != 0 and args[0] != "show":
            raise InstallError(f"systemctl {' '.join(args)}: {r.stderr.strip()}")
        return r.stdout

    return run


# --- intake ----------------------------------------------------------------------------------


def _safe_name(name: str) -> PurePosixPath:
    p = PurePosixPath(name)
    if not name or p.is_absolute() or ".." in p.parts or "\\" in name:
        raise InstallError(f"unsafe path in bundle: {name!r}")
    return p


def _extract_tar(stream: BinaryIO, staging: Path) -> None:
    total = files = 0
    try:
        with tarfile.open(fileobj=stream, mode="r|") as tf:
            for m in tf:
                rel = _safe_name(m.name)
                if m.isdir():
                    (staging / rel).mkdir(parents=True, exist_ok=True)
                    continue
                if not m.isfile():
                    raise InstallError(f"not a regular file in bundle: {m.name!r}")
                files += 1
                total += m.size
                if files > MAX_FILES or total > MAX_BYTES:
                    raise InstallError(
                        f"bundle too large (max {MAX_FILES} files, {MAX_BYTES} bytes)"
                    )
                src = tf.extractfile(m)
                if src is None:
                    raise InstallError(f"unreadable member {m.name!r}")
                dest = staging / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open("xb") as out:
                    shutil.copyfileobj(src, out)
    except tarfile.TarError as e:
        raise InstallError(f"not a tar stream ({e})") from e


def _copy_dir(src: Path, staging: Path) -> None:
    if src.is_symlink() or not src.is_dir():
        raise InstallError(f"{src}: not a regular directory")
    manifest = _read_manifest(src)
    total = 0
    for name in bundle_files(manifest):
        path = src / _safe_name(name)
        if path.is_symlink() or not path.is_file():
            raise InstallError(f"missing or not a regular file: {name}")
        total += path.stat().st_size
        if total > MAX_BYTES:
            raise InstallError(f"bundle too large (max {MAX_BYTES} bytes)")
        dest = staging / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)


def _read_manifest(bundle_dir: Path) -> BundleManifest:
    try:
        return load_manifest(bundle_dir)
    except FileNotFoundError as e:
        raise InstallError(f"missing {MANIFEST}") from e
    except (ValidationError, ValueError) as e:
        raise InstallError(f"invalid manifest: {e}".splitlines()[0]) from e


def _check(staging: Path) -> BundleManifest:
    manifest = _read_manifest(staging)
    for name in bundle_files(manifest):
        _safe_name(name)
    problems = verify_bundle(staging)
    if problems:
        raise InstallError("; ".join(problems))
    return manifest


# --- board state -----------------------------------------------------------------------------


@contextmanager
def _locked(paths: Paths) -> Iterator[None]:
    with paths.lock.open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _dir_name(digest: str, manifest: BundleManifest) -> str:
    name = "".join(c if c.isalnum() or c in "-_." else "_" for c in manifest.name)[:40]
    return f"{digest[:12]}-{name}"


def _touch_newest(path: Path, paths: Paths) -> None:
    """Mark `path` as the most recently used bundle (pruning keeps the newest)."""
    newest = max((p.stat().st_mtime for p in _bundle_dirs(paths)), default=time.time())
    t = max(newest + 1.0, time.time())
    os.utime(path, (t, t))


def _bundle_dirs(paths: Paths) -> list[Path]:
    return [p for p in paths.bundles.iterdir() if p.is_dir() and not p.name.startswith(".")]


def _migrate_legacy(paths: Paths) -> Path | None:
    """Boards set up before the symlink layout have a plain directory at ``bundle``: move it into
    ``bundles/`` and return it (it becomes the previous bundle)."""
    cur = paths.current
    if cur.is_symlink() or not cur.is_dir():
        return None
    try:
        target = paths.bundles / _dir_name(bundle_digest(cur), load_manifest(cur))
    except (OSError, ValidationError, ValueError):
        target = paths.bundles / f"legacy-{time.time_ns()}"
    if target.exists():
        shutil.rmtree(cur)
    else:
        cur.rename(target)
        os.utime(target, (0, 0))  # oldest: kept as "previous", pruned first
    return target


def _current_target(paths: Paths) -> Path | None:
    cur = paths.current
    return cur.resolve() if cur.is_symlink() and cur.resolve().is_dir() else None


def _swap(paths: Paths, target: Path) -> None:
    tmp = paths.root / ".bundle.new"
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(target)
    os.replace(tmp, paths.current)


def _prune(paths: Paths, keep: set[Path]) -> None:
    dirs = sorted(_bundle_dirs(paths), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in dirs[KEEP:]:
        if p.resolve() not in keep:
            shutil.rmtree(p)


def _service_state(systemctl: Systemctl) -> tuple[Service, int]:
    out = systemctl("show", UNIT, "-p", "ActiveState", "-p", "SubState", "-p", "ExecMainStatus")
    kv = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    status = int(kv.get("ExecMainStatus", "0") or 0)
    if kv.get("ActiveState") == "active":
        return "running", status
    if kv.get("ActiveState") == "activating" and kv.get("SubState") == "auto-restart":
        return "waiting", status
    if kv.get("ActiveState") in ("failed", "inactive"):
        return "failed", status
    return "unknown", status


def _restart_and_watch(systemctl: Systemctl, wait_s: float, poll_s: float) -> tuple[Service, int]:
    systemctl("restart", UNIT)
    deadline = time.monotonic() + wait_s
    while True:
        state, status = _service_state(systemctl)
        if state == "failed" or time.monotonic() >= deadline:
            return state, status
        time.sleep(poll_s)


# --- install ---------------------------------------------------------------------------------


def install(
    source: BinaryIO | Path,
    paths: Paths,
    systemctl: Systemctl,
    wait_s: float = WAIT_S,
    poll_s: float = 0.25,
) -> InstallResult:
    """Install a bundle from a tar stream or a directory; never raises for a bad bundle."""
    paths.bundles.mkdir(parents=True, exist_ok=True)
    with _locked(paths):
        staging = Path(tempfile.mkdtemp(prefix=".incoming-", dir=paths.bundles))
        try:
            try:
                if isinstance(source, Path):
                    _copy_dir(source, staging)
                else:
                    _extract_tar(source, staging)
                manifest = _check(staging)
                digest = bundle_digest(staging)
            except InstallError as e:
                return InstallResult(ok=False, detail=str(e))
            try:
                final = _store(staging, paths, manifest, digest)
                previous = _migrate_legacy(paths) or _current_target(paths)
                prev_digest = bundle_digest(previous) if previous else None
                _touch_newest(final, paths)
                _swap(paths, final)
            except OSError as e:
                return InstallResult(ok=False, name=manifest.name, digest=digest, detail=str(e))
            result = InstallResult(ok=True, name=manifest.name, digest=digest, previous=prev_digest)
            try:
                return _start(result, paths, previous, systemctl, wait_s, poll_s)
            except InstallError as e:
                result.ok, result.service, result.detail = False, "unknown", str(e)
                return result
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            if paths.current.is_symlink():
                _prune(paths, {paths.current.resolve()})


def _store(staging: Path, paths: Paths, manifest: BundleManifest, digest: str) -> Path:
    """Move the checked files into their final, root-owned and read-only bundle directory."""
    final = paths.bundles / _dir_name(digest, manifest)
    if final.exists():
        return final  # the same bundle deployed again
    final.mkdir()
    for name in bundle_files(manifest):
        dest = final / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging / name, dest)
    for p in [final, *final.rglob("*")]:
        p.chmod(0o755 if p.is_dir() else 0o644)
    return final


def _start(
    result: InstallResult,
    paths: Paths,
    previous: Path | None,
    systemctl: Systemctl,
    wait_s: float,
    poll_s: float,
) -> InstallResult:
    state, status = _restart_and_watch(systemctl, wait_s, poll_s)
    result.service = state
    if state == "waiting":
        result.detail = "installed; runtime waiting for the EV3/LiDAR link (retrying)"
    elif state == "running":
        result.detail = "installed; runtime running"
    elif status == EXIT_CONFIG:
        result.ok = False
        if previous is None:
            result.detail = (
                "the runtime refused the bundle (exit 4) and there is no previous bundle to "
                "roll back to"
            )
        else:
            _swap(paths, previous)
            _touch_newest(previous, paths)
            result.rolled_back = True
            state, _ = _restart_and_watch(systemctl, wait_s, poll_s)
            result.detail = f"the runtime refused the bundle (exit 4); rolled back ({state})"
    elif status == EXIT_NOT_ARMED:
        result.detail = (
            "installed; race mode not armed: a radio may be active (setup-board.sh --race)"
        )
    elif status == EXIT_FAULT:
        result.ok = False
        result.detail = "installed, but the runtime stopped with a driving fault right after start"
    else:
        result.ok = state != "failed"
        result.detail = f"installed; runtime {state} (exit {status}, see journalctl -u {UNIT})"
    return result


def _write_result(path: Path, text: str) -> None:
    """Write the USB result file as root without ever following a symlink planted there."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def main(
    argv: list[str] | None = None,
    paths: Paths | None = None,
    systemctl: Systemctl | None = None,
    wait_s: float = WAIT_S,
) -> int:
    ap = argparse.ArgumentParser(prog="raceforge-install-bundle", description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--stdin", action="store_true", help="read the bundle as a tar stream")
    src.add_argument("--from", dest="src", metavar="DIR", help="install from a directory (USB)")
    ap.add_argument("--result", metavar="FILE", help="also write the result as JSON to FILE")
    args = ap.parse_args(argv)
    source: BinaryIO | Path = Path(args.src) if args.src else sys.stdin.buffer
    result = install(
        source,
        paths or default_paths(),
        systemctl or default_systemctl(),
        wait_s=wait_s,
    )
    line = result.model_dump_json()
    print(line, flush=True)
    if args.result:
        _write_result(Path(args.result), line + "\n")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
