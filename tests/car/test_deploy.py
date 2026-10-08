"""`raceforge deploy --ssh/--usb` (spec 0005 "Deploy", AC12).

The SSH round trip uses a fake `ssh` that runs the real board installer against a temporary board
tree, so the tar stream, the forced-command protocol and the result line are tested end to end.
"""

import json
import shutil
import sys
import textwrap
from pathlib import Path

import pytest

from raceforge.api.deploy import DeployError, deploy_ssh, deploy_usb, usb_result
from raceforge.car.bundle import Ev3Spec, RobotSpec, build_bundle, bundle_digest
from raceforge.cli import main as cli

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
board_only = pytest.mark.skipif(
    sys.platform == "win32", reason="the installer side runs on the Linux board"
)

FAKE_SSH = textwrap.dedent(
    """
    import json, sys
    from pathlib import Path
    from raceforge.car import install

    board, log = Path(sys.argv[1]), Path(sys.argv[2])
    log.write_text(json.dumps(sys.argv[3:]))

    RUNNING = "ActiveState=active\\nSubState=running\\nExecMainStatus=0\\n"

    def systemctl(*args):
        return RUNNING if args[0] == "show" else ""

    sys.exit(install.main(["--stdin"], paths=install.Paths(board), systemctl=systemctl, wait_s=0))
    """
)


def make_bundle(tmp: Path, name: str = "wall_follow") -> Path:
    out = tmp / f"bundle-{name}"
    build_bundle(out, TEMPLATES / "wall_follow.py", ROBOT, EV3, name=name)
    (out / "__pycache__").mkdir()  # stray files on the dev machine are not deployed
    (out / "__pycache__" / "x.pyc").write_bytes(b"\0")
    return out


def fake_ssh(tmp: Path) -> tuple[list[str], Path, Path]:
    script = tmp / "fake_ssh.py"
    script.write_text(FAKE_SSH)
    board, log = tmp / "board", tmp / "ssh-args.json"
    board.mkdir()
    board = board.resolve()
    return [sys.executable, str(script), str(board), str(log)], board, log


@board_only
def test_ssh_round_trip_installs_and_checks_the_digest(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    ssh, board, log = fake_ssh(tmp_path)
    r = deploy_ssh(bundle, "car-1.local", ssh=ssh)
    assert r.ok and r.name == "wall_follow" and r.digest == bundle_digest(bundle)
    installed = (board / "bundle").resolve()
    assert sorted(p.name for p in installed.iterdir()) == [
        "bundle.json",
        "controller.py",
        "controller.yaml",
    ]
    args = json.loads(log.read_text())
    # Non-interactive, the dedicated deploy user by default, the installer as remote command.
    assert "BatchMode=yes" in args and "raceforge-deploy@car-1.local" in args
    assert args[-1] == "sudo -n /opt/raceforge/bin/raceforge-install-bundle --stdin"
    # An explicit user is kept.
    deploy_ssh(bundle, "admin@car-1.local", ssh=ssh)
    assert "admin@car-1.local" in json.loads(log.read_text())


def test_bundle_is_checked_before_connecting(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    ssh, _, log = fake_ssh(tmp_path)
    real = bundle / "controller.py"
    real.write_text(real.read_text() + "# edited after the build\n")
    with pytest.raises(DeployError, match=r"hash mismatch: controller\.py"):
        deploy_ssh(bundle, "car", ssh=ssh)
    assert not log.exists(), "ssh must not be started for a broken bundle"


def test_ssh_connection_failure_is_a_clear_error(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    failing = [
        sys.executable,
        "-c",
        "import sys; sys.stdin.buffer.read(); sys.stderr.write("
        "'ssh: connect to host car port 22: Connection refused\\n'); sys.exit(255)",
    ]
    with pytest.raises(DeployError, match="Connection refused"):
        deploy_ssh(bundle, "car", ssh=failing)


def test_ssh_detects_a_digest_mismatch(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    lying = [
        sys.executable,
        "-c",
        "import sys; sys.stdin.buffer.read(); "
        'print(\'{"ok": true, "name": "x", "digest": "' + "0" * 64 + "\"}')",
    ]
    with pytest.raises(DeployError, match="different bundle"):
        deploy_ssh(bundle, "car", ssh=lying)


def test_usb_writes_a_verified_copy_and_clears_the_old_result(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    stick = tmp_path / "stick"
    (stick / "raceforge" / "bundle").mkdir(parents=True)
    (stick / "raceforge" / "bundle" / "old.py").write_text("old")
    (stick / "raceforge" / "result.json").write_text('{"ok": true}')
    path = deploy_usb(bundle, stick)
    assert path == stick / "raceforge" / "bundle"
    assert sorted(p.name for p in path.iterdir()) == [
        "bundle.json",
        "controller.py",
        "controller.yaml",
    ]
    assert bundle_digest(path) == bundle_digest(bundle)
    assert usb_result(stick) is None
    with pytest.raises(DeployError, match="not a directory"):
        deploy_usb(bundle, tmp_path / "no-stick")


@board_only
def test_usb_copy_is_accepted_by_the_board_installer(tmp_path: Path) -> None:
    from raceforge.car import install

    bundle = make_bundle(tmp_path)
    stick = tmp_path / "stick"
    stick.mkdir()
    path = deploy_usb(bundle, stick)
    # What the board's USB service runs after mounting the stick.
    board = tmp_path / "board"
    board.mkdir()
    rc = install.main(
        ["--from", str(path), "--result", str(stick / "raceforge" / "result.json")],
        paths=install.Paths(board),
        systemctl=lambda *a: "ActiveState=active\nSubState=running\nExecMainStatus=0\n",
        wait_s=0,
    )
    assert rc == 0
    r = usb_result(stick)
    assert r is not None and r.ok and r.digest == bundle_digest(bundle)


def test_cli_usb_and_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = make_bundle(tmp_path)
    stick = tmp_path / "stick"
    stick.mkdir()
    assert cli(["deploy", str(bundle), "--usb", str(stick)]) == 0
    assert "eject the stick" in capsys.readouterr().out.lower()
    assert cli(["deploy", "--usb-result", str(stick)]) == 1  # not plugged into the car yet
    assert "no result" in capsys.readouterr().out
    (stick / "raceforge" / "result.json").write_text(
        json.dumps({"ok": False, "name": "wall_follow", "detail": "hash mismatch: controller.py"})
    )
    assert cli(["deploy", "--usb-result", str(stick)]) == 1
    assert "hash mismatch" in capsys.readouterr().out
    assert cli(["deploy", "--usb", str(stick)]) == 2  # a bundle is required
    shutil.rmtree(stick)


def test_cli_refuses_a_broken_bundle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = make_bundle(tmp_path)
    (bundle / "controller.yaml").unlink()
    stick = tmp_path / "stick"
    stick.mkdir()
    assert cli(["deploy", str(bundle), "--usb", str(stick)]) == 1
    assert "missing controller.yaml" in capsys.readouterr().err
    assert not (stick / "raceforge" / "bundle").exists()
