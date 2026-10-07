"""Command-line entry point (`raceforge`)."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

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

    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    return int(args.func(args))
