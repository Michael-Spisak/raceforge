"""The team's physical cars (spec 0033): one car config (car.yaml) per car, in the engine data.

Calibration results are written into the existing fields of a car's config; the file keeps its
comments (only the changed values are replaced) and the previous version is kept as ``.bak``.
"""

import re
import shutil
from pathlib import Path

import yaml
from pydantic import ValidationError

from raceforge.api.models import FleetCar
from raceforge.car.bundle import BundleError, CarConfig, load_car_config

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


def cars_dir() -> Path:
    from raceforge.api.workspace import default_root

    return default_root().parent / "cars"


def example() -> Path:
    from raceforge.api.service import TEMPLATES_DIR

    return TEMPLATES_DIR.parent / "car.example.yaml"


def path_of(name: str, root: Path | None = None) -> Path:
    if not NAME.match(name):
        raise ValueError("car names: lower-case letters, digits and '-', e.g. 'car-1'")
    return (root or cars_dir()) / f"{name}.yaml"


def list_cars(root: Path | None = None) -> list[FleetCar]:
    out: list[FleetCar] = []
    for p in sorted((root or cars_dir()).glob("*.yaml")):
        try:
            out.append(
                FleetCar(name=p.stem, path=str(p), car_name=load_car_config(p).robot.car_name)
            )
        except (BundleError, ValueError, OSError) as e:
            out.append(FleetCar(name=p.stem, path=str(p), car_name="", error=str(e)))
    return out


def load(name: str, root: Path | None = None) -> CarConfig:
    """The car's config; ValueError (readable) if it is missing or invalid."""
    try:
        return load_car_config(path_of(name, root))
    except BundleError as e:
        raise ValueError(str(e)) from e


def create_car(name: str, root: Path | None = None) -> FleetCar:
    """A new car from the example config, with ``robot.car_name`` = ``name``."""
    p = path_of(name, root)
    if p.exists():
        raise ValueError(f"car {name!r} exists already")
    p.parent.mkdir(parents=True, exist_ok=True)
    text = example().read_text(encoding="utf-8")
    text = re.sub(r"(?m)^(\s+car_name:\s*)\S+", lambda m: m.group(1) + name, text, count=1)
    p.write_text(text, encoding="utf-8")
    return FleetCar(name=name, path=str(p), car_name=name)


def _patch(text: str, section: str, key: str, value: float) -> str:
    """Replace ``key:`` inside top-level ``section:`` keeping indentation and comments; insert it
    right after the section header when it is missing."""
    lines = text.splitlines(keepends=True)
    start = next(
        (i for i, ln in enumerate(lines) if re.match(rf"^{re.escape(section)}:", ln)), None
    )
    if start is None:
        raise ValueError(f"section {section!r} not found in the car config")
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].strip() and not lines[i][0].isspace()),
        len(lines),
    )
    pattern = re.compile(rf"^(\s+{re.escape(key)}:\s*)([^#\n]*?)(\s*#.*)?$")
    for i in range(start + 1, end):
        m = pattern.match(lines[i].rstrip("\n"))
        if m:
            lines[i] = f"{m.group(1)}{value}{m.group(3) or ''}\n"
            return "".join(lines)
    lines.insert(start + 1, f"  {key}: {value}  # calibration (spec 0033)\n")
    return "".join(lines)


def apply_changes(name: str, changes: dict[str, float], root: Path | None = None) -> FleetCar:
    p = path_of(name, root)
    if not p.is_file():
        raise ValueError(f"no car {name!r}")
    text = p.read_text(encoding="utf-8")
    for dotted, value in changes.items():
        section, _, key = dotted.partition(".")
        if not key or "." in key:
            raise ValueError(f"unsupported field {dotted!r}")
        text = _patch(text, section, key, value)
    try:  # validate before writing: a calibration must never break the car config
        CarConfig.model_validate(yaml.safe_load(text))
    except ValidationError as e:
        raise ValueError(f"the calibrated config would be invalid: {e}") from e
    shutil.copy2(p, p.with_suffix(".yaml.bak"))
    p.write_text(text, encoding="utf-8")
    return FleetCar(name=name, path=str(p), car_name=load(name, root).robot.car_name)
