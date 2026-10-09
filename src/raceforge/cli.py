"""Command-line entry point (`raceforge`)."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from raceforge import __version__

if TYPE_CHECKING:
    from raceforge.api.deploy import InstallResult
    from raceforge.api.train import BenchConfig
    from raceforge.backend.service import Backend
    from raceforge.backend.settings import Settings


def _cmd_parts_fetch(args: argparse.Namespace) -> int:
    from raceforge.parts.ldraw import fetch_library, library_dir

    target = Path(args.dir) if args.dir else library_dir()
    print(f"Downloading the LDraw library (~146 MB) to {target} …")
    fetch_library(target)
    print("done")
    return 0


def _cmd_parts_build(args: argparse.Namespace) -> int:
    from raceforge.parts.catalogue import build_bboxes
    from raceforge.parts.ldraw import library_dir

    out = build_bboxes(
        Path(args.dir) if args.dir else library_dir(), Path(__file__).parent / "parts" / "data"
    )
    print(f"wrote {out}")
    return 0


def _cmd_quickstart(args: argparse.Namespace) -> int:
    import yaml

    from raceforge.construct.derive import derive
    from raceforge.construct.ldraw_export import export_mpd
    from raceforge.construct.quickstart import QuickStartParams, generate, vehicle_spec
    from raceforge.core.io import dump
    from raceforge.parts.catalogue import Catalogue
    from raceforge.sim.mjcf import build_mjcf

    raw = yaml.safe_load(Path(args.params).read_text()) if args.params else {}
    params = QuickStartParams.model_validate(raw or {})
    cat = Catalogue.load()
    result = generate(params, cat)
    spec = vehicle_spec(result, cat)
    derived = derive(result.assembly, cat, spec)
    xml, _ = build_mjcf(result.assembly, cat, spec)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "assembly.json").write_text(dump(result.assembly) + "\n")
    (out / "car.xml").write_text(xml)
    (out / "car.mpd").write_text(export_mpd(result.assembly, cat))
    (out / "derived.json").write_text(json.dumps(asdict(derived), indent=2) + "\n")
    print(
        f"mass {derived.mass_kg * 1000:.0f} g · top speed {derived.top_speed_m_s:.2f} m/s · "
        f"turning radius {derived.turning_radius_m:.2f} m → {out}"
    )
    for w in derived.warnings:
        print(f"warning [{w.code}]: {w.message}", file=sys.stderr)
    return 0


def _cmd_sim(args: argparse.Namespace) -> int:
    import time

    from raceforge.sim.runner import SimOptions, run_once

    opts = SimOptions(
        controller=Path(args.controller),
        params=Path(args.params) if args.params else None,
        track=args.track,
        loop=not args.open,
        length_m=args.length,
        laps=args.laps,
        opponents=args.opponents,
        seed=args.seed,
        record=Path(args.record) if args.record else None,
        quickstart=Path(args.quickstart) if args.quickstart else None,
        max_time_s=args.max_time,
    )
    if not args.watch:
        return 0 if run_once(opts, print) else 1
    print(f"watching {opts.controller} - save the file to re-run, Ctrl+C to stop")
    last = None
    try:
        while True:
            mtime = opts.controller.stat().st_mtime
            if mtime != last:
                last = mtime
                try:
                    run_once(opts, print)
                except Exception as exc:  # show controller errors and keep watching
                    print(f"error: {exc}")
            time.sleep(1.0)
    except KeyboardInterrupt:
        return 0


def _cmd_ui(args: argparse.Namespace) -> int:
    import socket
    import webbrowser

    import uvicorn

    from raceforge.server.app import FRONTEND_DIST, create_app

    port = args.port
    if port == 0:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
    url = f"http://127.0.0.1:{port}/"
    print(f"RACEFORGE_ENGINE_URL={url}", flush=True)  # read by the Electron shell
    if args.browser:
        if not FRONTEND_DIST.is_dir():
            print("frontend not built yet: run `npm run build` in frontend/")
        webbrowser.open(url)
    app = create_app()
    app.state.workspace().ws.start_background(30.0)  # spec 0006: background sync
    app.state.workspace().relay.start_background(60.0)  # spec 0007: TrackScout relay
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


def lan_ip() -> str:
    """This machine's address in the local network (the interface used for outgoing traffic)."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))  # UDP connect sends nothing; it only picks the route
            return str(s.getsockname()[0])
        except OSError:
            return socket.gethostbyname(socket.gethostname())


