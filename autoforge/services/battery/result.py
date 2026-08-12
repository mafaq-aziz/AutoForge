"""Typed outputs of a battery simulation run.

The battery layer consumes a powertrain result and adds the electrical,
thermal, degradation, and fault view. Deliberately flat and validated so fleet
analytics (later phases) can consume it without parsing dictionaries.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from autoforge.domain.battery_state import BatteryFaultCode


class BatteryStatePoint(BaseModel):
    """One sampled battery state along the trace.

    ``soc_error`` is the model's own energy-integrated SOC estimate minus the
    powertrain's authoritative SOC; it should be ~0 and exists as a consistency
    check, not as a second source of truth.
    """

    model_config = ConfigDict(frozen=True)

    time_s: float = Field(ge=0)
    soc: float = Field(ge=0, le=1)
    soh: float = Field(gt=0, le=1)
    voltage_v: float = Field(gt=0)
    current_a: float
    power_kw: float
    temperature_k: float = Field(gt=0)
    throughput_kwh: float = Field(ge=0)
    equivalent_full_cycles: float = Field(ge=0)
    soc_error: float = Field(default=0.0)
    faults: tuple[BatteryFaultCode, ...] = ()
    power_limited: bool = False


class BatterySummary(BaseModel):
    """Aggregated numbers for one battery run."""

    model_config = ConfigDict(frozen=True)

    source_run_id: str = Field(description="Run id of the powertrain run that fed this trace")
    initial_temperature_k: float = Field(gt=0)
    final_soc: float = Field(ge=0, le=1)
    final_soh: float = Field(gt=0, le=1)
    final_temperature_k: float = Field(gt=0)
    max_temperature_k: float = Field(gt=0)
    min_temperature_k: float = Field(gt=0)
    max_absolute_current_a: float = Field(ge=0)
    throughput_kwh: float = Field(ge=0, description="Cumulative |energy| through the pack")
    equivalent_full_cycles: float = Field(ge=0)
    max_soc_error: float = Field(
        ge=0, description="Max |model SOC - powertrain SOC| over the trace"
    )
    fault_counts: dict[str, int] = Field(
        default_factory=dict, description="Fault code -> number of intervals it was active"
    )


class BatterySimulationResult(BaseModel):
    """Full result of a battery simulation: summary plus per-interval states."""

    model_config = ConfigDict(frozen=True)

    summary: BatterySummary
    trajectory: tuple[BatteryStatePoint, ...]
