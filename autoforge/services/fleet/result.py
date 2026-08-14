"""Typed outputs of a fleet simulation run.

Frozen and validated so downstream consumers (dashboards, finance, the AI /
data phases) can rely on the schema. ``VehicleOperation`` and ``TelemetryPoint``
records live in the domain layer; the analytics aggregations live here.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from autoforge.domain.fleet import MaintenanceEvent, TelemetryPoint, VehicleOperation


class FleetAnalytics(BaseModel):
    """Aggregated numbers for one fleet run."""

    model_config = ConfigDict(frozen=True)

    source_run_id: str
    days: float = Field(gt=0, description="Simulated horizon in days")
    fleet_size: int = Field(gt=0)
    operating_days: int = Field(ge=0, description="Vehicle-days actually driven")
    availability: float = Field(ge=0, le=1, description="operating_days / (fleet_size * days)")
    total_distance_km: float = Field(ge=0)
    total_energy_kwh: float = Field(ge=0, description="Net battery energy over all operations")
    average_consumption_kwh_per_km: float | None = Field(
        default=None, description="Total net energy / total distance; None when no distance"
    )
    total_telemetry_points: int = Field(ge=0)
    fault_counts: dict[str, int] = Field(
        default_factory=dict, description="Battery fault code -> operating days it appeared"
    )
    maintenance_events: int = Field(ge=0)
    low_soh_vehicles: int = Field(ge=0, description="Vehicles ending below the SOH threshold")
    avg_final_soh: float | None = Field(
        default=None, description="Mean carried SOH over all fleet vehicles"
    )


class FleetSimulationResult(BaseModel):
    """Full result of a fleet run."""

    model_config = ConfigDict(frozen=True)

    analytics: FleetAnalytics
    operations: tuple[VehicleOperation, ...]
    telemetry: tuple[TelemetryPoint, ...]
    maintenance: tuple[MaintenanceEvent, ...]
