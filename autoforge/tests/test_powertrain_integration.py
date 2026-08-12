"""Integration tests for the powertrain subsystem on the shared engine.

Reference values are derived by hand from the equations in docs/powertrain.md
for the demo vehicle (kerb 1900 kg, Cd 0.23, A 2.3 m2) and default
PowertrainConfig (motor eff 0.92, drivetrain 0.95, regen 0.65, aux 0.6 kW,
Crr 0.011, usable 75 kWh).
"""

import pytest

from autoforge.apps.simulation.demo import build_demo_variant
from autoforge.data.scenarios import constant_speed_scenario, reference_highway_cycle
from autoforge.domain.scenario import DrivingScenario
from autoforge.domain.vehicle import VehicleVariant
from autoforge.services.vehicle.powertrain import (
    PowertrainConfig,
    PowertrainSimulation,
    PowertrainSimulator,
)


def _simulate(
    variant: VehicleVariant | None = None,
    scenario: DrivingScenario | None = None,
    config: PowertrainConfig | None = None,
    seed: int = 0,
    initial_soc: float = 1.0,
) -> PowertrainSimulation:
    variant = variant if variant is not None else build_demo_variant()
    scenario = scenario if scenario is not None else reference_highway_cycle()
    config = config if config is not None else PowertrainConfig()
    return PowertrainSimulator(
        variant=variant, scenario=scenario, config=config, seed=seed
    ).simulate(initial_soc=initial_soc)


class TestReferenceScenario:
    """Hand-derived expected outputs for the documented reference cycle."""

    def test_energy_consumption_matches_hand_calc(self) -> None:
        result = _simulate().result
        s = result.summary
        assert s.distance_km == pytest.approx(18.0, abs=1e-6)
        assert s.energy_consumed_kwh == pytest.approx(2.9412, rel=1e-3)
        assert s.energy_recovered_kwh == pytest.approx(0.0, abs=1e-9)
        assert s.average_consumption_kwh_per_km == pytest.approx(0.1634, rel=1e-3)
        assert s.final_soc == pytest.approx(0.9608, abs=1e-3)
        assert s.peak_power_kw == pytest.approx(17.65, rel=1e-2)
        assert s.estimated_range_km == pytest.approx(459.0, abs=2.0)

    def test_engine_integration_metadata(self) -> None:
        outcome = _simulate()
        run = outcome.run
        assert run.steps == 600  # 601 samples -> 600 intervals
        assert run.autoforge_version == "0.1.0"
        assert run.seed == 0
        assert run.config["system"] == "powertrain"
        assert run.result["final_soc"] == outcome.result.summary.final_soc

    def test_trajectory_length_and_cumulative_consistency(self) -> None:
        result = _simulate().result
        assert len(result.trajectory) == 600
        last = result.trajectory[-1]
        assert last.energy_consumed_kwh == pytest.approx(result.summary.energy_consumed_kwh)
        assert last.distance_km == pytest.approx(result.summary.distance_km)
        assert last.time_s == pytest.approx(600.0)

    def test_result_serializes(self) -> None:
        result = _simulate().result
        restored = type(result).model_validate_json(result.model_dump_json())
        assert restored == result


