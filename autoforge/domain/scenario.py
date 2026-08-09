"""Reusable driving scenario, independent of the powertrain.

A scenario is a prescribed time/speed/road-grade profile sampled at a uniform
timestep. It is pure data plus validation; the powertrain consumes it later.
Keeping it in the domain layer means other consumers (fleet, ADAS, telemetry)
can reuse the same representation.
"""

from __future__ import annotations

import math
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DrivingScenario(BaseModel):
    """Time, speed, and grade profiles for one drive.

    All values are SI (seconds, m/s). ``grade_fraction`` is a slope fraction:
    0.06 means a 6% grade (up is positive). The timestep must be uniform and
    strictly positive so the profile can be replayed step by step; non-uniform
    cycles must be resampled before use.
    """

    model_config = ConfigDict(frozen=True)

    #: Sanity bound on speed; values above it are almost certainly a unit error
    #: (e.g. km/h fed in as m/s). 100 m/s is 360 km/h.
    MAX_SPEED_MPS: ClassVar[float] = 100.0

    name: str = Field(default="", description="Optional scenario label for logs and records")
    time_s: tuple[float, ...]
    speed_mps: tuple[float, ...]
    grade_fraction: tuple[float, ...]

    @model_validator(mode="after")
    def _validate_profile(self) -> DrivingScenario:
        n = len(self.time_s)
        if n < 2:
            raise ValueError("scenario must contain at least two samples")
        if len(self.speed_mps) != n or len(self.grade_fraction) != n:
            raise ValueError("time, speed, and grade arrays must have equal length")
        if not math.isclose(self.time_s[0], 0.0, abs_tol=1e-9):
            raise ValueError("scenario time must start at 0.0 s")

        for i in range(n):
            for label, value in (
                ("time", self.time_s[i]),
                ("speed", self.speed_mps[i]),
                ("grade", self.grade_fraction[i]),
            ):
                if not math.isfinite(value):
                    raise ValueError(f"scenario {label} value must be finite at sample {i}")
            if self.speed_mps[i] < 0.0:
                raise ValueError(f"speed cannot be negative at sample {i}")
            if self.speed_mps[i] > self.MAX_SPEED_MPS:
                raise ValueError(
                    f"speed {self.speed_mps[i]:.1f} m/s exceeds {self.MAX_SPEED_MPS:.0f} m/s "
                    f"at sample {i}; likely a unit error"
                )
            if not -1.0 <= self.grade_fraction[i] <= 1.0:
                raise ValueError(f"grade fraction out of [-1, 1] at sample {i}")

        timestep = self.time_s[1] - self.time_s[0]
        if timestep <= 0.0:
            raise ValueError(f"timestep must be positive, got {timestep!r}")
        for i in range(2, n):
            step = self.time_s[i] - self.time_s[i - 1]
            if not math.isclose(step, timestep, rel_tol=1e-6, abs_tol=1e-9):
                raise ValueError(f"timestep not uniform at sample {i}: {step!r} != {timestep!r}")
        return self

    @property
    def sample_count(self) -> int:
        return len(self.time_s)

    @property
    def timestep_s(self) -> float:
        return self.time_s[1] - self.time_s[0]

    @property
    def duration_s(self) -> float:
        return self.time_s[-1] - self.time_s[0]

    @property
    def distance_km(self) -> float:
        """Trapezoidal integration of the speed profile (pure kinematics)."""
        total_m = 0.0
        for i in range(self.sample_count - 1):
            v_avg = 0.5 * (self.speed_mps[i] + self.speed_mps[i + 1])
            total_m += v_avg * (self.time_s[i + 1] - self.time_s[i])
        return total_m / 1000.0
