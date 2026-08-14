"""Tests for the simplified battery model and its powertrain integration.

The model must be deterministic (no RNG), energy-conserving (its SOC is
integrated from net battery power with the same clamping the powertrain uses),
and contain at least one hand-calculable case. Hand calculations are inline
where used and follow docs/battery.md.
"""

import pytest

from autoforge.apps.simulation.demo import build_demo_variant
from autoforge.data.scenarios import reference_highway_cycle
from autoforge.domain.battery_state import BatteryFaultCode, BatteryState
from autoforge.domain.scenario import DrivingScenario
from autoforge.services.battery.config import BatteryConfig
from autoforge.services.battery.model import (
    BatteryModel,
    BatterySimulator,
    current_from_power,
    detect_faults,
    ocv_voltage,
    soh_from_throughput,
    thermal_step,
)
from autoforge.services.vehicle.powertrain import PowertrainSimulation, PowertrainSimulator

PACK = build_demo_variant().battery_pack  # usable 75 kWh, 300..450 V, 1500 cycles to 80% SOH


def _state(
    *,
    time_s: float = 0.0,
    soc: float = 0.5,
    soh: float = 1.0,
    voltage_v: float = 375.0,
    current_a: float = 0.0,
    power_kw: float = 0.0,
    temperature_k: float = 298.15,
    throughput_kwh: float = 0.0,
    equivalent_full_cycles: float = 0.0,
) -> BatteryState:
    return BatteryState(
        time_s=time_s,
        soc=soc,
        soh=soh,
        voltage_v=voltage_v,
        current_a=current_a,
        power_kw=power_kw,
        temperature_k=temperature_k,
        throughput_kwh=throughput_kwh,
        equivalent_full_cycles=equivalent_full_cycles,
    )


def _powertrain(scenario: DrivingScenario | None = None) -> PowertrainSimulation:
    scenario = scenario if scenario is not None else reference_highway_cycle()
    return PowertrainSimulator(variant=build_demo_variant(), scenario=scenario, seed=7).simulate()


class TestConfig:
    def test_defaults_are_sane(self) -> None:
        cfg = BatteryConfig()
        assert cfg.ambient_temperature_k == 298.15
        assert cfg.internal_resistance_ohm == 0.05
        assert cfg.thermal_capacity_j_per_k == 100_000.0
        assert cfg.cooling_coefficient_w_per_k == 50.0
        assert cfg.soh_floor == 0.6
        assert cfg.soh_fault_threshold == 0.8

    def test_soh_threshold_must_exceed_floor(self) -> None:
        with pytest.raises(ValueError):
            BatteryConfig(soh_floor=0.85, soh_fault_threshold=0.8)


class TestElectrical:
    def test_ocv_is_linear_between_voltage_bounds(self) -> None:
        assert ocv_voltage(PACK, 0.0) == pytest.approx(300.0)
        assert ocv_voltage(PACK, 1.0) == pytest.approx(450.0)
        assert ocv_voltage(PACK, 0.5) == pytest.approx(375.0)

    def test_zero_power_means_zero_current(self) -> None:
        current_a, limited = current_from_power(450.0, 0.05, 0.0)
        assert current_a == pytest.approx(0.0)
        assert limited is False

    def test_hand_calc_current_and_voltage(self) -> None:
        # OCV=450 V at soc 1.0, R=0.05 ohm, P=50 kW.
        # I = (450 - sqrt(450^2 - 4*0.05*50e3)) / (2*0.05) = 112.52 A
        # V = 450 - 0.05*I = 444.37 V; V*I must equal P exactly on the branch.
        current_a, limited = current_from_power(450.0, 0.05, 50_000.0)
        assert current_a == pytest.approx(112.518, rel=1e-4)
        assert limited is False
        voltage_v = 450.0 - 0.05 * current_a
        assert voltage_v == pytest.approx(444.374, rel=1e-4)
        assert voltage_v * current_a == pytest.approx(50_000.0, rel=1e-9)

    def test_charging_is_negative_current(self) -> None:
        current_a, _ = current_from_power(450.0, 0.05, -50_000.0)
        assert current_a < 0.0

    def test_impossible_power_clamps_to_max_power_point(self) -> None:
        # P > OCV^2/(4R) = 1.0125 MW at 450 V cannot be delivered.
        current_a, limited = current_from_power(450.0, 0.05, 2_000_000.0)
        assert limited is True
        assert current_a == pytest.approx(450.0 / (2.0 * 0.05))

    def test_nonpositive_resistance_rejected(self) -> None:
        with pytest.raises(ValueError):
            current_from_power(450.0, 0.0, 1000.0)


