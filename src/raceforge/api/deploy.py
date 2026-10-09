"""Build and deploy bundles for the car (spec 0005 "Deploy"): the API the CLI and the app's Deploy
panel (spec 0012) use."""

import re
from pathlib import Path

from raceforge.api.models import BundleInfo, BundleRequest, DeployRequest, DeployResponse
from raceforge.car.bundle import (
    TOKEN_ENV,
    BundleError,
    CarConfig,
    build_car_bundle,
    bundle_digest,
    load_car_config,
    token_in_file,
)
from raceforge.car.deploy import (
    DeployError,
    InstallResult,
    deploy_ssh,
    deploy_usb,
    pack_bundle,
    usb_result,
)


def bundles_dir() -> Path:
    """Where the app builds bundles: next to the workspace cache (tests use a temp folder)."""
    from raceforge.api.workspace import default_root

    return default_root().parent / "bundles"


def _safe_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")
    return safe or "bundle"


def _resolve(path: str) -> Path:
    """User paths: absolute, relative to the engine's folder, or relative to the project (the app
    may start the engine elsewhere, e.g. ``controllers/car.example.yaml``)."""
    p = Path(path).expanduser()
    if p.is_absolute() or p.exists():
        return p
    from raceforge.api.service import TEMPLATES_DIR

    project = TEMPLATES_DIR.parent.parent
    return project / p if (project / p).exists() else p


def build_from_request(req: BundleRequest, root: Path | None = None) -> BundleInfo:
    """Spec 0012: a test-mode bundle (race bundles stay on the CLI: owner gate)."""
    controller = _resolve(req.controller)
    car_path = _resolve(req.car_config)
    params = _resolve(req.params) if req.params else None
    name = _safe_name(req.name or controller.stem)
    out = (root or bundles_dir()) / name
    out.parent.mkdir(parents=True, exist_ok=True)
    car = load_car_config(car_path)
    m = build_car_bundle(out, controller, car, params=params, name=name)
    warnings: list[str] = []
    if car.telemetry is not None and token_in_file(car_path):
        warnings.append(
            f"{car_path} contains the telemetry token; keep it out of git ({TOKEN_ENV})"
        )
    return BundleInfo(
        path=str(out),
        name=m.name,
        digest=bundle_digest(out),
        controller=m.controller.file,
        params=m.params.file if m.params else None,
        car_name=m.robot.car_name,
        mode=m.runtime.mode,
        speed_limit_m_s=m.runtime.test_speed_limit_m_s,
        warnings=warnings,
    )


def deploy_from_request(req: DeployRequest) -> DeployResponse:
    bundle = Path(req.bundle).expanduser()
    if req.target == "ssh":
        if not req.host:
            raise ValueError("host: give the car's [user@]host for an SSH deploy")
        return DeployResponse(result=deploy_ssh(bundle, req.host))
    if not req.stick:
        raise ValueError("stick: give the mounted USB stick folder")
    return DeployResponse(usb_path=str(deploy_usb(bundle, Path(req.stick).expanduser())))


__all__ = [
    "TOKEN_ENV",
    "BundleError",
    "CarConfig",
    "DeployError",
    "InstallResult",
    "build_car_bundle",
    "build_from_request",
    "bundle_digest",
    "bundles_dir",
    "deploy_from_request",
    "deploy_ssh",
    "deploy_usb",
    "load_car_config",
    "pack_bundle",
    "token_in_file",
    "usb_result",
]