def _dev_host(args: argparse.Namespace) -> str:
    return "0.0.0.0" if getattr(args, "lan", False) else getattr(args, "host", "127.0.0.1")


def _dev_public_host(args: argparse.Namespace) -> str:
    """Address that phones and other laptops use (invite links, TrackScout pairing)."""
    host = _dev_host(args)
    return lan_ip() if host in ("0.0.0.0", "::") else host


def _backend_settings(args: argparse.Namespace) -> "Settings":
    from raceforge.backend.settings import Settings

    settings = Settings.from_env()
    if getattr(args, "data", None):  # local dev server: SQLite + blob directory
        data = Path(args.data).resolve()
        data.mkdir(parents=True, exist_ok=True)
        settings = settings.model_copy(
            update={
                "database_url": f"sqlite:///{data / 'backend.db'}",
                "blob_backend": "fs",
                "blob_dir": data / "blobs",
                "data_path": data,
                "secure_cookies": False,
                "public_url": f"http://{_dev_public_host(args)}:{args.port}",
            }
        )
    return settings


def _dev_reset_admin(backend: "Backend", username: str, password: str) -> bool:
    """`backend dev --admin USER:PW` on an existing test database: make the account usable again.

    Returns False when there is no such user (the bootstrap error was something else)."""
    from sqlalchemy import select

    from raceforge.backend import db
    from raceforge.backend.models import Role

    with backend.db.session() as s:
        user = s.scalars(select(db.User).where(db.User.username == username)).one_or_none()
        if user is None:
            return False
        user.password_hash = backend.passwords.hash(password)
        user.role, user.disabled = Role.ADMIN.value, False
        user.totp_enabled, user.totp_secret = False, None
        return True


def _cmd_backend(args: argparse.Namespace) -> int:
    import getpass
    import os

    from raceforge.backend.migrate import upgrade

    settings = _backend_settings(args)
    cmd = args.backend_command
    if cmd in ("migrate", "dev"):
        upgrade(settings.database_url)
        if cmd == "migrate":
            print("database is up to date")
            return 0
    from raceforge.backend.app import create_backend_app, make_backend
    from raceforge.backend.service import ApiError

    backend = make_backend(settings)
    if cmd == "bootstrap-admin" or (cmd == "dev" and args.admin):
        if cmd == "dev":
            username, _, password = args.admin.partition(":")
        else:
            username = args.username
            password = os.environ.get("RF_ADMIN_PASSWORD") or getpass.getpass("Password: ")
        try:
            backend.bootstrap_admin(username, password)
            print(f"admin {username!r} created; log in and set up TOTP 2FA")
        except ApiError as exc:
            if cmd == "bootstrap-admin" or not _dev_reset_admin(backend, username, password):
                print(f"error: {exc.detail}")
                return 1
            # test server: --admin always wins for an existing account
            print(f"admin {username!r} exists: password reset, 2FA off (dev server only)")
        if cmd == "dev" and args.admin_totp:
            from sqlalchemy import select

            from raceforge.backend import db

            with backend.db.session() as s:
                user = s.scalars(select(db.User).where(db.User.username == username)).one()
                user.totp_secret, user.totp_enabled = args.admin_totp, True
        if cmd == "bootstrap-admin":
            return 0
    if cmd == "purge-trash":
        print(f"purged {backend.purge_trash()} objects")
        return 0
    if cmd == "export-blobs":
        print(f"exported {backend.export_blobs(Path(args.dir))} new blobs")
        return 0
    if cmd == "import-blobs":
        print(f"restored {backend.import_blobs(Path(args.dir))} blobs")
        return 0
    import uvicorn

    host = _dev_host(args) if cmd == "dev" else args.host
    public = _dev_public_host(args) if cmd == "dev" else host
    print(f"RACEFORGE_BACKEND_URL=http://{public}:{args.port}/", flush=True)
    if cmd == "dev" and host in ("0.0.0.0", "::"):
        print(
            f"Reachable in the local network (e.g. TrackScout): log in on the Team tab with "
            f"http://{public}:{args.port} — test data only, plain HTTP, no TLS.",
            flush=True,
        )
    uvicorn.run(
        create_backend_app(backend),
        host=host,
        port=args.port,
        log_level="info" if cmd == "serve" else "warning",
        # Only `serve` sits behind a reverse proxy; a LAN dev server must not trust client headers.
        proxy_headers=cmd == "serve",
        forwarded_allow_ips="*" if cmd == "serve" else None,
    )
    return 0


