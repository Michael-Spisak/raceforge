"""Deploy a bundle to the car (spec 0005 "Deploy"): the API the CLI and, later, the UI use."""

from raceforge.car.deploy import (
    DeployError,
    InstallResult,
    deploy_ssh,
    deploy_usb,
    pack_bundle,
    usb_result,
)

__all__ = ["DeployError", "InstallResult", "deploy_ssh", "deploy_usb", "pack_bundle", "usb_result"]
