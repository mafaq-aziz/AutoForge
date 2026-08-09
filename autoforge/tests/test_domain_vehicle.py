"""Tests for vehicle, battery, and motor domain models."""

import pytest
from pydantic import ValidationError

from autoforge.domain.battery import BatteryPack, CellChemistry
from autoforge.domain.motor import Motor, MotorType
from autoforge.domain.vehicle import Drivetrain, VehicleModel, VehicleSegment, VehicleVariant


def _pack_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "chemistry": CellChemistry.NMC,
        "nominal_energy_kwh": 77.0,
        "usable_energy_kwh": 75.0,
        "nominal_voltage_v": 400.0,
        "max_voltage_v": 450.0,
        "min_voltage_v": 300.0,
        "max_charge_c_rate": 2.5,
        "max_discharge_c_rate": 4.0,
        "nominal_temperature_k": 298.15,
        "cells_in_series": 108,
        "cells_in_parallel": 2,
        "cycle_life_to_80_soh": 1500,
        "mass_kg": 520.0,
    }
    kwargs.update(overrides)
    return kwargs


def _motor_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "motor_type": MotorType.PMSM,
        "peak_power_kw": 230.0,
        "continuous_power_kw": 150.0,
        "peak_efficiency": 0.97,
        "nominal_efficiency": 0.92,
        "mass_kg": 85.0,
    }
    kwargs.update(overrides)
    return kwargs


def _model() -> VehicleModel:
    return VehicleModel(
        name="Aurora",
        segment=VehicleSegment.SEDAN,
        manufacturer="AutoForge Motors",
        launch_year=2025,
    )


def _variant_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "model": _model(),
        "trim_name": "Long Range",
        "kerb_mass_kg": 1900.0,
        "length_m": 4.9,
        "width_m": 1.88,
        "height_m": 1.45,
        "frontal_area_m2": 2.30,
        "drag_coefficient": 0.23,
        "passenger_capacity": 5,
        "wheel_radius_m": 0.36,
        "battery_pack": BatteryPack(**_pack_kwargs()),
        "motor": Motor(**_motor_kwargs()),
        "drivetrain": Drivetrain.RWD,
        "base_price_eur": 45_000.0,
        "variable_cost_eur": 32_000.0,
        "range_target_km": 550.0,
        "target_0_100_kmh_s": 5.9,
    }
    kwargs.update(overrides)
    return kwargs


class TestBatteryPack:
    def test_cell_count_is_series_times_parallel(self) -> None:
        assert BatteryPack(**_pack_kwargs()).total_cell_count == 108 * 2

    def test_nominal_capacity_from_energy_and_voltage(self) -> None:
        pack = BatteryPack(**_pack_kwargs())
        assert pack.nominal_capacity_ah == pytest.approx(77000.0 / 400.0)

    def test_voltage_ordering_enforced(self) -> None:
        with pytest.raises(ValidationError):
            BatteryPack(**_pack_kwargs(min_voltage_v=500.0))

    def test_negative_energy_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BatteryPack(**_pack_kwargs(nominal_energy_kwh=-1.0))

    def test_usable_energy_bounds_enforced(self) -> None:
        with pytest.raises(ValidationError):
            BatteryPack(**_pack_kwargs(usable_energy_kwh=0.0))
        with pytest.raises(ValidationError):
            BatteryPack(**_pack_kwargs(usable_energy_kwh=77.5))
        with pytest.raises(ValidationError):
            BatteryPack(**_pack_kwargs(usable_energy_kwh=-5.0))

    def test_serialization_round_trip(self) -> None:
        pack = BatteryPack(**_pack_kwargs())
        restored = BatteryPack.model_validate_json(pack.model_dump_json())
        assert restored == pack


class TestMotor:
    def test_continuous_power_may_not_exceed_peak(self) -> None:
        with pytest.raises(ValidationError):
            Motor(**_motor_kwargs(continuous_power_kw=300.0))

    def test_efficiency_must_be_below_one(self) -> None:
        with pytest.raises(ValidationError):
            Motor(**_motor_kwargs(peak_efficiency=1.05))

    def test_peak_efficiency_gte_nominal(self) -> None:
        with pytest.raises(ValidationError):
            Motor(**_motor_kwargs(nominal_efficiency=0.99))


class TestVehicleVariant:
    def test_valid_variant_constructs(self) -> None:
        variant = VehicleVariant(**_variant_kwargs())
        assert variant.model.name == "Aurora"
        assert variant.motor.peak_power_kw > variant.motor.continuous_power_kw

    def test_battery_embedded_in_variant(self) -> None:
        assert VehicleVariant(**_variant_kwargs()).battery_pack.chemistry == CellChemistry.NMC

    def test_drag_coefficient_bounds(self) -> None:
        with pytest.raises(ValidationError):
            VehicleVariant(**_variant_kwargs(drag_coefficient=2.0))

    def test_passenger_capacity_bounds(self) -> None:
        with pytest.raises(ValidationError):
            VehicleVariant(**_variant_kwargs(passenger_capacity=0))

    def test_variants_are_immutable_by_default(self) -> None:
        variant = VehicleVariant(**_variant_kwargs())
        with pytest.raises(ValidationError):
            variant.kerb_mass_kg = 999.0  # type: ignore[misc]

    def test_dimensions_and_targets_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            VehicleVariant(**_variant_kwargs(length_m=0.0))
        with pytest.raises(ValidationError):
            VehicleVariant(**_variant_kwargs(width_m=-1.0))
        with pytest.raises(ValidationError):
            VehicleVariant(**_variant_kwargs(target_0_100_kmh_s=0.0))

    def test_serialization_round_trip(self) -> None:
        variant = VehicleVariant(**_variant_kwargs())
        restored = VehicleVariant.model_validate_json(variant.model_dump_json())
        assert restored == variant
