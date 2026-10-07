"""Controller base class, host (deadline/exception handling) and file loader (spec 0004)."""

import importlib.util
import inspect
import sys
import time
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import cast

from raceforge.control.params import ControllerParams
from raceforge.control.types import Command, Observation, RobotInfo, RobotIO


class Controller[P: ControllerParams](ABC):
    """Base class for all driving controllers.

    Subclass it, set ``Params`` to your :class:`ControllerParams` subclass (optional) and implement
    :meth:`step`. The same file runs in the simulator and on the real car.
    """

    Params: type[ControllerParams] = ControllerParams

    def __init__(self, params: P | None = None) -> None:
        self.params: P = params if params is not None else self.Params()  # type: ignore[assignment]
        self.io: RobotIO | None = None
        self.state: str = "run"

    def setup(self, info: RobotInfo) -> None:  # noqa: B027 - optional hook
        """Called once before the first step."""

    @abstractmethod
    def step(self, obs: Observation) -> Command:
        """Return the command for this control step. Must finish within the step deadline."""

    def teardown(self) -> None:  # noqa: B027 - optional hook
        """Called once after the last step."""

    def emit(self, channel: str, value: float | int | bool | str) -> None:
        """Publish a named value to telemetry (shows up in the live plots and logs)."""
        if self.io is not None:
            self.io.emit(channel, value)


@dataclass(frozen=True)
class StepProblem:
    step: int
    kind: str  # "exception" | "deadline"
    detail: str


@dataclass
class ControllerHost:
    """Runs a controller step-by-step with deadline measurement and exception policy.

    ``on_exception``: "stop" (default, returns a zero command) or "raise".
    """

    controller: Controller[ControllerParams]
    io: RobotIO
    deadline_s: float
    on_exception: str = "stop"
    steps: int = 0
    problems: list[StepProblem] = field(default_factory=list[StepProblem])
    max_step_s: float = 0.0

    def start(self) -> None:
        self.controller.io = self.io
        self.controller.setup(self.io.info)

    def step(self) -> Command:
        obs = self.io.read()
        t0 = time.perf_counter()
        try:
            cmd = self.controller.step(obs)
        except Exception as exc:
            if self.on_exception == "raise":
                raise
            self.problems.append(
                StepProblem(
                    self.steps, "exception", "".join(traceback.format_exception_only(exc)).strip()
                )
            )
            cmd = Command()
        elapsed = time.perf_counter() - t0
        self.max_step_s = max(self.max_step_s, elapsed)
        if elapsed > self.deadline_s:
            self.problems.append(
                StepProblem(
                    self.steps,
                    "deadline",
                    f"{elapsed * 1000:.1f} ms > {self.deadline_s * 1000:.1f} ms",
                )
            )
        self.io.emit("state", self.controller.state)
        self.io.write(cmd)
        self.steps += 1
        return cmd

    def stop(self) -> None:
        self.controller.teardown()


def load_controller_class(path: Path) -> type[Controller[ControllerParams]]:
    """Import ``path`` and return the single concrete Controller subclass defined in it."""
    module_name = f"raceforge_user_controller_{abs(hash(path.resolve()))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    found: list[type[Controller[ControllerParams]]] = []
    for obj in list(vars(module).values()):
        if not inspect.isclass(obj) or obj is Controller or inspect.isabstract(obj):
            continue
        if issubclass(obj, Controller) and obj.__module__ == module_name:
            found.append(cast(type[Controller[ControllerParams]], obj))
    if len(found) != 1:
        names = [c.__name__ for c in found]
        raise ImportError(f"{path} must define exactly one Controller subclass, found {names}")
    return found[0]


def load_controller(path: Path, params_path: Path | None = None) -> Controller[ControllerParams]:
    cls = load_controller_class(path)
    params_file = params_path or path.with_suffix(".yaml")
    params = cls.Params.from_yaml(params_file) if params_file.is_file() else cls.Params()
    return cls(params)
