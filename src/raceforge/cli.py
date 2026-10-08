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
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")
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

    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    return int(args.func(args))