class TestScenarioBehavior:
    def test_stationary_vehicle_consumes_only_aux(self) -> None:
        scenario = constant_speed_scenario(duration_s=60.0, speed_mps=0.0, name="stationary")
        result = _simulate(scenario=scenario).result
        s = result.summary
        assert s.distance_km == pytest.approx(0.0)
        assert s.energy_consumed_kwh == pytest.approx(0.6 * 60.0 / 3600.0, rel=1e-9)
        assert s.average_consumption_kwh_per_km is None
        assert s.estimated_range_km is None
        assert s.final_soc == pytest.approx(1.0 - 0.01 / 75.0, abs=1e-9)

    def test_constant_speed_flat_scales_with_duration(self) -> None:
        short = _simulate(scenario=constant_speed_scenario(duration_s=120.0, speed_mps=30.0)).result
        long = _simulate(scenario=constant_speed_scenario(duration_s=600.0, speed_mps=30.0)).result
        assert short.summary.energy_consumed_kwh * 5 == pytest.approx(
            long.summary.energy_consumed_kwh, rel=1e-6
        )
        assert short.summary.distance_km == pytest.approx(3.6)

    def test_uphill_consumes_more_than_downhill(self) -> None:
        uphill = _simulate(
            scenario=constant_speed_scenario(duration_s=120.0, speed_mps=20.0, grade_fraction=0.06)
        ).result
        downhill = _simulate(
            scenario=constant_speed_scenario(duration_s=120.0, speed_mps=20.0, grade_fraction=-0.06)
        ).result
        assert uphill.summary.energy_consumed_kwh > downhill.summary.energy_consumed_kwh

    def test_downhill_regen_hand_calc(self) -> None:
        scenario = constant_speed_scenario(
            duration_s=120.0, speed_mps=20.0, grade_fraction=-0.06, name="downhill"
        )
        result = _simulate(scenario=scenario, initial_soc=0.5).result
        s = result.summary
        # ~15.64 kW negative wheel power, 65% regen efficiency, no limits binding.
        assert s.energy_recovered_kwh == pytest.approx(0.3389, rel=1e-3)
        assert s.energy_consumed_kwh == pytest.approx(0.02, rel=1e-9)  # aux only
        assert s.final_soc == pytest.approx(0.5043, abs=1e-3)
        # Net energy is negative: range is undefined (regenerating more than used).
        assert s.net_energy_kwh < 0.0
        assert s.estimated_range_km is None

    def test_deceleration_braking_recovers_energy(self) -> None:
        # 30 -> 10 m/s over 20 s (a = -1 m/s2), flat.
        time = [float(i) for i in range(21)]
        speed = [30.0 - float(i) for i in range(21)]
        scenario = DrivingScenario(
            name="braking", time_s=time, speed_mps=speed, grade_fraction=[0.0] * 21
        )
        result = _simulate(scenario=scenario, initial_soc=0.5).result
        s = result.summary
        assert s.energy_recovered_kwh > 0.05
        assert all(p.recovered_power_kw > 0.0 for p in result.trajectory)
        # Peak regen: 0.65 * max negative wheel power, hand-computed at t=0.
        assert s.peak_regen_power_kw == pytest.approx(27.09, rel=1e-3)

    def test_auxiliary_load_increases_consumption_linearly(self) -> None:
        scenario = constant_speed_scenario(duration_s=360.0, speed_mps=25.0)
        base = _simulate(scenario=scenario, config=PowertrainConfig(auxiliary_power_kw=0.0)).result
        loaded = _simulate(
            scenario=scenario, config=PowertrainConfig(auxiliary_power_kw=1.2)
        ).result
        delta = loaded.summary.energy_consumed_kwh - base.summary.energy_consumed_kwh
        assert delta == pytest.approx(1.2 * 360.0 / 3600.0, rel=1e-6)

    def test_battery_depletion_limits_power_and_emits_event(self) -> None:
        variant = build_demo_variant()
        pack = variant.battery_pack.model_copy(
            update={
                "nominal_energy_kwh": 0.06,
                "usable_energy_kwh": 0.05,
                "max_discharge_c_rate": 1_000_000.0,
            }
        )
        variant = variant.model_copy(update={"battery_pack": pack})
        outcome = _simulate(variant=variant)
        s = outcome.result.summary
        assert s.final_soc == pytest.approx(0.0)
        assert s.energy_consumed_kwh == pytest.approx(0.05, rel=1e-6)
        assert s.power_limited_seconds > 500.0
        depleted = [e for e in outcome.events if e.get("event") == "battery_depleted"]
        assert len(depleted) == 1
        assert s.min_soc == pytest.approx(0.0)

    def test_empty_battery_cannot_drive(self) -> None:
        result = _simulate(initial_soc=0.0).result
        assert result.summary.energy_consumed_kwh == pytest.approx(0.0)
        assert result.summary.energy_recovered_kwh == pytest.approx(0.0)
        assert result.summary.final_soc == pytest.approx(0.0)
        assert all(p.depleted for p in result.trajectory)


