"""Unit tests for the EV powertrain physics and battery energy model."""

import pytest
from pydantic import ValidationError

from autoforge.apps.simulation.demo import build_demo_variant
from autoforge.services.vehicle.powertrain import (
    PowertrainConfig,
    longitudinal_force,
    step_interval,
)

# Expected values below are derived by hand from the equations in
# docs/powertrain.md, not fitted to the code.


def test_config_defaults_are_physically_sane() -> None:
    config = PowertrainConfig()
    assert 0.0 < config.rolling_resistance_coefficient < 0.05
    assert 0.0 < config.motor_efficiency < 1.0
    assert 0.0 < config.regen_efficiency < 1.0
    assert config.gravity_mps2 == pytest.approx(9.81)


def test_config_soc_bounds_enforced() -> None:
    with pytest.raises(ValidationError):
        PowertrainConfig(soc_min=0.8, soc_max=0.2)


def test_flat_constant_speed_force_matches_hand_calc() -> None:
    variant = build_demo_variant()
    config = PowertrainConfig()
    force = longitudinal_force(
        config,
        total_mass_kg=variant.kerb_mass_kg,
        frontal_area_m2=variant.frontal_area_m2,
        drag_coefficient=variant.drag_coefficient,
        speed_mps=30.0,
        acceleration_mps2=0.0,
        grade_fraction=0.0,
    )
    drag = 0.5 * 1.225 * 0.23 * 2.3 * 30.0**2
    rolling = 0.011 * 1900.0 * 9.81
    assert force == pytest.approx(drag + rolling, rel=1e-9)


def test_zero_speed_has_no_aero_drag() -> None:
    variant = build_demo_variant()
    force = longitudinal_force(
        PowertrainConfig(),
        total_mass_kg=variant.kerb_mass_kg,
        frontal_area_m2=variant.frontal_area_m2,
        drag_coefficient=variant.drag_coefficient,
        speed_mps=0.0,
        acceleration_mps2=0.0,
        grade_fraction=0.0,
    )
    assert force == pytest.approx(0.011 * 1900.0 * 9.81)


def test_grade_force_uses_exact_trigonometry() -> None:
    config = PowertrainConfig(rolling_resistance_coefficient=0.0)
    mass = 1900.0
    g = config.gravity_mps2
    # 6% grade uphill: sin = 0.06 / sqrt(1 + 0.06^2)
    expected_sin = 0.06 / (1 + 0.06**2) ** 0.5
    uphill = longitudinal_force(
        config,
        total_mass_kg=mass,
        frontal_area_m2=1.0,
        drag_coefficient=0.0,
        speed_mps=1.0,
        acceleration_mps2=0.0,
        grade_fraction=0.06,
    )
    assert uphill == pytest.approx(mass * g * expected_sin, rel=1e-9)
    downhill = longitudinal_force(
        config,
        total_mass_kg=mass,
        frontal_area_m2=1.0,
        drag_coefficient=0.0,
        speed_mps=1.0,
        acceleration_mps2=0.0,
        grade_fraction=-0.06,
    )
    assert downhill == pytest.approx(-mass * g * expected_sin, rel=1e-9)


def test_acceleration_force_is_inertial() -> None:
    config = PowertrainConfig(rolling_resistance_coefficient=0.0)
    force = longitudinal_force(
        config,
        total_mass_kg=1900.0,
        frontal_area_m2=0.0,
        drag_coefficient=0.0,
        speed_mps=1.0,
        acceleration_mps2=2.0,
        grade_fraction=0.0,
    )
    assert force == pytest.approx(1900.0 * 2.0)


