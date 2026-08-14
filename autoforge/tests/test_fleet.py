"""Tests for the connected fleet and telemetry simulator.

The fleet consumes finished vehicles (or the factory's output directly),
operates them against a scenario day by day on the shared engine, samples
battery telemetry, and derives maintenance events from battery faults and
carried SOH. It must be deterministic per (config, vehicles, scenario, seed,
version) and contain at least one hand-calculable case.
"""

import pytest
from pydantic import ValidationError

from autoforge.apps.simulation.demo import build_demo_variant
from autoforge.data.scenarios import constant_speed_scenario, reference_highway_cycle
from autoforge.domain.battery_state import BatteryFaultCode
from autoforge.domain.factory import FinishedVehicle, ProductionOrder
from autoforge.domain.fleet import MaintenanceEvent, TelemetryPoint, VehicleOperation
from autoforge.services.factory.config import FactoryConfig
from autoforge.services.factory.simulator import FactorySimulator
from autoforge.services.fleet.config import FleetConfig
from autoforge.services.fleet.simulator import FleetSimulator

VARIANT = build_demo_variant()  # usable 75 kWh, 300..450 V, 1500 cycles to 80% SOH


def _vehicle(vin: str = "AF-ORD-0001") -> FinishedVehicle:
    return FinishedVehicle(
        vin=vin,
        item_id=f"ITEM-{vin}",
        order_id="ORD-2026-0001",
        variant=VARIANT,
        completed_at_day=1.0,
        rework_count=0,
        production_cost_eur=45_000.0,
    )


# -- domain models ---------------------------------------------------------


class TestDomainModels:
    def test_telemetry_point_valid(self) -> None:
        point = TelemetryPoint(
            vin="AF-ORD-0001",
            day=1.0,
            time_s=60.0,
            speed_mps=30.0,
            battery_power_kw=17.65,
            soc=0.96,
            soh=0.9999,
            temperature_k=298.2,
            current_a=39.9,
            voltage_v=444.0,
            odometer_km=1.8,
        )
        assert point.vin == "AF-ORD-0001"
        assert point.faults == ()

    def test_telemetry_point_rejects_soc_and_soh(self) -> None:
        kwargs = {
            "vin": "AF-ORD-0001",
            "day": 1.0,
            "time_s": 60.0,
            "speed_mps": 30.0,
            "battery_power_kw": 17.65,
            "temperature_k": 298.2,
            "current_a": 39.9,
            "voltage_v": 444.0,
            "odometer_km": 1.8,
        }
        with pytest.raises(ValidationError):
            TelemetryPoint(soc=1.5, soh=0.9999, **kwargs)
        with pytest.raises(ValidationError):
            TelemetryPoint(soc=0.5, soh=0.0, **kwargs)

    def test_vehicle_operation_valid(self) -> None:
        op = VehicleOperation(
            vin="AF-ORD-0001",
            order_id="ORD-2026-0001",
            day=1.0,
            scenario_name="reference_highway",
            distance_km=18.0,
            energy_kwh=2.9412,
            peak_power_kw=39.9,
            min_soc=0.96,
            final_soc=0.9608,
            final_soh=0.9999948,
            max_temperature_k=298.6,
            fault_codes=("under_voltage",),
            depleted=True,
        )
        assert op.fault_codes == ("under_voltage",)

    def test_maintenance_event_valid(self) -> None:
        event = MaintenanceEvent(
            vin="AF-ORD-0001", start_day=3.0, duration_days=2.0, reason="low_soh"
        )
        assert event.reason == "low_soh"


class TestFleetConfig:
    def test_default_config(self) -> None:
        config = FleetConfig()
        assert config.telemetry_interval_s == 60.0
        assert config.operation_probability == 1.0
        assert config.maintenance_soh_threshold == 0.8
        assert config.maintenance_days == 3.0

    def test_rejects_invalid_values(self) -> None:
        with pytest.raises(ValidationError):
            FleetConfig(telemetry_interval_s=0)
        with pytest.raises(ValidationError):
            FleetConfig(operation_probability=1.5)
        with pytest.raises(ValidationError):
            FleetConfig(maintenance_soh_threshold=1.0)
        with pytest.raises(ValidationError):
            FleetConfig(maintenance_days=-1)


# -- simulation behavior -----------------------------------------------------


