"""Factory runtime configuration: the line, inventory, and cost parameters.

Like ``BatteryConfig``, this stays separate from the domain specifications so a
run can record the exact model assumptions without mutating them.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoforge.domain.factory import (
    DefectCode,
    InventoryItem,
    PartCode,
    ProductionStage,
    ProductionStation,
)


def default_line() -> tuple[ProductionStation, ...]:
    """The demo line; bottleneck capacity is 10 items/day.

    BATTERY, PAINT, and FINAL_ASSEMBLY all run at 10/day while the other
    stations run faster, so the bottleneck is unambiguous and hand-calculable.
    """
    return (
        ProductionStation(stage=ProductionStage.RAW, capacity_per_day=1000.0),
        ProductionStation(
            stage=ProductionStage.BATTERY,
            capacity_per_day=10.0,
            defect_code=DefectCode.BATTERY_ISOLATION,
            consumes=(PartCode.BATTERY_PACK,),
        ),
        ProductionStation(
            stage=ProductionStage.BODY,
            capacity_per_day=12.0,
            defect_code=DefectCode.BODY_WELD_ISSUE,
            consumes=(PartCode.BODY_SHELL,),
        ),
        ProductionStation(
            stage=ProductionStage.PAINT,
            capacity_per_day=10.0,
            defect_code=DefectCode.PAINT_OVERSPRAY,
            consumes=(PartCode.PAINT,),
        ),
        ProductionStation(
            stage=ProductionStage.POWERTRAIN,
            capacity_per_day=11.0,
            defect_code=DefectCode.POWERTRAIN_MISALIGNMENT,
            consumes=(PartCode.MOTOR, PartCode.ELECTRONICS),
        ),
        ProductionStation(
            stage=ProductionStage.FINAL_ASSEMBLY,
            capacity_per_day=10.0,
            defect_code=DefectCode.ELECTRICAL_FAULT,
            consumes=(PartCode.INTERIOR,),
        ),
        ProductionStation(stage=ProductionStage.QUALITY_INSPECTION, capacity_per_day=15.0),
    )


def default_inventory() -> tuple[InventoryItem, ...]:
    """Plentiful default stock; parts never run out unless overridden."""
    return tuple(
        InventoryItem(part=part, start_stock=10_000.0)
        for part in (
            PartCode.BATTERY_PACK,
            PartCode.BODY_SHELL,
            PartCode.PAINT,
            PartCode.MOTOR,
            PartCode.ELECTRONICS,
            PartCode.INTERIOR,
        )
    )


class FactoryConfig(BaseModel):
    """One factory run: line, inventory, and cost assumptions."""

    model_config = ConfigDict(frozen=True)

    line: tuple[ProductionStation, ...] = Field(default_factory=default_line)
    inventory: tuple[InventoryItem, ...] = Field(default_factory=default_inventory)
    rework_repeat_limit: int = Field(
        default=2, ge=0, description="Reworks before a defective item is scrapped"
    )
    rework_cost_eur: float = Field(default=1500.0, ge=0, description="Added to the item per rework")
    scrap_cost_eur: float = Field(
        default=20_000.0, ge=0, description="Lost value charged per scrapped item"
    )

    @model_validator(mode="after")
    def _line_starts_raw_ends_qc(self) -> FactoryConfig:
        order = {stage: i for i, stage in enumerate(ProductionStage)}
        if not self.line:
            raise ValueError("factory line must not be empty")
        if self.line[0].stage != ProductionStage.RAW:
            raise ValueError("line must start at the RAW stage")
        if self.line[-1].stage != ProductionStage.QUALITY_INSPECTION:
            raise ValueError("line must end at the QUALITY_INSPECTION stage")
        seen: set[ProductionStage] = set()
        for station in self.line:
            if station.stage in seen:
                raise ValueError(f"duplicate station stage {station.stage.value}")
            seen.add(station.stage)
        stages = [s.stage for s in self.line]
        if stages != sorted(stages, key=lambda s: order[s]):
            raise ValueError("line stages must follow the canonical ProductionStage order")
        return self

    @property
    def bottleneck_capacity_per_day(self) -> float:
        """Theoretical steady-state throughput: the slowest station."""
        return min(s.capacity_per_day for s in self.line)
