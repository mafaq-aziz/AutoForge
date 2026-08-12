"""Typed outputs of a factory simulation run.

Frozen and validated so downstream consumers (fleet, finance, dashboards) can
rely on the schema. ``FinishedVehicle`` records produced here feed the fleet
phase directly.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from autoforge.domain.factory import FinishedVehicle, ProductionStage, QualityInspection


class StationMetrics(BaseModel):
    """Per-station results for one run."""

    model_config = ConfigDict(frozen=True)

    stage: ProductionStage
    processed: int = Field(ge=0, description="Items completed at this station")
    downtime_days: float = Field(ge=0)
    material_wait_days: float = Field(ge=0, description="Item-days starved for material")
    utilization: float = Field(ge=0, le=1, description="Processed / (capacity * days)")
    bottleneck: bool = Field(description="True for stations at the maximum observed utilization")


class FactorySummary(BaseModel):
    """Aggregated numbers for one factory run."""

    model_config = ConfigDict(frozen=True)

    source_run_id: str
    days_simulated: float = Field(gt=0)
    orders_released: int = Field(ge=0)
    items_released: int = Field(ge=0)
    vehicles_finished: int = Field(ge=0)
    scrapped: int = Field(ge=0)
    reworks: int = Field(ge=0)
    finished_cost_eur: float = Field(ge=0, description="Sum of finished-vehicle production costs")
    rework_cost_eur: float = Field(ge=0)
    scrap_cost_eur: float = Field(ge=0)
    total_cost_eur: float = Field(ge=0, description="finished + scrap cost")
    bottleneck_stages: tuple[str, ...] = Field(
        description="Stations with the highest observed utilization"
    )
    bottleneck_capacity_per_day: float = Field(
        gt=0, description="Theoretical steady-state throughput (slowest station)"
    )
    throughput_per_day: float = Field(
        ge=0, description="vehicles_finished / days_simulated (includes ramp-up)"
    )
    wip_remaining: int = Field(
        ge=0, description="Items still queued or in process when the run ended"
    )
    waiting_for_material: int = Field(ge=0)
    orders_completed: int = Field(ge=0)
    orders_late: int = Field(ge=0)
    inspection_pass: int = Field(ge=0)
    inspection_rework: int = Field(ge=0)
    inspection_fail: int = Field(ge=0)


class FactorySimulationResult(BaseModel):
    """Full result of a factory run."""

    model_config = ConfigDict(frozen=True)

    summary: FactorySummary
    finished_vehicles: tuple[FinishedVehicle, ...]
    inspections: tuple[QualityInspection, ...]
    station_metrics: tuple[StationMetrics, ...]
