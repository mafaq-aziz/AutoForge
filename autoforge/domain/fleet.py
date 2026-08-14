"""Fleet domain models: daily operations, telemetry samples, and maintenance.

The fleet phase is the DATA step of the core loop: vehicles finished by the
factory are operated against a scenario day by day, their battery telemetry is
sampled at a configurable interval, and battery faults / SOH feed simple
scheduled-maintenance rules. All records are frozen outputs.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from autoforge.domain.battery_state import BatteryFaultCode


class TelemetryPoint(BaseModel):
    """One sampled data point from a vehicle during an operation.

    Fields come straight from the powertrain and battery trajectories at the
    same timestamp; ``odometer_km`` is the vehicle's lifetime distance.
    """

    model_config = ConfigDict(frozen=True)

    vin: str
    day: float = Field(ge=0)
    time_s: float = Field(ge=0)
    speed_mps: float = Field(ge=0)
    battery_power_kw: float
    soc: float = Field(ge=0, le=1)
    soh: float = Field(gt=0, le=1)
    temperature_k: float = Field(gt=0)
    current_a: float
    voltage_v: float = Field(gt=0)
    odometer_km: float = Field(ge=0)
    faults: tuple[BatteryFaultCode, ...] = ()


class VehicleOperation(BaseModel):
    """One vehicle driving one scenario on one day."""

    model_config = ConfigDict(frozen=True)

    vin: str
    order_id: str
    day: float = Field(ge=0)
    scenario_name: str
    distance_km: float = Field(ge=0)
    energy_kwh: float = Field(ge=0, description="Net battery energy for the drive")
    peak_power_kw: float = Field(ge=0)
    min_soc: float = Field(ge=0, le=1)
    final_soc: float = Field(ge=0, le=1)
    final_soh: float = Field(gt=0, le=1)
    max_temperature_k: float = Field(gt=0)
    fault_codes: tuple[str, ...] = ()
    power_limited: bool = False
    depleted: bool = False


class MaintenanceEvent(BaseModel):
    """A scheduled service visit triggered by a battery fault or low SOH."""

    model_config = ConfigDict(frozen=True)

    vin: str
    start_day: float = Field(ge=0)
    duration_days: float = Field(ge=0)
    reason: str = Field(description="Fault code value, or 'low_soh' for SOH-driven service")
