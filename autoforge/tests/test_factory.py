"""Tests for the smart factory simulator.

Reference case is hand-calculable: with integer capacities and 1-day cycle
times, steady-state throughput equals the bottleneck station's capacity and an
item spends one day per station (7 stations -> first finish on day 8 when the
order is released on the first engine day). See docs/factory.md.
"""

import pytest

from autoforge.apps.simulation.demo import build_demo_variant
from autoforge.domain.factory import (
    InventoryItem,
    PartCode,
    ProductionOrder,
    ProductionStage,
    ProductionStation,
)
from autoforge.services.factory.config import FactoryConfig, default_line
from autoforge.services.factory.result import FactorySimulationResult
from autoforge.services.factory.simulator import FactorySimulator

VARIANT = build_demo_variant()


def _order(quantity: int = 30, order_id: str = "ORD1", **kwargs: float | None) -> ProductionOrder:
    return ProductionOrder(order_id=order_id, variant=VARIANT, quantity=quantity, **kwargs)


def _line(**stage_overrides: dict[str, object]) -> tuple[ProductionStation, ...]:
    """default_line with per-stage keyword overrides keyed by stage name."""
    stations: list[ProductionStation] = []
    for station in default_line():
        overrides = stage_overrides.get(station.stage.value, {})
        stations.append(station.model_copy(update=overrides))
    return tuple(stations)


def _run(
    config: FactoryConfig,
    orders: tuple[ProductionOrder, ...],
    days: float,
    seed: int = 0,
) -> FactorySimulationResult:
    return FactorySimulator(config, orders).simulate(days=days, seed=seed).result


class TestConfigValidation:
    def test_default_bottleneck_is_ten_per_day(self) -> None:
        assert FactoryConfig().bottleneck_capacity_per_day == pytest.approx(10.0)

    def test_line_must_start_raw_and_end_qc(self) -> None:
        line = list(default_line())
        with pytest.raises(ValueError):
            FactoryConfig(line=tuple(line[1:]))
        with pytest.raises(ValueError):
            FactoryConfig(line=tuple(line[:-1]))

    def test_duplicate_stage_rejected(self) -> None:
        line = list(default_line())
        line.append(line[1])
        with pytest.raises(ValueError):
            FactoryConfig(line=tuple(line))

    def test_stage_order_must_be_canonical(self) -> None:
        line = list(default_line())
        line[1], line[2] = line[2], line[1]
        with pytest.raises(ValueError):
            FactoryConfig(line=tuple(line))

    def test_defect_rate_requires_defect_code(self) -> None:
        with pytest.raises(ValueError):
            ProductionStation(stage=ProductionStage.BODY, capacity_per_day=10.0, defect_rate=0.05)

    def test_downtime_requires_duration(self) -> None:
        with pytest.raises(ValueError):
            ProductionStation(
                stage=ProductionStage.BODY,
                capacity_per_day=10.0,
                downtime_probability_per_day=0.5,
                mean_downtime_days=0.5,
            )