def _bench_config(args: argparse.Namespace) -> "BenchConfig":
    from raceforge.api.train import BenchConfig

    return BenchConfig(
        tracks=args.tracks,
        length_m=args.length,
        laps=args.laps,
        opponents=args.opponents,
        max_time_s=args.max_time,
        workers=args.workers,
    )


def _cmd_train_rl(args: argparse.Namespace) -> int:
    """Spec 0022: PPO in the simulator → ONNX policy in a params YAML for onnx_policy.py."""
    from raceforge.api.train import ONNX_TEMPLATE, EnvConfig, RLConfig, RLProgress, train_ppo

    try:
        bench = _bench_config(args)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    out = Path(args.out)
    env = EnvConfig(
        length_m=args.length, laps=args.laps, opponents=args.opponents, max_time_s=args.max_time
    )

    def line(p: RLProgress) -> None:
        mean = "-" if p.mean_reward is None else f"{p.mean_reward:.1f}"
        print(f"  {p.steps}/{p.total} steps · mean episode reward {mean}", flush=True)

    try:
        res = train_ppo(
            RLConfig(
                steps=args.steps, train_tracks=args.train_tracks, env=env, bench=bench, out=out
            ),
            line,
        )
    except ImportError as e:
        print(f"error: {e}: install the extra: uv sync --extra rl", file=sys.stderr)
        return 1
    v = res.validation
    if v is not None:
        print(f"held-out score {v.score:.1f} · finished {v.finished_rate:.0%}")
    print(f"policy written to {out}")
    print(f"deploy it with: raceforge bundle {ONNX_TEMPLATE} --params {out} ...")
    return 0


