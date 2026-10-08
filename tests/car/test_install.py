"""Board-side bundle installer (spec 0005 "Deploy", AC11) against a fake board tree and systemd."""

import io
import json
import os
import sys
import tarfile
from pathlib import Path

import pytest

from raceforge.car.bundle import (
    MANIFEST,
    Ev3Spec,
    RobotSpec,
    build_bundle,
    bundle_digest,
)

if sys.platform == "win32":  # before the import below: the installer needs fcntl
    pytest.skip("the installer runs on the Linux board (symlinks, flock)", allow_module_level=True)

from raceforge.car import install

TEMPLATES = Path(__file__).parents[2] / "controllers" / "templates"
ROBOT = RobotSpec(
    car_name="car",
    sensors=["front", "left", "right", "gyro"],
    max_steer_rad=0.45,
    max_speed_m_s=1.5,
    wheelbase_m=0.2,
    track_m=0.15,
    control_rate_hz=50,
)
EV3 = Ev3Spec(steer_motor_deg_per_rad=171.9, drive_counts_per_m=2046.0)


class FakeSystemd:
    """`systemctl` stand-in. `exit_for` maps a bundle name to the runtime's exit status after a
    restart (None = keeps running, 1 = waiting for the EV3 and retrying, 4 = bundle refused)."""

    def __init__(self, board: Path) -> None:
        self.board = board
        self.calls: list[tuple[str, ...]] = []
        self.exit_for: dict[str, int | None] = {}

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        if args[0] != "show":
            return ""
        name = install.load_manifest((self.board / "bundle").resolve()).name
        status = self.exit_for.get(name)
        if status is None:
            state = ("active", "running", 0)
        elif status == 1:
            state = ("activating", "auto-restart", 1)
        else:
            state = ("failed", "failed", status)
        return "ActiveState={}\nSubState={}\nExecMainStatus={}\n".format(*state)

    @property
    def restarts(self) -> int:
        return sum(1 for c in self.calls if c[0] == "restart")


@pytest.fixture
def board(tmp_path: Path) -> Path:
    root = tmp_path / "board"
    root.mkdir()
    return root.resolve()  # macOS: the temp dir is behind a symlink; the installer resolves links


@pytest.fixture
def systemd(board: Path) -> FakeSystemd:
    return FakeSystemd(board)


def make_bundle(tmp: Path, name: str, template: str = "wall_follow") -> Path:
    out = tmp / f"src-{name}"
    build_bundle(out, TEMPLATES / f"{template}.py", ROBOT, EV3, name=name)
    return out


def tar_of(bundle: Path, extra: list[tarfile.TarInfo] | None = None) -> io.BytesIO:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for f in sorted(bundle.iterdir()):
            tf.add(f, arcname=f.name)
        for info in extra or []:
            tf.addfile(info, io.BytesIO(b"x" * info.size) if info.isfile() else None)
    buf.seek(0)
    return buf


def do_install(
    board: Path, systemd: FakeSystemd, source: io.BytesIO | Path
) -> install.InstallResult:
    return install.install(source, install.Paths(board), systemctl=systemd, wait_s=0, poll_s=0)


def current(board: Path) -> str:
    return install.load_manifest((board / "bundle").resolve()).name


def test_tar_install_swaps_the_symlink_and_restarts(
    tmp_path: Path, board: Path, systemd: FakeSystemd
) -> None:
    src = make_bundle(tmp_path, "alpha")
    r = do_install(board, systemd, tar_of(src))
    assert r.ok and r.name == "alpha" and r.digest == bundle_digest(src)
    assert r.previous is None and not r.rolled_back and r.service == "running"
    link = board / "bundle"
    assert link.is_symlink()
    target = link.resolve()
    assert target.parent == board / "bundles" and target.name == f"{(r.digest or '')[:12]}-alpha"
    assert sorted(p.name for p in target.iterdir()) == sorted(p.name for p in src.iterdir())
    # Read-only for the runtime user: files 0644, directory 0755.
    assert oct(target.stat().st_mode & 0o777) == "0o755"
    assert all(oct(p.stat().st_mode & 0o777) == "0o644" for p in target.iterdir())
    assert systemd.restarts == 1
    assert not [p for p in (board / "bundles").iterdir() if p.name.startswith(".")]


def test_keeps_the_current_and_two_previous_bundles(
    tmp_path: Path, board: Path, systemd: FakeSystemd
) -> None:
    digests = []
    for name in ["a", "b", "c", "d"]:
        r = do_install(board, systemd, tar_of(make_bundle(tmp_path, name)))
        assert r.ok
        digests.append(r.digest)
    assert r.previous == digests[2]
    kept = sorted(p.name.split("-", 1)[1] for p in (board / "bundles").iterdir())
    assert kept == ["b", "c", "d"] and current(board) == "d"
    # Re-deploying a kept bundle reuses it.
    r = do_install(board, systemd, tar_of(make_bundle(tmp_path, "b")))
    assert r.ok and current(board) == "b"
    assert len(list((board / "bundles").iterdir())) == 3


