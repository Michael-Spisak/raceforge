"""PID controller and simple filters (spec 0004)."""

import math
from collections import deque


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class PID:
    """PID with output limits, anti-windup and derivative on measurement."""

    def __init__(
        self,
        kp: float,
        ki: float = 0.0,
        kd: float = 0.0,
        out_min: float = -math.inf,
        out_max: float = math.inf,
    ) -> None:
        self.kp, self.ki, self.kd = kp, ki, kd
        self.out_min, self.out_max = out_min, out_max
        self.integral = 0.0
        self._last_meas: float | None = None

    def reset(self) -> None:
        self.integral = 0.0
        self._last_meas = None

    def update(self, setpoint: float, measurement: float, dt_s: float) -> float:
        error = setpoint - measurement
        deriv = 0.0
        if self._last_meas is not None and dt_s > 0:
            deriv = -(measurement - self._last_meas) / dt_s
        self._last_meas = measurement
        unsat = self.kp * error + self.ki * (self.integral + error * dt_s) + self.kd * deriv
        out = clamp(unsat, self.out_min, self.out_max)
        if (
            out == unsat
            or (unsat > self.out_max and error < 0)
            or (unsat < self.out_min and error > 0)
        ):
            self.integral += error * dt_s
        return out


class Ema:
    """Exponential moving average; ``alpha`` = weight of the newest sample."""

    def __init__(self, alpha: float) -> None:
        if not 0 < alpha <= 1:
            raise ValueError("alpha must be in (0, 1]")
        self.alpha = alpha
        self.value: float | None = None

    def update(self, x: float) -> float:
        self.value = x if self.value is None else self.value + self.alpha * (x - self.value)
        return self.value


class Median:
    """Sliding-window median (good against ultrasonic spikes)."""

    def __init__(self, window: int) -> None:
        if window < 1:
            raise ValueError("window must be >= 1")
        self.buf: deque[float] = deque(maxlen=window)

    def update(self, x: float) -> float:
        self.buf.append(x)
        s = sorted(self.buf)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


class RateLimiter:
    """Limits how fast a value may change per second."""

    def __init__(self, max_rate: float, value: float = 0.0) -> None:
        self.max_rate, self.value = max_rate, value

    def update(self, target: float, dt_s: float) -> float:
        step = self.max_rate * dt_s
        self.value += clamp(target - self.value, -step, step)
        return self.value
