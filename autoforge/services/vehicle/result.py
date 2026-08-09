"""Typed outputs of a vehicle powertrain simulation.

Deliberately not plain dictionaries: the summary and trajectory are validated
models so downstream consumers (dashboards, fleet analytics) can rely on the
schema and the units of every field.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TrajectoryPoint(BaseModel):
    """One sampled state along the simulated drive.

    ``battery_power_kw`` is signed from the battery's point of view: positive
    means discharging (draw), negative means net charging (regen exceeding
    aux). Energy fields are cumulative over the whole drive up to this point.
    """

    model_config = ConfigDict(frozen=True)

    time_s: float = Field(ge=0)
    speed_mps: float = Field(ge=0)
    acceleration_mps2: float
    grade_fraction: float
    wheel_power_kw: float
    traction_power_kw: float = Field(ge=0, description="Motor output to the wheels")
    battery_power_kw: float
    recovered_power_kw: float = Field(ge=0, description="Regen power accepted by the battery")
    aux_power_kw: float = Field(ge=0)
    soc: float = Field(ge=0, le=1, description="State of charge of usable energy, 0..1")
    energy_consumed_kwh: float = Field(ge=0, description="Cumulative energy drawn from battery")
    energy_recovered_kwh: float = Field(ge=0, description="Cumulative regen energy stored")
    distance_km: float = Field(ge=0)
    power_limited: bool = False
    depleted: bool = False


class ResultSummary(BaseModel):
    """Aggregated numbers for one simulated drive."""

    model_config = ConfigDict(frozen=True)

    duration_s: float = Field(ge=0)
    distance_km: float = Field(ge=0)
    energy_consumed_kwh: float = Field(ge=0, description="Gross battery outflow (traction + aux)")
    energy_recovered_kwh: float = Field(ge=0, description="Regen energy actually stored")
    regen_discarded_kwh: float = Field(
        ge=0, description="Regen energy not stored (limits or full battery)"
    )
    net_energy_kwh: float = Field(description="Consumed minus recovered (can be negative)")
    average_consumption_kwh_per_km: float | None = Field(
        default=None, description="Net energy per km; None when distance is ~0"
    )
    peak_power_kw: float = Field(ge=0, description="Peak battery draw")
    peak_regen_power_kw: float = Field(ge=0, description="Peak regen power stored")
    min_soc: float = Field(ge=0, le=1)
    final_soc: float = Field(ge=0, le=1)
    estimated_range_km: float | None = Field(
        default=None,
        description="Usable energy / net consumption; simplified, not certified",
    )
    power_limited_seconds: float = Field(ge=0, description="Time demand exceeded capability")


class SimulationResult(BaseModel):
    """Full result of one powertrain run: summary plus trajectory."""

    model_config = ConfigDict(frozen=True)

    summary: ResultSummary
    trajectory: tuple[TrajectoryPoint, ...]
