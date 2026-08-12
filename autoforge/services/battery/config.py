"""Battery model parameters (runtime model), kept separate from the pack spec.

``BatteryPack`` states what the pack *is*; ``BatteryConfig`` states how the
SIMPLIFIED model behaves. Separating the two lets a run record the exact model
assumptions without mutating the physical specification.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BatteryConfig(BaseModel):
    """Parameters of the simplified electrical/thermal/degradation model.

    Units: temperature in kelvin, resistance in ohm, thermal capacity in J/K,
    cooling coefficient in W/K. SOC/SOH values are fractions.
    """

    model_config = ConfigDict(frozen=True)

    ambient_temperature_k: float = Field(default=298.15, gt=200, lt=400)
    internal_resistance_ohm: float = Field(
        default=0.05, gt=0, description="Pack-level series resistance for the IR voltage drop"
    )
    thermal_capacity_j_per_k: float = Field(default=100_000.0, gt=0)
    cooling_coefficient_w_per_k: float = Field(
        default=50.0, ge=0, description="Newton-style heat loss to ambient, W per K of delta"
    )
    soh_floor: float = Field(
        default=0.6, gt=0, lt=1, description="Lower SOH clamp of the degradation baseline"
    )
    soh_fault_threshold: float = Field(
        default=0.8, gt=0, lt=1, description="SOH below this raises the severe_degradation fault"
    )

    @model_validator(mode="after")
    def _soh_threshold_above_floor(self) -> BatteryConfig:
        if self.soh_fault_threshold <= self.soh_floor:
            raise ValueError("soh_fault_threshold must be above soh_floor")
        return self