class TestThermal:
    def test_zero_heat_holds_ambient(self) -> None:
        cfg = BatteryConfig()
        assert thermal_step(cfg, cfg.ambient_temperature_k, 0.0, 60.0) == pytest.approx(
            cfg.ambient_temperature_k
        )

    def test_steady_state_equilibrium(self) -> None:
        # Constant heat: equilibrium where heat == cooling => T = ambient + heat/k.
        cfg = BatteryConfig()
        heat_w = 500.0
        equilibrium = cfg.ambient_temperature_k + heat_w / cfg.cooling_coefficient_w_per_k
        next_t = thermal_step(cfg, equilibrium, heat_w, 60.0)
        assert next_t == pytest.approx(equilibrium)

    def test_cooling_toward_ambient(self) -> None:
        cfg = BatteryConfig()
        cooled = thermal_step(cfg, 320.0, 0.0, 60.0)
        assert cfg.ambient_temperature_k < cooled < 320.0


class TestDegradation:
    def test_fresh_pack_is_100_percent_soh(self) -> None:
        soh, efc = soh_from_throughput(PACK, BatteryConfig(), 0.0)
        assert soh == pytest.approx(1.0)
        assert efc == pytest.approx(0.0)

    def test_soh_reaches_0_8_at_rated_cycle_life(self) -> None:
        cfg = BatteryConfig()
        throughput = PACK.cycle_life_to_80_soh * PACK.usable_energy_kwh
        soh, efc = soh_from_throughput(PACK, cfg, throughput)
        assert efc == pytest.approx(PACK.cycle_life_to_80_soh)
        assert soh == pytest.approx(0.8, rel=1e-9)

    def test_soh_clamped_at_floor(self) -> None:
        cfg = BatteryConfig()
        soh, _ = soh_from_throughput(PACK, cfg, 1e7)
        assert soh == pytest.approx(cfg.soh_floor)

    def test_throughput_counts_charge_and_discharge(self) -> None:
        model = BatteryModel(PACK, BatteryConfig())
        state = model.initial_state(soc=0.5)
        state = model.step(state, power_kw=50.0, dt_s=600.0)
        state = model.step(state, power_kw=-50.0, dt_s=600.0)
        assert state.throughput_kwh == pytest.approx(50.0 * 1200.0 / 3600.0)
        assert state.equivalent_full_cycles == pytest.approx(
            state.throughput_kwh / PACK.usable_energy_kwh
        )


class TestFaultRules:
    def test_temperature_faults(self) -> None:
        assert BatteryFaultCode.OVER_TEMPERATURE in detect_faults(
            PACK, BatteryConfig(), _state(temperature_k=340.0)
        )
        assert BatteryFaultCode.UNDER_TEMPERATURE in detect_faults(
            PACK, BatteryConfig(), _state(temperature_k=250.0)
        )

    def test_current_faults(self) -> None:
        # Max discharge 4.0 C * 192.5 Ah = 770 A; max charge 2.5 C * 192.5 Ah = 481.25 A.
        assert BatteryFaultCode.OVER_CURRENT in detect_faults(
            PACK, BatteryConfig(), _state(current_a=800.0)
        )
        assert BatteryFaultCode.OVER_CURRENT in detect_faults(
            PACK, BatteryConfig(), _state(current_a=-500.0)
        )

    def test_voltage_faults(self) -> None:
        assert BatteryFaultCode.OVER_VOLTAGE in detect_faults(
            PACK, BatteryConfig(), _state(voltage_v=451.0)
        )
        assert BatteryFaultCode.UNDER_VOLTAGE in detect_faults(
            PACK, BatteryConfig(), _state(voltage_v=299.0)
        )

    def test_soh_fault(self) -> None:
        assert BatteryFaultCode.SEVERE_DEGRADATION in detect_faults(
            PACK, BatteryConfig(), _state(soh=0.79)
        )

    def test_healthy_state_has_no_faults(self) -> None:
        assert detect_faults(PACK, BatteryConfig(), _state()) == ()


class TestModelStep:
    def test_initial_state_validation(self) -> None:
        model = BatteryModel(PACK, BatteryConfig())
        with pytest.raises(ValueError):
            model.initial_state(soc=1.5)
        with pytest.raises(ValueError):
            model.initial_state(soh=0.0)

    def test_hand_calc_step(self) -> None:
        # Single 600 s step at 50 kW from a full battery, default config.
        # SOC drop = 50 * (600/3600) / 75 = 0.1111
        # heat = I^2*R = 112.52^2 * 0.05 = 633.0 W
        # dT = heat*600 / C_th = 3.798 K
        model = BatteryModel(PACK, BatteryConfig())
        state = model.step(model.initial_state(), power_kw=50.0, dt_s=600.0)
        assert state.time_s == pytest.approx(600.0)
        assert state.soc == pytest.approx(0.888889, rel=1e-4)
        assert state.throughput_kwh == pytest.approx(8.33333, rel=1e-4)
        assert state.equivalent_full_cycles == pytest.approx(0.111111, rel=1e-4)
        assert state.temperature_k == pytest.approx(298.15 + 3.798, rel=1e-3)
        assert state.faults == ()
        assert state.power_limited is False
        assert state.soc_limited is False

    def test_discharge_clamped_at_soc_min(self) -> None:
        model = BatteryModel(PACK, BatteryConfig())
        state = model.step(model.initial_state(), power_kw=200.0, dt_s=3600.0)
        assert state.soc == pytest.approx(0.0)
        assert state.soc_limited is True

    def test_charge_clamped_at_soc_max_raises_over_voltage(self) -> None:
        # Charging a full battery: OCV = 450 V, charging current pushes the
        # terminal voltage above V_max, which is exactly what a BMS must flag.
        model = BatteryModel(PACK, BatteryConfig())
        state = model.step(model.initial_state(), power_kw=-50.0, dt_s=600.0)
        assert state.soc == pytest.approx(1.0)
        assert state.soc_limited is True
        assert BatteryFaultCode.OVER_VOLTAGE in state.faults

    def test_initial_state_is_deterministic(self) -> None:
        a = BatteryModel(PACK, BatteryConfig()).initial_state()
        b = BatteryModel(PACK, BatteryConfig()).initial_state()
        assert a == b