class TestStepInterval:
    def test_discharge_soc_tracks_energy(self) -> None:
        variant = build_demo_variant()
        config = PowertrainConfig()
        result = step_interval(
            config,
            variant.battery_pack,
            variant.motor,
            soc=1.0,
            wheel_power_kw=17.0469,
            dt_s=1.0,
        )
        # battery draw ~17.0469/0.874 + 0.6 kW over 1 s
        draw = 17.0469 / (0.92 * 0.95) + 0.6
        expected_soc = 1.0 - (draw / 3600.0) / variant.battery_pack.usable_energy_kwh
        assert result.soc == pytest.approx(expected_soc, rel=1e-9)
        assert result.energy_consumed_kwh == pytest.approx(draw / 3600.0, rel=1e-9)
        assert result.power_limited is False
        assert result.depleted is False

    def test_discharge_capped_by_motor_peak_power(self) -> None:
        variant = build_demo_variant()
        result = step_interval(
            PowertrainConfig(),
            variant.battery_pack,
            variant.motor,
            soc=1.0,
            wheel_power_kw=500.0,  # demands far above the 230 kW motor
            dt_s=1.0,
        )
        assert result.traction_power_kw <= variant.motor.peak_power_kw + 1e-9
        assert result.power_limited is True

    def test_regen_recovered_within_limits(self) -> None:
        variant = build_demo_variant()
        config = PowertrainConfig()
        result = step_interval(
            config, variant.battery_pack, variant.motor, soc=0.5, wheel_power_kw=-15.0, dt_s=1.0
        )
        assert result.recovered_power_kw == pytest.approx(15.0 * 0.65, rel=1e-9)
        assert result.energy_recovered_kwh == pytest.approx(15.0 * 0.65 / 3600.0, rel=1e-9)
        assert result.soc > 0.5

    def test_regen_at_full_soc_offsets_aux_only(self) -> None:
        variant = build_demo_variant()
        config = PowertrainConfig()
        result = step_interval(
            config,
            variant.battery_pack,
            variant.motor,
            soc=config.soc_max,
            wheel_power_kw=-15.0,
            dt_s=1.0,
        )
        # At SOC max only the concurrent aux draw (0.6 kW) can be offset; the
        # rest must be discarded so SOC never exceeds its bound.
        assert result.recovered_power_kw == pytest.approx(config.auxiliary_power_kw, rel=1e-9)
        assert result.soc == pytest.approx(config.soc_max)
        assert result.energy_discarded_kwh == pytest.approx(
            (15.0 * 0.65 - config.auxiliary_power_kw) / 3600.0, rel=1e-9
        )

    def test_regen_capped_by_max_regen_power(self) -> None:
        variant = build_demo_variant()
        config = PowertrainConfig(max_regen_power_kw=30.0)
        result = step_interval(
            config, variant.battery_pack, variant.motor, soc=0.5, wheel_power_kw=-200.0, dt_s=1.0
        )
        assert result.recovered_power_kw == pytest.approx(30.0, rel=1e-9)

    def test_depletion_never_below_soc_min(self) -> None:
        variant = build_demo_variant()
        config = PowertrainConfig(soc_min=0.1)
        result = step_interval(
            config,
            variant.battery_pack,
            variant.motor,
            soc=config.soc_min,
            wheel_power_kw=1000.0,
            dt_s=1.0,
        )
        assert result.soc == pytest.approx(config.soc_min)
        assert result.depleted is True
        assert result.energy_consumed_kwh == pytest.approx(0.0)

    def test_depletion_flags_when_energy_exhausted(self) -> None:
        variant = build_demo_variant()
        config = PowertrainConfig()
        # Tiny usable energy so one hard second fully empties the pack; the
        # discharge C-rate cap is lifted so depletion (not the cap) triggers.
        pack = variant.battery_pack.model_copy(
            update={
                "usable_energy_kwh": 0.0001,
                "nominal_energy_kwh": 0.0001,
                "max_discharge_c_rate": 1_000_000.0,
            }
        )
        result = step_interval(config, pack, variant.motor, soc=1.0, wheel_power_kw=50.0, dt_s=1.0)
        assert result.depleted is True
        assert result.soc == pytest.approx(config.soc_min)
        assert result.energy_consumed_kwh == pytest.approx(pack.usable_energy_kwh, rel=1e-6)

    def test_zero_timestep_rejected(self) -> None:
        variant = build_demo_variant()
        with pytest.raises(ValueError):
            step_interval(
                PowertrainConfig(),
                variant.battery_pack,
                variant.motor,
                soc=1.0,
                wheel_power_kw=1.0,
                dt_s=0.0,
            )

    def test_soc_out_of_bounds_rejected(self) -> None:
        variant = build_demo_variant()
        with pytest.raises(ValueError):
            step_interval(
                PowertrainConfig(),
                variant.battery_pack,
                variant.motor,
                soc=1.5,
                wheel_power_kw=1.0,
                dt_s=1.0,
            )

    def test_energy_is_never_created(self) -> None:
        """SOC change must equal -(draw - regen) regardless of state."""
        variant = build_demo_variant()
        config = PowertrainConfig()
        pack = variant.battery_pack
        usable = pack.usable_energy_kwh
        soc0 = 0.5
        result = step_interval(
            config, pack, variant.motor, soc=soc0, wheel_power_kw=-10.0, dt_s=2.0
        )
        delta_energy = -(result.energy_consumed_kwh - result.energy_recovered_kwh)
        assert (result.soc - soc0) * usable == pytest.approx(delta_energy, abs=1e-12)