class TestReferenceScenario:
    """Hand-calculable steady-state throughput and pipeline latency."""

    def test_thirty_units_in_ten_days(self) -> None:
        result = _run(FactoryConfig(), (_order(30),), days=10)
        s = result.summary
        assert s.vehicles_finished == 30
        assert s.scrapped == 0 and s.reworks == 0
        assert s.orders_completed == 1
        assert s.orders_late == 0
        assert s.inspection_pass == 30
        assert len(result.finished_vehicles) == 30
        days_finished = [v.completed_at_day for v in result.finished_vehicles]
        assert min(days_finished) == 8.0  # 7 stations * 1 day after the release day
        assert max(days_finished) == 10.0  # 30 units at the 10/day bottleneck
        # cost: 30 units * 32,000 EUR variable cost
        assert s.total_cost_eur == pytest.approx(30 * VARIANT.variable_cost_eur)
        assert s.finished_cost_eur == pytest.approx(960_000.0)
        assert s.wip_remaining == 0

    def test_bottleneck_from_metrics(self) -> None:
        s = _run(FactoryConfig(), (_order(30),), days=10).summary
        assert set(s.bottleneck_stages) == {
            "battery",
            "paint",
            "final_assembly",
        }
        assert s.bottleneck_capacity_per_day == pytest.approx(10.0)

    def test_steady_state_throughput_equals_bottleneck_capacity(self) -> None:
        # 200 units, 27 days: 10/day from day 8 to day 27 inclusive = 200.
        result = _run(FactoryConfig(), (_order(200, target_day=15.0),), days=27)
        assert result.summary.vehicles_finished == 200
        assert result.summary.orders_completed == 1
        assert result.summary.orders_late == 1  # finished day 27 after target day 15
        days_finished = [v.completed_at_day for v in result.finished_vehicles]
        assert max(days_finished) == 27.0
        throughput = 200 / (27.0 - 8.0 + 1.0)
        assert throughput == pytest.approx(result.summary.bottleneck_capacity_per_day)

    def test_pipeline_latency_is_one_day_per_station(self) -> None:
        result = _run(FactoryConfig(), (_order(10),), days=8)
        # 10 units (below bottleneck) all finish on day 8 (7 stations).
        assert result.summary.vehicles_finished == 10
        assert {v.completed_at_day for v in result.finished_vehicles} == {8.0}

    def test_utilization_orders(self) -> None:
        metrics = _run(FactoryConfig(), (_order(30),), days=10).station_metrics
        by_stage = {m.stage.value: m for m in metrics}
        bottleneck_util = by_stage["battery"].utilization
        assert by_stage["battery"].utilization == pytest.approx(by_stage["paint"].utilization)
        assert by_stage["paint"].utilization == pytest.approx(
            by_stage["final_assembly"].utilization
        )
        assert bottleneck_util > by_stage["body"].utilization
        assert bottleneck_util > by_stage["powertrain"].utilization
        assert bottleneck_util > by_stage["quality_inspection"].utilization


class TestDeterminism:
    def test_same_seed_reproduces_identical_results(self) -> None:
        config = FactoryConfig(
            line=_line(paint={"defect_rate": 0.1, "rework_fraction": 1.0}),
            rework_repeat_limit=1,
        )
        a = _run(config, (_order(20),), days=30, seed=3)
        b = _run(config, (_order(20),), days=30, seed=3)
        assert a == b
        assert a.summary == b.summary


class TestDefectsAndRework:
    """Seed-locked deterministic outcomes for the defect/rework rules."""

    def test_defects_reworked_unlimited_never_scrapped(self) -> None:
        config = FactoryConfig(
            line=_line(paint={"defect_rate": 0.1, "rework_fraction": 1.0}),
            rework_repeat_limit=100,
        )
        s = _run(config, (_order(20),), days=30, seed=1).summary
        assert s.vehicles_finished == 20
        assert s.scrapped == 0
        assert s.reworks == 5
        assert s.inspection_pass == 20
        assert s.inspection_rework == 5
        assert s.inspection_fail == 0

    def test_rework_repeat_limit_scraps_second_defect(self) -> None:
        config = FactoryConfig(
            line=_line(paint={"defect_rate": 0.1, "rework_fraction": 1.0}),
            rework_repeat_limit=1,
        )
        s = _run(config, (_order(20),), days=30, seed=1).summary
        assert s.vehicles_finished == 19
        assert s.scrapped == 1
        assert s.reworks == 4

    def test_rework_fraction_zero_scraps_all_defects(self) -> None:
        config = FactoryConfig(
            line=_line(paint={"defect_rate": 0.1, "rework_fraction": 0.0}),
            rework_repeat_limit=100,
        )
        s = _run(config, (_order(20),), days=30, seed=1).summary
        assert s.vehicles_finished == 16
        assert s.scrapped == 4
        assert s.reworks == 0
        assert s.inspection_fail == 4

    def test_invariants_finished_plus_scrapped_equals_released(self) -> None:
        config = FactoryConfig(
            line=_line(paint={"defect_rate": 0.1, "rework_fraction": 1.0}),
            rework_repeat_limit=2,
        )
        s = _run(config, (_order(40),), days=40, seed=7).summary
        assert s.vehicles_finished + s.scrapped == s.items_released
        assert s.inspection_pass == s.vehicles_finished
        assert s.inspection_fail == s.scrapped
        # Every reworked item that finished carries the extra rework cost.
        for v in _run(config, (_order(40),), days=40, seed=7).finished_vehicles:
            assert v.production_cost_eur == pytest.approx(
                VARIANT.variable_cost_eur + config.rework_cost_eur * v.rework_count
            )