class TestBatterySimulator:
    def test_reference_cycle_matches_powertrain_soc(self) -> None:
        powertrain = _powertrain()
        result = BatterySimulator(build_demo_variant()).simulate(powertrain)
        assert isinstance(result.summary.source_run_id, str)
        assert result.summary.source_run_id == powertrain.run.run_id
        assert result.summary.final_soc == pytest.approx(
            powertrain.result.summary.final_soc, rel=1e-9
        )
        assert result.summary.max_soc_error < 1e-6
        assert len(result.trajectory) == len(powertrain.result.trajectory)

    def test_reference_cycle_throughput_equals_gross_energy(self) -> None:
        powertrain = _powertrain()
        result = BatterySimulator(build_demo_variant()).simulate(powertrain)
        assert result.summary.throughput_kwh == pytest.approx(
            powertrain.result.summary.energy_consumed_kwh, rel=1e-9
        )
        assert result.summary.fault_counts == {}
        assert result.summary.equivalent_full_cycles == pytest.approx(
            result.summary.throughput_kwh / PACK.usable_energy_kwh
        )

    def test_reproducible_for_same_seed(self) -> None:
        a = BatterySimulator(build_demo_variant()).simulate(_powertrain())
        b = BatterySimulator(build_demo_variant()).simulate(_powertrain())
        assert a.trajectory == b.trajectory
        assert a.summary == b.summary

    def test_regen_scenario_charges_battery(self) -> None:
        scenario = DrivingScenario(
            name="hard_stop",
            time_s=(0.0, 1.0, 2.0),
            speed_mps=(30.0, 5.0, 0.0),
            grade_fraction=(0.0, 0.0, 0.0),
        )
        # Start at 80% SOC so there is headroom; a full battery has none and
        # would discard all regen (physically correct).
        powertrain = PowertrainSimulator(
            variant=build_demo_variant(), scenario=scenario, seed=7
        ).simulate(initial_soc=0.8)
        assert any(p.battery_power_kw < 0.0 for p in powertrain.result.trajectory)
        result = BatterySimulator(build_demo_variant()).simulate(powertrain, initial_soc=0.8)
        assert any(p.power_kw < 0.0 for p in result.trajectory)
        assert result.summary.max_soc_error < 1e-6
        assert result.summary.final_soc == pytest.approx(
            powertrain.result.summary.final_soc, rel=1e-9
        )

    def test_resistance_changes_electrical_but_not_energy(self) -> None:
        powertrain = _powertrain()
        low_r = BatterySimulator(build_demo_variant()).simulate(powertrain)
        high_r = BatterySimulator(
            build_demo_variant(), BatteryConfig(internal_resistance_ohm=0.2)
        ).simulate(powertrain)
        assert high_r.trajectory[0].voltage_v < low_r.trajectory[0].voltage_v
        assert high_r.trajectory[0].current_a > low_r.trajectory[0].current_a
        assert high_r.summary.final_soc == pytest.approx(low_r.summary.final_soc, rel=1e-9)
        assert high_r.summary.throughput_kwh == pytest.approx(low_r.summary.throughput_kwh)

    def test_initial_soc_validation_against_powertrain_bounds(self) -> None:
        with pytest.raises(ValueError):
            BatterySimulator(build_demo_variant()).simulate(_powertrain(), initial_soc=1.5)

    def test_initial_soh_carries_degradation(self) -> None:
        # Degradation accumulates from the carried SOH, not from a fresh 1.0:
        # a 600 s reference drive drops SOH by about 5.2e-6 from where it starts.
        degraded = BatterySimulator(build_demo_variant()).simulate(
            _powertrain(), initial_soc=1.0, initial_soh=0.9
        )
        fresh = BatterySimulator(build_demo_variant()).simulate(_powertrain())
        assert degraded.summary.final_soh == pytest.approx(0.9 - (1.0 - fresh.summary.final_soh))
        assert degraded.summary.final_soh == pytest.approx(0.8999948, abs=1e-6)
        assert degraded.summary.final_soh < 0.9