def test_bad_bundles_are_refused_without_touching_the_running_one(
    tmp_path: Path, board: Path, systemd: FakeSystemd
) -> None:
    good = make_bundle(tmp_path, "good")
    assert do_install(board, systemd, tar_of(good)).ok
    restarts = systemd.restarts

    tampered = make_bundle(tmp_path, "tampered")
    with (tampered / "controller.py").open("a") as f:
        f.write("# edited after the build\n")
    missing = make_bundle(tmp_path, "missing")
    (missing / "controller.yaml").unlink()
    bad_manifest = make_bundle(tmp_path, "badmanifest")
    (bad_manifest / MANIFEST).write_text('{"schema": "car_bundle"}')

    def member(name: str, kind: bytes = tarfile.REGTYPE, size: int = 1) -> tarfile.TarInfo:
        info = tarfile.TarInfo(name)
        info.type, info.size = kind, size if kind == tarfile.REGTYPE else 0
        if kind == tarfile.SYMTYPE:
            info.linkname = "/etc/shadow"
        return info

    cases = {
        "hash mismatch: controller.py": tar_of(tampered),
        "missing controller.yaml": tar_of(missing),
        "invalid manifest": tar_of(bad_manifest),
        "unsafe path": tar_of(good, [member("../evil.py")]),
        "unsafe path ": tar_of(good, [member("/etc/cron.d/evil")]),
        "not a regular file": tar_of(good, [member("link", tarfile.SYMTYPE)]),
        "too large": tar_of(good, [member("big.bin", size=install.MAX_BYTES + 1)]),
        "not a tar stream": io.BytesIO(b"definitely not tar"),
    }
    for expected, source in cases.items():
        r = do_install(board, systemd, source)
        assert not r.ok and expected.strip() in r.detail, (expected, r.detail)
        assert current(board) == "good" and systemd.restarts == restarts
    assert not (tmp_path / "evil.py").exists() and not (board / "evil.py").exists()
    assert [p.name.split("-", 1)[1] for p in (board / "bundles").iterdir()] == ["good"]


def test_bundle_the_runtime_refuses_is_rolled_back(
    tmp_path: Path, board: Path, systemd: FakeSystemd
) -> None:
    assert do_install(board, systemd, tar_of(make_bundle(tmp_path, "old"))).ok
    systemd.exit_for["new"] = 4  # rf-runtime: bundle cannot run as deployed
    r = do_install(board, systemd, tar_of(make_bundle(tmp_path, "new")))
    assert not r.ok and r.rolled_back and r.name == "new"
    assert current(board) == "old" and systemd.restarts == 3
    assert "refused" in r.detail


def test_first_bundle_refused_cannot_roll_back(
    tmp_path: Path, board: Path, systemd: FakeSystemd
) -> None:
    systemd.exit_for["only"] = 4
    r = do_install(board, systemd, tar_of(make_bundle(tmp_path, "only")))
    assert not r.ok and not r.rolled_back and "no previous bundle" in r.detail


def test_runtime_waiting_for_the_ev3_counts_as_installed(
    tmp_path: Path, board: Path, systemd: FakeSystemd
) -> None:
    systemd.exit_for["bench"] = 1
    r = do_install(board, systemd, tar_of(make_bundle(tmp_path, "bench")))
    assert r.ok and r.service == "waiting" and "EV3" in r.detail
    systemd.exit_for["race"] = 5
    r = do_install(board, systemd, tar_of(make_bundle(tmp_path, "race")))
    assert r.ok and r.service == "failed" and "radio" in r.detail and not r.rolled_back


def test_usb_directory_install_and_result_file(
    tmp_path: Path, board: Path, systemd: FakeSystemd
) -> None:
    stick = tmp_path / "stick" / "raceforge"
    src = make_bundle(tmp_path, "usb")
    stick.mkdir(parents=True)
    (stick / "bundle").symlink_to(src)  # a stick never holds links: refused
    r = do_install(board, systemd, stick / "bundle")
    assert not r.ok and "not a regular" in r.detail
    (stick / "bundle").unlink()
    src.rename(stick / "bundle")
    rc = install.main(
        ["--from", str(stick / "bundle"), "--result", str(stick / "result.json")],
        paths=install.Paths(board),
        systemctl=systemd,
        wait_s=0,
    )
    assert rc == 0 and current(board) == "usb"
    saved = json.loads((stick / "result.json").read_text())
    assert saved["ok"] and saved["name"] == "usb"
    # A result.json planted as a symlink is never followed (the installer runs as root).
    victim = tmp_path / "victim"
    victim.write_text("untouched")
    (stick / "result.json").unlink()
    (stick / "result.json").symlink_to(victim)
    with pytest.raises(OSError):
        install.main(
            ["--from", str(stick / "bundle"), "--result", str(stick / "result.json")],
            paths=install.Paths(board),
            systemctl=systemd,
            wait_s=0,
        )
    assert victim.read_text() == "untouched"


def test_legacy_bundle_directory_becomes_the_previous_bundle(
    tmp_path: Path, board: Path, systemd: FakeSystemd
) -> None:
    # Boards set up before the symlink layout have a plain directory at /opt/raceforge/bundle.
    legacy = make_bundle(tmp_path, "legacy")
    legacy.rename(board / "bundle")
    r = do_install(board, systemd, tar_of(make_bundle(tmp_path, "fresh")))
    assert r.ok and r.previous is not None and current(board) == "fresh"
    names = sorted(p.name.split("-", 1)[1] for p in (board / "bundles").iterdir())
    assert names == ["fresh", "legacy"]


def test_cli_prints_one_json_line_and_ignores_overrides_as_root(
    tmp_path: Path,
    board: Path,
    systemd: FakeSystemd,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    src = make_bundle(tmp_path, "cli")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(tar_of(src)))
    rc = install.main(["--stdin"], paths=install.Paths(board), systemctl=systemd, wait_s=0)
    out = capsys.readouterr().out.strip().splitlines()
    assert rc == 0 and len(out) == 1 and json.loads(out[0])["digest"] == bundle_digest(src)
    # Test overrides (another prefix or systemctl) only apply when not running as root: the sudo
    # rule must not let anyone point the installer elsewhere.
    monkeypatch.setenv("RACEFORGE_INSTALL_PREFIX", str(board))
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    assert install.default_paths().root == board
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert install.default_paths().root == Path("/opt/raceforge")
