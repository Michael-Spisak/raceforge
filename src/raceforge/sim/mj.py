"""Typed access point for the MuJoCo bindings (they ship without type stubs)."""

import importlib
from typing import Any

mujoco: Any = importlib.import_module("mujoco")
