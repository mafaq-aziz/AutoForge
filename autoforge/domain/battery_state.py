"""Battery runtime state, separate from the immutable BatteryPack specification.

``BatteryPack`` (domain/battery.py) is the configuration: chemistry, energy,
voltages, C-rates, thermal limits. This module holds the time-varying state a
BMS-like layer produces: SOC, SOH, electrical quantities, temperature, and
faults. State is frozen so every step is a pure transition of one value into
the next, which keeps simulation reproducible and easy to test.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class BatteryFaultCode(StrEnum):
    """Deterministic rule-based fault indicators.

    This is the transparent baseline that future anomaly-detection work can be
    evaluated against; there is no ML here.
    """

    OVER_TEMPERATURE = "over_temperature"
    UNDER_TEMPERATURE = "under_temperature"
    OVER_CURRENT = "over_current"
    OVER_VOLTAGE = "over_voltage"
    UNDER_VOLTAGE = "under_voltage"
    SOC_OUT_OF_BOUNDS = "soc_out_of_bounds"
    SEVERE_DEGRADATION = "severe_degradation"


class BatteryState(BaseModel):
    """One instant of battery runtime state.

    Units: SOC/SOH fractions of usable energy / initial capacity; voltage in
    volts; current in amperes (positive = discharging); power in kW (positive =
    discharging, matching the powertrain's battery-side convention); temperature
    in kelvin.
    """

    model_config = ConfigDict(frozen=True)

    time_s: float = Field(ge=0)
    soc: float = Field(ge=0, le=1, description="State of charge of usable energy")
    soh: float = Field(gt=0, le=1, description="State of health vs initial capacity")
    voltage_v: float = Field(gt=0, description="Terminal voltage (OCV minus IR drop)")
    current_a: float = Field(description="Pack current; positive discharges")
    power_kw: float = Field(description="Net battery power; positive discharges")
    temperature_k: float = Field(gt=0)
    throughput_kwh: float = Field(
        ge=0, description="Cumulative |energy| moved through the pack (in + out)"
    )
    equivalent_full_cycles: float = Field(ge=0, description="Throughput / usable energy")
    faults: tuple[BatteryFaultCode, ...] = ()
    power_limited: bool = Field(
        default=False, description="Electrical model could not deliver the requested power"
    )
    soc_limited: bool = Field(
        default=False, description="SOC integration hit an energy bound and was clamped"
    )