class TestInventory:
    def test_shortage_starves_station(self) -> None:
        stock = {i.part: i for i in FactoryConfig().inventory}
        stock[PartCode.BATTERY_PACK] = InventoryItem(part=PartCode.BATTERY_PACK, start_stock=10.0)
        config = FactoryConfig(inventory=tuple(stock.values()))
        s = _run(config, (_order(30),), days=20).summary
        assert s.vehicles_finished == 10  # only the 10 batteries on hand
        assert s.waiting_for_material == 20
        assert s.wip_remaining == 20

    def test_replenishment_keeps_line_supplied(self) -> None:
        stock = {i.part: i for i in FactoryConfig().inventory}
        stock[PartCode.BATTERY_PACK] = InventoryItem(
            part=PartCode.BATTERY_PACK, start_stock=10.0, replenish_per_day=10.0
        )
        config = FactoryConfig(inventory=tuple(stock.values()))
        s = _run(config, (_order(30),), days=20).summary
        assert s.vehicles_finished == 30
        assert s.waiting_for_material == 0


class TestDowntime:
    def test_downtime_halves_effective_capacity(self) -> None:
        # Battery breaks down with probability 1 for 1 day, so it runs every
        # other day: 10 downtime days and 10 working days over a 20-day run.
        config = FactoryConfig(
            line=_line(
                battery={
                    "downtime_probability_per_day": 1.0,
                    "mean_downtime_days": 1.0,
                }
            )
        )
        result = _run(config, (_order(200),), days=20)
        s = result.summary
        battery = next(m for m in result.station_metrics if m.stage == ProductionStage.BATTERY)
        assert battery.downtime_days == pytest.approx(10.0)
        # 100 starts over the 10 working days; the last 10 are still in flight.
        assert battery.processed == 90
        # Downtime shifts the bottleneck onto the degraded station.
        assert s.bottleneck_stages == ("battery",)


class TestOrdersAndLateness:
    def test_release_day_defers_entry(self) -> None:
        result = _run(FactoryConfig(), (_order(30, release_day=5.0),), days=10)
        s = result.summary
        assert s.orders_released == 1
        # Released on day 5 -> first finish on day 12, outside the horizon.
        assert s.vehicles_finished == 0
        assert s.wip_remaining > 0

    def test_missed_target_marks_order_late(self) -> None:
        config = FactoryConfig()
        s = _run(config, (_order(30, target_day=8.0),), days=10).summary
        assert s.orders_completed == 1
        assert s.orders_late == 1  # finished day 10 > target 8

    def test_target_met_is_not_late(self) -> None:
        s = _run(FactoryConfig(), (_order(30, target_day=15.0),), days=10).summary
        assert s.orders_completed == 1
        assert s.orders_late == 0


class TestEngineIntegration:
    def test_run_metadata(self) -> None:
        outcome = FactorySimulator(FactoryConfig(), (_order(30),)).simulate(days=10, seed=5)
        run = outcome.run
        assert run.steps == 10
        assert run.config["system"] == "factory"
        assert run.seed == 5
        assert run.result["vehicles_finished"] == 30

    def test_structured_events_emitted(self) -> None:
        outcome = FactorySimulator(FactoryConfig(), (_order(10),)).simulate(days=8, seed=0)
        events = outcome.events
        kinds = {e["event"] for e in events}
        assert "order_released" in kinds
        assert "item_started" in kinds
        assert "vehicle_finished" in kinds
        finished = [e for e in events if e["event"] == "vehicle_finished"]
        assert len(finished) == 10

    def test_finished_vehicles_have_unique_vins(self) -> None:
        result = _run(FactoryConfig(), (_order(30),), days=10)
        vins = [v.vin for v in result.finished_vehicles]
        assert len(vins) == len(set(vins))


class TestWipRemaining:
    def test_short_horizon_leaves_wip(self) -> None:
        s = _run(FactoryConfig(), (_order(200),), days=5).summary
        assert s.vehicles_finished == 0
        assert s.wip_remaining > 0
        assert s.items_released == 200
