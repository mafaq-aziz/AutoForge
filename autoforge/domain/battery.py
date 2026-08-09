"""Battery pack domain model.

SI units are used throughout; energy is stored in kWh (industry convention) but
every field name carries its unit suffix so there is never ambiguity.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CellChemistry(StrEnum):
    """Common automotive cell chemistries; used as labelled inputs to models."""

    NMC = "nmc"
    LFP = "lfp"
    NCA = "nca"
    LCO = "lco"
    SOLID_STATE = "solid_state"


class BatteryPack(BaseModel):
    """Pack-level specification.

    The series/parallel layout pins the nominal voltage and capacity trade-off
    and is the input the BMS module needs for per-cell imbalance modelling.
    """

    model_config = ConfigDict(frozen=True)

    chemistry: CellChemistry
    nominal_energy_kwh: float = Field(gt=0, description="Total pack energy at 100% SOC")
    usable_energy_kwh: float = Field(
        gt=0, description="Energy usable between SOC bounds; basis for SOC and range"
    )
    nominal_voltage_v: float = Field(gt=0, description="Pack terminal voltage at nominal SOC")
    max_voltage_v: float = Field(gt=0, description="Upper terminal voltage limit (full charge)")
    min_voltage_v: float = Field(gt=0, description="Lower terminal voltage limit (cutoff)")
    max_charge_c_rate: float = Field(gt=0, description="Max continuous charge rate, 1/h")
    max_discharge_c_rate: float = Field(gt=0, description="Max continuous discharge rate, 1/h")
    nominal_temperature_k: float = Field(gt=0, description="Nominal operating temperature")
    cells_in_series: int = Field(ge=1)
    cells_in_parallel: int = Field(ge=1)
    cycle_life_to_80_soh: int = Field(
        ge=1, description="Full cycles until 80% SOH at reference conditions"
    )
    mass_kg: float = Field(gt=0)

    @model_validator(mode="after")
    def _voltage_ordering(self) -> BatteryPack:
        if not (self.min_voltage_v < self.nominal_voltage_v < self.max_voltage_v):
            raise ValueError("voltage ordering violated: min < nominal < max")
        if not 0.0 < self.usable_energy_kwh <= self.nominal_energy_kwh:
            raise ValueError("usable energy must be positive and no more than nominal energy")
        return self

    @property
    def total_cell_count(self) -> int:
        """Number of cells in the pack (series x parallel)."""
        return self.cells_in_series * self.cells_in_parallel

    @property
    def nominal_capacity_ah(self) -> float:
        """Nominal capacity in ampere-hours (energy / nominal voltage)."""
        return self.nominal_energy_kwh * 1000.0 / self.nominal_voltage_v
