"""Simulation time handling.

The company-scale simulation works in days. Physics subsystems (powertrain,
ADAS, robotics) convert to SI seconds internally when they model fast dynamics.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SimulationClock:
    """Advances simulated time in fixed steps. Base unit is days.

    A single clock drives every subsystem so all modules agree on "now".
    Partial final steps are supported by the engine calling step with a smaller
    dt, so runs can stop exactly on a requested horizon.
    """

    step_days: float
    now_days: float = 0.0

    def __post_init__(self) -> None:
        if self.step_days <= 0:
            raise ValueError(f"step_days must be positive, got {self.step_days!r}")
        if self.now_days < 0:
            raise ValueError(f"now_days must be non-negative, got {self.now_days!r}")

    def advance(self, dt_days: float) -> float:
        """Advance by ``dt_days`` and return the new simulation time."""
        if dt_days < 0:
            raise ValueError(f"cannot advance time backwards (dt={dt_days!r})")
        self.now_days += dt_days
        return self.now_days
