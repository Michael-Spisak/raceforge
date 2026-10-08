"""Command-line entry point (`raceforge`)."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from raceforge.backend.settings import Settings

from raceforge import __version__


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
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


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
                "public_url": f"http://127.0.0.1:{args.port}",
            }
        )
    return settings


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
            print(f"error: {exc.detail}")
            if cmd == "bootstrap-admin":
                return 1
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
    import uvicorn

    host = "127.0.0.1" if cmd == "dev" else args.host
    print(f"RACEFORGE_BACKEND_URL=http://{host}:{args.port}/", flush=True)
    uvicorn.run(
        create_backend_app(backend),
        host=host,
        port=args.port,
        log_level="info" if cmd == "serve" else "warning",
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
    return 0


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

    ui = sub.add_parser("ui", help="start the local engine and the user interface")
    ui.add_argument("--port", type=int, default=8765, help="port (0 = pick a free one)")
    ui.add_argument("--browser", action="store_true", help="open the UI in the default browser")
    ui.set_defaults(func=_cmd_ui)

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
    dev.add_argument("--data", default=".raceforge-backend", help="data directory")
    dev.add_argument("--admin", help="create admin USER:PASSWORD if missing")
    dev.add_argument("--admin-totp", help="enable TOTP for that admin with this base32 secret")
    for p in (serve, boot, dev, bsub.choices["migrate"], bsub.choices["purge-trash"]):
        p.set_defaults(func=_cmd_backend)

    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    return int(args.func(args))
