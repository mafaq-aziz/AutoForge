"""Electric traction motor domain model."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MotorType(StrEnum):
    """Common EV traction motor types; informs efficiency/loss modelling later."""

    PMSM = "pmsm"
    INDUCTION = "induction"
    SWITCHED_RELUCTANCE = "switched_reluctance"


class Motor(BaseModel):
    """Motor specification; power in kW, mass in kg, efficiency as a fraction."""

    model_config = ConfigDict(frozen=True)

    motor_type: MotorType
    peak_power_kw: float = Field(gt=0, description="Short-duration peak output")
    continuous_power_kw: float = Field(gt=0, description="Sustained output limit")
    peak_efficiency: float = Field(gt=0, lt=1, description="Best-case conversion efficiency")
    nominal_efficiency: float = Field(gt=0, lt=1, description="Typical operating-point efficiency")
    mass_kg: float = Field(gt=0)

    @model_validator(mode="after")
    def _power_and_efficiency_sane(self) -> Motor:
        if self.continuous_power_kw > self.peak_power_kw:
            raise ValueError("continuous power cannot exceed peak power")
        if self.peak_efficiency < self.nominal_efficiency:
            raise ValueError("peak efficiency cannot be below nominal efficiency")
        return self
