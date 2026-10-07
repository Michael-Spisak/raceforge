"""Small declarative state machine for controllers (spec 0004)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum


@dataclass
class _Transition[S: Enum]:
    source: S | None  # None = from any state
    target: S
    guard: Callable[[], bool]


@dataclass
class StateMachine[S: Enum]:
    """States are Enum members. Add transitions with guards; call :meth:`update` once per step.

    Example::

        class St(Enum):
            DRIVE = auto()
            STOP = auto()

        sm = StateMachine(St.DRIVE)
        sm.transition(St.DRIVE, St.STOP, lambda: obs.ultrasonic_m["front"] < 0.2)
    """

    state: S
    _transitions: list[_Transition[S]] = field(default_factory=list[_Transition[S]])
    _on_enter: dict[S, Callable[[], None]] = field(default_factory=dict[S, Callable[[], None]])
    _on_exit: dict[S, Callable[[], None]] = field(default_factory=dict[S, Callable[[], None]])
    time_in_state_s: float = 0.0

    def transition(self, source: S | None, target: S, guard: Callable[[], bool]) -> None:
        """Register a transition; transitions are checked in the order they were added."""
        self._transitions.append(_Transition(source, target, guard))

    def on_enter(self, state: S, fn: Callable[[], None]) -> None:
        self._on_enter[state] = fn

    def on_exit(self, state: S, fn: Callable[[], None]) -> None:
        self._on_exit[state] = fn

    def update(self, dt_s: float) -> S:
        """Fire the first matching transition (at most one per step); returns the current state."""
        self.time_in_state_s += dt_s
        for t in self._transitions:
            if (
                (t.source is None or t.source == self.state)
                and t.target != self.state
                and t.guard()
            ):
                self.set(t.target)
                break
        return self.state

    def set(self, target: S) -> None:
        if target == self.state:
            return
        if self.state in self._on_exit:
            self._on_exit[self.state]()
        self.state = target
        self.time_in_state_s = 0.0
        if target in self._on_enter:
            self._on_enter[target]()