class TestEdgeCases:
    def test_sudden_acceleration_capped_by_motor_peak(self) -> None:
        # 0 -> 30 m/s in one second (a = 30 m/s2) then hold at 30.
        scenario = DrivingScenario(
            name="sudden_accel",
            time_s=[0.0, 1.0, 2.0, 3.0],
            speed_mps=[0.0, 30.0, 30.0, 30.0],
            grade_fraction=[0.0, 0.0, 0.0, 0.0],
        )
        variant = build_demo_variant()
        config = PowertrainConfig()
        outcome = _simulate(variant=variant, scenario=scenario, config=config)
        s = outcome.result.summary
        # The first interval demands ~983 kW of wheel power and is clamped to
        # the 230 kW motor; the next two are ordinary cruise.
        assert s.power_limited_seconds == pytest.approx(1.0)
        assert s.peak_power_kw == pytest.approx(
            variant.motor.peak_power_kw + config.auxiliary_power_kw
        )
        assert outcome.result.trajectory[0].power_limited is True
        assert outcome.result.trajectory[1].power_limited is False

    def test_steep_uphill_consumes_far_more_than_flat(self) -> None:
        # 30 s at 20 m/s on a 30% grade vs flat; hand-derived values.
        flat = _simulate(scenario=constant_speed_scenario(duration_s=30.0, speed_mps=20.0)).result
        uphill = _simulate(
            scenario=constant_speed_scenario(duration_s=30.0, speed_mps=20.0, grade_fraction=0.3)
        ).result
        assert flat.summary.energy_consumed_kwh == pytest.approx(0.0688, rel=1e-3)
        assert uphill.summary.energy_consumed_kwh == pytest.approx(1.0885, rel=1e-3)
        assert uphill.summary.energy_consumed_kwh > 10.0 * flat.summary.energy_consumed_kwh

    def test_very_short_simulation_at_zero_distance(self) -> None:
        outcome = _simulate(
            scenario=constant_speed_scenario(duration_s=1.0, speed_mps=0.0, name="still")
        )
        s = outcome.result.summary
        assert outcome.run.steps == 1
        assert s.distance_km == pytest.approx(0.0)
        assert s.estimated_range_km is None


class TestTimestepAndRobustness:
    def test_constant_speed_is_timestep_invariant(self) -> None:
        dt1 = _simulate(scenario=constant_speed_scenario(duration_s=600.0, speed_mps=30.0)).result
        dt2 = _simulate(
            scenario=constant_speed_scenario(duration_s=600.0, speed_mps=30.0, timestep_s=2.0)
        ).result
        dt05 = _simulate(
            scenario=constant_speed_scenario(duration_s=600.0, speed_mps=30.0, timestep_s=0.5)
        ).result
        assert dt1.summary.energy_consumed_kwh == pytest.approx(
            dt2.summary.energy_consumed_kwh, rel=1e-9
        )
        assert dt1.summary.energy_consumed_kwh == pytest.approx(
            dt05.summary.energy_consumed_kwh, rel=1e-9
        )

    def test_timestep_changes_step_count(self) -> None:
        outcome = _simulate(
            scenario=constant_speed_scenario(duration_s=600.0, speed_mps=30.0, timestep_s=0.5)
        )
        assert outcome.run.steps == 1200
        assert len(outcome.result.trajectory) == 1200

    def test_two_sample_minimum_run(self) -> None:
        scenario = DrivingScenario(
            name="minimal",
            time_s=[0.0, 1.0],
            speed_mps=[10.0, 10.0],
            grade_fraction=[0.0, 0.0],
        )
        outcome = _simulate(scenario=scenario)
        assert len(outcome.result.trajectory) == 1
        assert outcome.run.steps == 1

    def test_invalid_initial_soc_rejected(self) -> None:
        with pytest.raises(ValueError):
            _simulate(initial_soc=1.5)
        with pytest.raises(ValueError):
            _simulate(initial_soc=-0.1)

    def test_low_speed_does_not_divide_by_zero(self) -> None:
        scenario = constant_speed_scenario(duration_s=10.0, speed_mps=0.001, timestep_s=0.5)
        result = _simulate(scenario=scenario).result
        assert result.summary.distance_km == pytest.approx(0.001 * 10.0 / 1000.0, abs=1e-9)
        assert result.summary.peak_power_kw >= 0.0

    def test_energy_conservation_over_run(self) -> None:
        """SOC change times usable energy equals net energy, across the run."""
        scenario = constant_speed_scenario(duration_s=600.0, speed_mps=30.0)
        outcome = _simulate(scenario=scenario, initial_soc=0.6)
        s = outcome.result.summary
        usable = build_demo_variant().battery_pack.usable_energy_kwh
        soc_delta_energy = (0.6 - s.final_soc) * usable
        assert soc_delta_energy == pytest.approx(s.net_energy_kwh, abs=1e-6)


class TestReproducibility:
    def test_identical_inputs_identical_results(self) -> None:
        a = _simulate(seed=1)
        b = _simulate(seed=1)
        assert a.run.run_id == b.run.run_id
        assert a.result == b.result
        assert a.result.summary.model_dump() == a.run.result

    def test_seed_does_not_change_dynamics(self) -> None:
        a = _simulate(seed=1)
        b = _simulate(seed=2)
        assert a.result == b.result
        assert a.run.run_id != b.run.run_id