class TestFleetSimulator:
    def test_empty_fleet_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one finished vehicle"):
            FleetSimulator(FleetConfig(), (), reference_highway_cycle()).simulate(days=3)

    def test_vehicle_operates_and_soc_carries(self) -> None:
        outcome = FleetSimulator(FleetConfig(), (_vehicle(),), reference_highway_cycle()).simulate(
            days=3
        )
        ops = outcome.result.operations
        assert [o.day for o in ops] == [1.0, 2.0, 3.0]
        assert ops[0].final_soc > ops[1].final_soc > ops[2].final_soc

    def test_soh_degrades_across_operations(self) -> None:
        outcome = FleetSimulator(FleetConfig(), (_vehicle(),), reference_highway_cycle()).simulate(
            days=3
        )
        sohs = [o.final_soh for o in outcome.result.operations]
        assert sohs[0] > sohs[1] > sohs[2]
        assert sohs[0] == pytest.approx(0.9999948, abs=1e-7)

    def test_telemetry_sample_count_and_times(self) -> None:
        outcome = FleetSimulator(FleetConfig(), (_vehicle(),), reference_highway_cycle()).simulate(
            days=1
        )
        times = [t.time_s for t in outcome.result.telemetry]
        assert times == [60.0, 120.0, 180.0, 240.0, 300.0, 360.0, 420.0, 480.0, 540.0, 600.0]

    def test_telemetry_interval_changes_sample_count(self) -> None:
        fast = FleetSimulator(
            FleetConfig(telemetry_interval_s=60.0), (_vehicle(),), reference_highway_cycle()
        ).simulate(days=1)
        slow = FleetSimulator(
            FleetConfig(telemetry_interval_s=150.0), (_vehicle(),), reference_highway_cycle()
        ).simulate(days=1)
        assert len(fast.result.telemetry) == 10
        assert len(slow.result.telemetry) == 4
        assert [t.time_s for t in slow.result.telemetry] == [150.0, 300.0, 450.0, 600.0]

    def test_telemetry_values_come_from_trajectories(self) -> None:
        outcome = FleetSimulator(FleetConfig(), (_vehicle(),), reference_highway_cycle()).simulate(
            days=1
        )
        first = outcome.result.telemetry[0]
        assert first.speed_mps == pytest.approx(30.0)
        assert first.odometer_km == pytest.approx(1.8)
        assert 0.0 < first.soc < 1.0

    def test_hand_calculable_healthy_reference(self) -> None:
        outcome = FleetSimulator(FleetConfig(), (_vehicle(),), reference_highway_cycle()).simulate(
            days=3
        )
        ops = outcome.result.operations
        assert len(ops) == 3
        for op in ops:
            assert op.distance_km == pytest.approx(18.0, abs=1e-6)
            assert op.energy_kwh == pytest.approx(2.9412, abs=1e-3)
        analytics = outcome.result.analytics
        assert analytics.total_distance_km == pytest.approx(54.0, abs=1e-6)
        assert analytics.total_energy_kwh == pytest.approx(8.8236, abs=1e-3)
        assert analytics.average_consumption_kwh_per_km == pytest.approx(0.1634, abs=1e-4)
        assert analytics.total_telemetry_points == 30
        assert analytics.availability == pytest.approx(1.0)
        assert analytics.fault_counts == {}
        assert analytics.maintenance_events == 0

    def test_battery_fault_propagates_to_operation_and_telemetry(self) -> None:
        depletion = constant_speed_scenario(duration_s=20_000.0, speed_mps=30.0)
        outcome = FleetSimulator(FleetConfig(), (_vehicle(),), depletion).simulate(days=1)
        op = outcome.result.operations[0]
        assert op.depleted
        assert "under_voltage" in op.fault_codes
        assert any(t.faults for t in outcome.result.telemetry)
        fault_vin_ops = [t for t in outcome.result.telemetry if t.faults]
        assert all(fault_vin_ops)
        assert any(BatteryFaultCode.UNDER_VOLTAGE in t.faults for t in fault_vin_ops)
        assert outcome.result.analytics.fault_counts["under_voltage"] == 1

    def test_fault_schedules_maintenance(self) -> None:
        depletion = constant_speed_scenario(duration_s=20_000.0, speed_mps=30.0)
        outcome = FleetSimulator(
            FleetConfig(maintenance_days=2.0), (_vehicle(),), depletion
        ).simulate(days=5)
        assert len(outcome.result.maintenance) == 1
        event = outcome.result.maintenance[0]
        assert event.reason == "under_voltage"
        assert event.start_day == 2.0
        assert event.duration_days == 2.0
        assert [o.day for o in outcome.result.operations] == [1.0]

    def test_low_soh_schedules_maintenance(self) -> None:
        config = FleetConfig(maintenance_soh_threshold=0.99999, maintenance_days=2.0)
        outcome = FleetSimulator(config, (_vehicle(),), reference_highway_cycle()).simulate(days=5)
        assert [o.day for o in outcome.result.operations] == [1.0, 2.0, 5.0]
        assert len(outcome.result.maintenance) == 2
        assert outcome.result.maintenance[0].reason == "low_soh"
        assert outcome.result.maintenance[0].start_day == 3.0
        assert outcome.result.analytics.availability == pytest.approx(0.6)
        assert outcome.result.analytics.low_soh_vehicles == 1

    def test_seeded_runs_are_reproducible(self) -> None:
        config = FleetConfig(operation_probability=0.5)
        vehicles = (_vehicle("AF-ORD-0001"), _vehicle("AF-ORD-0002"))
        first = FleetSimulator(config, vehicles, reference_highway_cycle()).simulate(days=4, seed=3)
        second = FleetSimulator(config, vehicles, reference_highway_cycle()).simulate(
            days=4, seed=3
        )
        assert first.result.analytics == second.result.analytics
        assert first.run.run_id == second.run.run_id

    def test_different_seed_changes_operation_days(self) -> None:
        config = FleetConfig(operation_probability=0.5)
        vehicles = (_vehicle("AF-ORD-0001"), _vehicle("AF-ORD-0002"))
        a = FleetSimulator(config, vehicles, reference_highway_cycle()).simulate(days=4, seed=3)
        b = FleetSimulator(config, vehicles, reference_highway_cycle()).simulate(days=4, seed=99)
        assert a.result.analytics.operating_days != b.result.analytics.operating_days

    def test_different_configuration_changes_results(self) -> None:
        always = FleetSimulator(FleetConfig(), (_vehicle(),), reference_highway_cycle()).simulate(
            days=3
        )
        never = FleetSimulator(
            FleetConfig(operation_probability=0.0), (_vehicle(),), reference_highway_cycle()
        ).simulate(days=3)
        assert always.result.analytics.operating_days == 3
        assert never.result.analytics.operating_days == 0
        assert never.result.analytics.availability == 0.0
        assert never.result.analytics.total_distance_km == 0.0
        assert never.result.analytics.average_consumption_kwh_per_km is None

    def test_analytics_derived_from_records(self) -> None:
        vehicles = (_vehicle("AF-ORD-0001"), _vehicle("AF-ORD-0002"))
        outcome = FleetSimulator(FleetConfig(), vehicles, reference_highway_cycle()).simulate(
            days=2
        )
        analytics = outcome.result.analytics
        ops = outcome.result.operations
        assert analytics.fleet_size == 2
        assert analytics.operating_days == len(ops)
        assert analytics.total_distance_km == sum(o.distance_km for o in ops)
        assert analytics.total_energy_kwh == sum(o.energy_kwh for o in ops)
        assert analytics.total_telemetry_points == len(outcome.result.telemetry)
        assert analytics.maintenance_events == len(outcome.result.maintenance)


# -- factory integration -------------------------------------------------------


class TestFactoryIntegration:
    def test_factory_finished_vehicles_feed_fleet(self) -> None:
        variant = build_demo_variant()
        order = ProductionOrder(
            order_id="ORD-2026-0001", variant=variant, quantity=3, target_day=15.0
        )
        factory = FactorySimulator(FactoryConfig(), (order,)).simulate(days=12, seed=7)
        assert len(factory.result.finished_vehicles) == 3

        fleet = FleetSimulator(
            FleetConfig(), factory.result.finished_vehicles, reference_highway_cycle()
        ).simulate(days=4)
        vins = {v.vin for v in factory.result.finished_vehicles}
        assert {o.vin for o in fleet.result.operations} == vins
        assert fleet.result.analytics.operating_days == 12
        assert fleet.result.analytics.total_distance_km == pytest.approx(216.0, abs=1e-6)
