"""Factory domain models: the production line, orders, items, quality, and output.

Specifications (``ProductionStation``, ``ProductionOrder``, ``InventoryItem``)
are frozen and validated. ``WorkItem`` is runtime state with frozen transitions,
in the same style as ``BatteryState``; records (``QualityInspection``,
``FinishedVehicle``) are frozen outputs that later phases (fleet) consume.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoforge.domain.vehicle import VehicleVariant


class ProductionStage(StrEnum):
    """Stages of the assembly line, in process order."""

    RAW = "raw"
    BATTERY = "battery"
    BODY = "body"
    PAINT = "paint"
    POWERTRAIN = "powertrain"
    FINAL_ASSEMBLY = "final_assembly"
    QUALITY_INSPECTION = "quality_inspection"
    FINISHED = "finished"


class WorkStatus(StrEnum):
    """Lifecycle status of one work item."""

    QUEUED = "queued"
    IN_PROCESS = "in_process"
    WAITING_FOR_MATERIAL = "waiting_for_material"
    REWORK = "rework"
    COMPLETE = "complete"
    SCRAPPED = "scrapped"


class DefectCode(StrEnum):
    """Defect categories a station can flag (SAMPLED, not real quality data)."""

    PAINT_OVERSPRAY = "paint_overspray"
    BODY_WELD_ISSUE = "body_weld_issue"
    BATTERY_ISOLATION = "battery_isolation"
    POWERTRAIN_MISALIGNMENT = "powertrain_misalignment"
    ELECTRICAL_FAULT = "electrical_fault"


class InspectionResult(StrEnum):
    """Outcome of a quality inspection."""

    PASS = "pass"
    REWORK = "rework"
    FAIL = "fail"


class PartCode(StrEnum):
    """Parts consumed by stations; inventory is tracked per part."""

    BATTERY_PACK = "battery_pack"
    BODY_SHELL = "body_shell"
    PAINT = "paint"
    MOTOR = "motor"
    ELECTRONICS = "electronics"
    INTERIOR = "interior"


class ProductionStation(BaseModel):
    """One station on the line.

    ``capacity_per_day`` is the steady-state throughput (items started per day);
    each item then occupies the station for ``cycle_time_days``. Defects and
    downtime are drawn from the seeded RNG and are SIMPLIFIED — there is no
    real quality or reliability data behind the rates.
    """

    model_config = ConfigDict(frozen=True)

    stage: ProductionStage
    capacity_per_day: float = Field(gt=0, description="Items started per day in steady state")
    cycle_time_days: float = Field(
        default=1.0, gt=0, description="Days one item spends in process at the station"
    )
    defect_rate: float = Field(
        default=0.0, ge=0, lt=1, description="Fraction of processed items flagged defective"
    )
    defect_code: DefectCode | None = Field(
        default=None, description="Defect category this station can produce"
    )
    downtime_probability_per_day: float = Field(
        default=0.0, ge=0, le=1, description="Probability a healthy station breaks down on a day"
    )
    mean_downtime_days: float = Field(
        default=0.0, ge=0, description="Full days lost per downtime event (>= 1 when enabled)"
    )
    rework_fraction: float = Field(
        default=1.0, ge=0, le=1, description="Defective items reworked; the rest are scrapped"
    )
    consumes: tuple[PartCode, ...] = Field(
        default=(), description="Parts withdrawn from inventory for each started item"
    )

    @model_validator(mode="after")
    def _defect_requires_code(self) -> ProductionStation:
        if self.defect_rate > 0.0 and self.defect_code is None:
            raise ValueError(f"station {self.stage.value}: defect_rate requires a defect_code")
        return self

    @model_validator(mode="after")
    def _downtime_requires_duration(self) -> ProductionStation:
        if self.downtime_probability_per_day > 0.0 and self.mean_downtime_days < 1.0:
            raise ValueError(
                f"station {self.stage.value}: downtime requires mean_downtime_days >= 1 (day granularity)"
            )
        return self


class ProductionOrder(BaseModel):
    """An order for a number of a given vehicle variant."""

    model_config = ConfigDict(frozen=True)

    order_id: str
    variant: VehicleVariant
    quantity: int = Field(gt=0)
    release_day: float = Field(default=0.0, ge=0, description="Day the order enters the line")
    target_day: float | None = Field(
        default=None, description="Delivery target; completion after this is 'late'"
    )


class WorkItem(BaseModel):
    """One vehicle in production, tracked through the line.

    Runtime state; every transition is a frozen ``model_copy`` so the history
    of a run is reproducible. ``stage`` is the station the item is at or waits
    for next; ``station_remaining_days`` counts down while in process.
    """

    model_config = ConfigDict(frozen=True)

    item_id: str
    order_id: str
    sequence: int = Field(ge=1)
    stage: ProductionStage
    status: WorkStatus = WorkStatus.QUEUED
    entered_at_day: float = Field(ge=0)
    station_remaining_days: float = Field(default=0.0, ge=0)
    defect_stage: ProductionStage | None = None
    defect_code: DefectCode | None = None
    rework_count: int = Field(default=0, ge=0)
    production_cost_eur: float = Field(default=0.0, ge=0)
    completed_at_day: float | None = None


class InventoryItem(BaseModel):
    """Inventory specification for one part.

    SIMPLIFIED: stock is replenished at a continuous per-day rate; there is no
    order/lead-time logic.
    """

    model_config = ConfigDict(frozen=True)

    part: PartCode
    start_stock: float = Field(default=0.0, ge=0)
    replenish_per_day: float = Field(
        default=0.0, ge=0, description="Continuous inflow from the supplier, units/day"
    )


class QualityInspection(BaseModel):
    """Record of one inspection at the QC station."""

    model_config = ConfigDict(frozen=True)

    inspection_id: str
    item_id: str
    order_id: str
    inspected_at_day: float
    result: InspectionResult
    defect_code: DefectCode | None = None


class FinishedVehicle(BaseModel):
    """A vehicle that passed QC and left the factory."""

    model_config = ConfigDict(frozen=True)

    vin: str
    item_id: str
    order_id: str
    variant: VehicleVariant
    completed_at_day: float
    rework_count: int = Field(ge=0)
    production_cost_eur: float = Field(ge=0)
