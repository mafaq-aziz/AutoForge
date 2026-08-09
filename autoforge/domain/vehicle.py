"""Vehicle product domain models.

A model is a product family (e.g. "Aurora"); a variant is a buildable
configuration of that model. All physics-relevant fields use SI units, with
industry-conventional units (km, kWh) explicitly named in the field suffix.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from autoforge.domain.battery import BatteryPack
from autoforge.domain.motor import Motor


class VehicleSegment(StrEnum):
    """Market segment a model targets; used by the market simulator later."""

    COMPACT = "compact"
    SEDAN = "sedan"
    SUV = "suv"
    TRUCK = "truck"
    VAN = "van"


class Drivetrain(StrEnum):
    """Which axle(s) the motor(s) drive; affects efficiency and cost."""

    FWD = "fwd"
    RWD = "rwd"
    AWD = "awd"


class VehicleModel(BaseModel):
    """A product family shared by one or more variants."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    segment: VehicleSegment
    manufacturer: str = Field(min_length=1)
    launch_year: int = Field(ge=1990)


class VehicleVariant(BaseModel):
    """A buildable configuration of a model, fully specifying its physics."""

    model_config = ConfigDict(frozen=True)

    model: VehicleModel
    trim_name: str = Field(min_length=1)
    # Body and dimensions (SI)
    kerb_mass_kg: float = Field(gt=0, description="Mass without cargo or occupants")
    length_m: float = Field(gt=0)
    width_m: float = Field(gt=0)
    height_m: float = Field(gt=0)
    frontal_area_m2: float = Field(gt=0)
    drag_coefficient: float = Field(gt=0, lt=1.5)
    passenger_capacity: int = Field(ge=1, le=9)
    wheel_radius_m: float = Field(gt=0)
    # Powertrain
    battery_pack: BatteryPack
    motor: Motor
    drivetrain: Drivetrain
    # Commercial targets
    base_price_eur: float = Field(gt=0)
    variable_cost_eur: float = Field(gt=0, description="Cost to build one unit")
    range_target_km: float = Field(
        gt=0, description="WLTP-like target; validated by powertrain later"
    )
    target_0_100_kmh_s: float = Field(gt=0, description="Target 0-100 km/h time in seconds")