def _cmd_train(args: argparse.Namespace) -> int:
    from raceforge.api.train import RunResult, Trial, TuneConfig, benchmark, tune

    controller = Path(args.controller)
    try:
        bench = _bench_config(args)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    def run_line(r: RunResult) -> None:
        state = f"finished in {r.time_s:.1f} s" if r.finished else f"DNF ({r.fraction:.0%})"
        extra = f" · {r.error}" if r.error else ""
        print(f"  corridor {r.seed}: {state} · walls {r.wall_contacts}{extra}", flush=True)

    if args.train_command == "benchmark":
        res = benchmark(controller, Path(args.params) if args.params else None, bench, run_line)
        print(f"score {res.score:.1f} (lower is better) · finished {res.finished_rate:.0%}")
        return 0

    def trial_line(t: Trial, best: Trial) -> None:
        print(f"  trial {t.number}: {t.score:.1f} (best {best.score:.1f})", flush=True)

    out = Path(args.out) if args.out else controller.with_name(f"{controller.stem}.tuned.yaml")
    try:
        res = tune(
            controller,
            TuneConfig(
                trials=args.trials,
                timeout_s=args.timeout,
                train_tracks=args.train_tracks,
                bench=bench,
                out=out,
            ),
            trial_line,
        )
    except (ValueError, ImportError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(
        f"train score {res.default_train_score:.1f} -> {res.train_score:.1f}; held-out "
        f"{res.default_validation.score:.1f} -> {res.validation.score:.1f}\nparams written to {out}"
    )
    if res.validation.score > res.default_validation.score:
        print(
            "warning: the tuned params are worse on the held-out corridors (overfitting): "
            "use more --train-tracks or --trials"
        )
    return 0


def _cmd_worker(args: argparse.Namespace) -> int:
    """Spec 0020: register this computer as a team worker and run the team's jobs."""
    import socket

    from raceforge.api.worker_runner import (
        WorkerConfig,
        config_path,
        load_config,
        run_worker,
        save_config,
    )
    from raceforge.api.workspace import default_root
    from raceforge.workspace.client import BackendClient, BackendError, OfflineError
    from raceforge.workspace.sync import Workspace

    cmd = args.worker_command
    try:
        if cmd == "register":
            ws = Workspace(default_root())
            status = ws.status()
            if ws.workspace_id is None or status.server_url is None:
                print("error: log in and pick a workspace on the Team tab first", file=sys.stderr)
                return 1
            name = args.name or socket.gethostname().removesuffix(".local")
            reg = ws.client().register_worker(ws.workspace_id, name)
            path = save_config(
                WorkerConfig(status.server_url, reg.token, reg.worker.id, name, ws.workspace_id)
            )
            print(f"registered worker {name!r}; token saved to {path} (owner-only)")
            print("start it with: raceforge worker run")
            return 0
        cfg = load_config()
        if cfg is None:
            print(
                f"error: not registered (no {config_path()}): raceforge worker register",
                file=sys.stderr,
            )
            return 1
        client = BackendClient(cfg.server, access=cfg.token)
        if cmd == "status":
            info = client.worker_heartbeat({})
            state = ("online" if info.online else "offline", "busy" if info.busy else "idle")
            print(f"{info.name}: {state[0]}, {state[1]}")
            return 0
        if cmd == "remove":
            ws = Workspace(default_root())
            ws.client().remove_worker(cfg.worker_id)
            config_path().unlink(missing_ok=True)
            print(f"worker {cfg.name!r} removed; its token is revoked")
            return 0
        print(f"worker {cfg.name!r} waiting for jobs on {cfg.server} (Ctrl+C to stop)", flush=True)
        try:
            n = run_worker(
                client, idle_only=args.idle_only, once=args.once, out=lambda s: print(s, flush=True)
            )
        except KeyboardInterrupt:
            return 0
        print(f"{n} job(s) done")
        return 0
    except (BackendError, OfflineError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def _cmd_capture(args: argparse.Namespace) -> int:
    from raceforge.capture.tscan import TscanError, TscanPass

    path = Path(args.file)
    try:
        with TscanPass(path) as p:
            summary = p.summary()
    except (TscanError, OSError) as exc:
        print(f"error: {exc}")
        return 1
    if args.capture_command == "info":
        width = max(len(k) for k in summary)
        for key, value in summary.items():
            print(f"{key:<{width}}  {value}")
        return 0
    from raceforge.api.workspace import default_root
    from raceforge.workspace.client import BackendError, OfflineError
    from raceforge.workspace.sync import Workspace

    ws = Workspace(default_root())
    if ws.workspace_id is None:
        print("error: log in and pick a workspace on the Team tab first")
        return 1
    from raceforge.capture.inbox import capture_slug

    slug = args.slug or capture_slug(summary["project"])
    try:
        v = ws.save_files("capture", slug, [path], f"imported {path.name}", merge=True)
    except (BackendError, OfflineError) as exc:
        print(f"error: {exc}")
        return 1
    state = (
        f"version {v.semver}" if v.semver else "saved locally, syncs when the backend is reachable"
    )
    print(f"{path.name} → {slug}: {state}")
    return 0


def _print_install(r: "InstallResult") -> None:
    if r.ok:
        print(f"installed {r.name} ({(r.digest or '')[:12]}): {r.detail}")
    else:
        what = f"{r.name} " if r.name else ""
        rolled = " (previous bundle restored)" if r.rolled_back else ""
        print(f"NOT installed {what}{rolled}: {r.detail}")


def _cmd_bundle(args: argparse.Namespace) -> int:
    from raceforge.api.deploy import (
        TOKEN_ENV,
        BundleError,
        build_car_bundle,
        bundle_digest,
        load_car_config,
        token_in_file,
    )

    car_path, out = Path(args.car), Path(args.out)
    try:
        car = load_car_config(car_path)
        m = build_car_bundle(
            out,
            Path(args.controller),
            car,
            params=Path(args.params) if args.params else None,
            name=args.name,
            race=args.race,
        )
    except BundleError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if car.telemetry is not None and token_in_file(car_path):
        print(
            f"warning: {car_path} contains the telemetry token; keep it out of git "
            f"(set {TOKEN_ENV} instead)",
            file=sys.stderr,
        )
    limit = m.runtime.test_speed_limit_m_s
    mode = "race" if m.runtime.mode == "race" else f"test (speed limit {limit or 'car max'})"
    print(
        f"bundle {m.name} ({bundle_digest(out)[:12]}) -> {out}: {m.controller.file}"
        f"{' + ' + m.params.file if m.params else ''}, {m.robot.car_name}, {mode}\n"
        f"next: raceforge deploy {out} --ssh <car> | --usb <stick>"
    )
    return 0


def _cmd_deploy(args: argparse.Namespace) -> int:
    from raceforge.api.deploy import DeployError, deploy_ssh, deploy_usb, usb_result

    try:
        if args.usb_result:
            r = usb_result(Path(args.usb_result))
            if r is None:
                print("no result on the stick yet: plug it into the car's board first")
                return 1
            _print_install(r)
            return 0 if r.ok else 1
        if not args.bundle:
            print("error: give the bundle directory to deploy", file=sys.stderr)
            return 2
        bundle = Path(args.bundle)
        if args.ssh:
            r = deploy_ssh(bundle, args.ssh)
            _print_install(r)
            return 0 if r.ok else 1
        path = deploy_usb(bundle, Path(args.usb))
        print(
            f"bundle written to {path}. Eject the stick and plug it into the car's board; "
            f"afterwards check with: raceforge deploy --usb-result {args.usb}"
        )
        return 0
    except DeployError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="raceforge", description="RaceForge command-line interface"
    )
    parser.add_argument("--version", action="version", version=f"raceforge {__version__}")
    sub = parser.add_subparsers(dest="command")

    parts = sub.add_parser("parts", help="LDraw library and parts catalogue")
    psub = parts.add_subparsers(dest="parts_command", required=True)
    fetch = psub.add_parser("fetch", help="download the LDraw library into the local cache")
    fetch.add_argument("--dir", help="target directory (default ~/.cache/raceforge/ldraw)")
    fetch.set_defaults(func=_cmd_parts_fetch)
    build = psub.add_parser(
        "build-catalogue", help="regenerate catalogue bounding boxes from LDraw"
    )
    build.add_argument("--dir", help="LDraw library directory")
    build.set_defaults(func=_cmd_parts_build)

    qs = sub.add_parser(
        "quickstart", help="generate a quick-start car (assembly, MJCF, MPD, derived data)"
    )
    qs.add_argument("--params", help="YAML file with QuickStartParams (defaults if omitted)")
    qs.add_argument("--out", default="quickstart-car", help="output directory")
    qs.set_defaults(func=_cmd_quickstart)

    simp = sub.add_parser("sim", help="drive a controller in the simulator")
    simp.add_argument(
        "--controller", required=True, help="Python file with one Controller subclass"
    )
    simp.add_argument("--params", help="YAML parameters (default: <controller>.yaml if present)")
    simp.add_argument(
        "--track", default="seed:0", help="seed:<n> for a generated corridor or a track JSON"
    )
    simp.add_argument(
        "--open", action="store_true", help="point-to-point corridor instead of a loop"
    )
    simp.add_argument(
        "--length", type=float, default=40.0, help="corridor length for generated tracks (m)"
    )
    simp.add_argument("--laps", type=int, default=3)
    simp.add_argument("--opponents", type=int, default=0)
    simp.add_argument("--seed", type=int, default=0, help="simulation seed (sensor noise, doors)")
    simp.add_argument(
        "--quickstart", help="YAML QuickStartParams for the car (default car otherwise)"
    )
    simp.add_argument("--record", help="directory for runlog.json + run.mcap")
    simp.add_argument("--max-time", type=float, default=900.0, help="simulated time limit (s)")
    simp.add_argument(
        "--watch", action="store_true", help="re-run whenever the controller file changes"
    )
    simp.set_defaults(func=_cmd_sim)

    bun = sub.add_parser("bundle", help="build a deploy bundle: controller + car config")
    bun.add_argument("controller", help="Python file with one Controller subclass")
    bun.add_argument(
        "--car", required=True, help="car config YAML (see controllers/car.example.yaml)"
    )
    bun.add_argument("--out", default="bundle", help="bundle directory (replaced if it is one)")
    bun.add_argument("--params", help="YAML parameters (default: <controller>.yaml if present)")
    bun.add_argument("--name", help="bundle name (default: the controller file name)")
    bun.add_argument(
        "--race", action="store_true", help="race mode: radios must be off, no teleop, no limit"
    )
    bun.set_defaults(func=_cmd_bundle)

    dep = sub.add_parser("deploy", help="install a bundle on the car (over SSH or a USB stick)")
    dep.add_argument("bundle", nargs="?", help="bundle directory (raceforge.car.bundle)")
    how = dep.add_mutually_exclusive_group(required=True)
    how.add_argument(
        "--ssh", metavar="[USER@]HOST", help="install now (default user raceforge-deploy)"
    )
    how.add_argument("--usb", metavar="STICK", help="write it to a mounted USB stick")
    how.add_argument(
        "--usb-result", metavar="STICK", help="show what the car reported on the stick"
    )
    dep.set_defaults(func=_cmd_deploy)

    ui = sub.add_parser("ui", help="start the local engine and the user interface")
    ui.add_argument("--port", type=int, default=8765, help="port (0 = pick a free one)")
    ui.add_argument("--browser", action="store_true", help="open the UI in the default browser")
    ui.set_defaults(func=_cmd_ui)

    wk = sub.add_parser("worker", help="run the team's training jobs on this computer (spec 0020)")
    wsub = wk.add_subparsers(dest="worker_command", required=True)
    wreg = wsub.add_parser("register", help="register this computer in the current workspace")
    wreg.add_argument("--name", help="worker name (default: host name)")
    wrun = wsub.add_parser("run", help="wait for jobs and run them")
    wrun.add_argument(
        "--idle-only", action="store_true", help="only start jobs when the CPU is idle"
    )
    wrun.add_argument("--once", action="store_true", help="run at most one job, then exit")
    wsub.add_parser("status", help="show whether the backend sees this worker")
    wsub.add_parser("remove", help="unregister this computer and revoke its token")
    for p in wsub.choices.values():
        p.set_defaults(func=_cmd_worker)

    tr = sub.add_parser("train", help="benchmark and tune controllers in the sim (spec 0013)")
    tsub = tr.add_subparsers(dest="train_command", required=True)
    tbench = tsub.add_parser("benchmark", help="score a controller on held-out corridors")
    tbench.add_argument("controller")
    tbench.add_argument("--params", help="params YAML (default: next to the controller)")
    ttune = tsub.add_parser("tune", help="Optuna search over the controller's Tunable params")
    ttune.add_argument("controller")
    ttune.add_argument("--trials", type=int, default=30)
    ttune.add_argument("--timeout", type=float, help="stop after this many seconds")
    ttune.add_argument("--train-tracks", type=int, default=3, help="training corridors per trial")
    ttune.add_argument("--out", help="params YAML (default: <controller>.tuned.yaml)")
    trl = tsub.add_parser("rl", help="train a driving policy with PPO (needs the 'rl' extra)")
    trl.add_argument("--steps", type=int, default=200_000, help="environment steps")
    trl.add_argument("--train-tracks", type=int, default=8, help="training corridors")
    trl.add_argument("--out", default="policy.yaml", help="params YAML for onnx_policy.py")
    for p in (tbench, ttune, trl):
        p.add_argument("--tracks", type=int, default=5, help="held-out benchmark corridors")
        p.add_argument("--length", type=float, default=25.0, help="corridor length in m (20-120)")
        p.add_argument("--laps", type=int, default=1)
        p.add_argument("--opponents", type=int, default=0)
        p.add_argument("--max-time", type=float, default=240.0, help="seconds per race")
        p.add_argument("--workers", type=int, default=0, help="processes (0: CPUs - 1)")
        p.set_defaults(func=_cmd_train)
    trl.set_defaults(func=_cmd_train_rl)

    cap = sub.add_parser("capture", help="TrackScout scans (.tscan, spec 0007)")
    csub = cap.add_subparsers(dest="capture_command", required=True)
    cinfo = csub.add_parser("info", help="check a .tscan pass and print a summary")
    cinfo.add_argument("file")
    cimp = csub.add_parser("import", help="add a .tscan pass to the current team workspace")
    cimp.add_argument("file")
    cimp.add_argument("--slug", help="capture object (default: scan-<project name>)")
    for p in (cinfo, cimp):
        p.set_defaults(func=_cmd_capture)

    be = sub.add_parser("backend", help="team backend server (spec 0006)")
    bsub = be.add_subparsers(dest="backend_command", required=True)
    serve = bsub.add_parser("serve", help="run the backend API (config from RF_* env vars)")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    bsub.add_parser("migrate", help="apply database migrations")
    boot = bsub.add_parser("bootstrap-admin", help="create the first admin account")
    boot.add_argument("--username", required=True)
    bsub.add_parser("purge-trash", help="delete objects older than 30 days in the trash")
    dev = bsub.add_parser("dev", help="local backend with SQLite and a blob folder (testing)")
    dev.add_argument("--port", type=int, default=8080)
    dev.add_argument(
        "--host", default="127.0.0.1", help="bind address (default: this machine only)"
    )
    dev.add_argument(
        "--lan",
        action="store_true",
        help="reachable from phones/laptops in the local network (binds 0.0.0.0, uses the LAN IP)",
    )
    dev.add_argument("--data", default=".raceforge-backend", help="data directory")
    dev.add_argument("--admin", help="create admin USER:PASSWORD if missing")
    dev.add_argument("--admin-totp", help="enable TOTP for that admin with this base32 secret")
    exp = bsub.add_parser("export-blobs", help="copy new blobs into a backup folder")
    exp.add_argument("dir")
    imp = bsub.add_parser("import-blobs", help="restore blobs from a backup folder")
    imp.add_argument("dir")
    for p in (serve, boot, dev, exp, imp, bsub.choices["migrate"], bsub.choices["purge-trash"]):
        p.set_defaults(func=_cmd_backend)

    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    return int(args.func(args))
