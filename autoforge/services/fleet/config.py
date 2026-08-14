"""Fleet runtime configuration, kept separate from the domain records.

Like the factory and battery configs, this is the "how the SIMPLIFIED fleet
behaves" layer: telemetry cadence, operation probability, and the maintenance
rules derived from battery faults and SOH.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FleetConfig(BaseModel):
    """Parameters of the simplified fleet simulation."""

    model_config = ConfigDict(frozen=True)

    telemetry_interval_s: float = Field(
        default=60.0, gt=0, description="Sample battery/powertrain telemetry this often"
    )
    operation_probability: float = Field(
        default=1.0,
        ge=0,
        le=1,
        description="Seeded daily probability each vehicle operates (drives the fleet utilization)",
    )
    maintenance_soh_threshold: float = Field(
        default=0.8,
        gt=0,
        lt=1,
        description="SOH below this schedules a service visit (same baseline as the battery fault)",
    )
    maintenance_days: float = Field(
        default=3.0, ge=0, description="Days a vehicle is out of service for maintenance"
    )
