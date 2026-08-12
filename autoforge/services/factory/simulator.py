"""SIMPLIFIED SMART FACTORY SIMULATOR.

A discrete-day, discrete-item flow line on the shared engine. Items move through
the configured line (RAW -> BATTERY -> BODY -> PAINT -> POWERTRAIN ->
FINAL_ASSEMBLY -> QUALITY_INSPECTION -> FINISHED), each station having a
steady-state capacity (items started per day) and a cycle time. Stations can
suffer downtime and flag defects (both seeded); defects are caught at QC and
either reworked (sent back to the station that flagged them) or scrapped.
Stations that consume parts can be starved (WAITING_FOR_MATERIAL) when
inventory runs out.

Granularity is one day: the subsystem processes a full day for each whole day
accumulated from the engine's steps. Randomness is drawn only through the
engine's SeededRng, so the same (config, orders, seed, version) reproduces the
same event log and results.

This is an educational model, not a production-planning or MES tool. Real
factories have shift calendars, setups, batching, line-balancing, and supply
chains; none of that is modeled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from autoforge.domain.factory import (
    FinishedVehicle,
    InspectionResult,
    PartCode,
    ProductionOrder,
    ProductionStage,
    ProductionStation,
    QualityInspection,
    WorkItem,
    WorkStatus,
)
from autoforge.domain.vehicle import VehicleVariant
from autoforge.services.factory.config import FactoryConfig
from autoforge.services.factory.result import (
    FactorySimulationResult,
    FactorySummary,
    StationMetrics,
)
from autoforge.simulation.engine import SimulationContext, SimulationEngine, SimulationRun


class FactorySubsystem:
    """Engine subsystem that processes one factory day per whole engine day."""

    name = "factory"

    def __init__(self, config: FactoryConfig, orders: tuple[ProductionOrder, ...]) -> None:
        self._config = config
        self._orders = tuple(sorted(orders, key=lambda o: (o.release_day, o.order_id)))
        stages = [s.stage for s in config.line]
        self._stage_index = {stage: i for i, stage in enumerate(stages)}

        self._day = 0.0
        self._release_cursor = 0
        self._item_counter = 0
        self._items: dict[str, WorkItem] = {}
        self._queues: dict[ProductionStage, list[WorkItem]] = {s: [] for s in stages}
        self._in_process: dict[ProductionStage, dict[str, WorkItem]] = {s: {} for s in stages}
        self._credits: dict[ProductionStage, float] = dict.fromkeys(stages, 0.0)
        self._down: dict[ProductionStage, bool] = dict.fromkeys(stages, False)
        self._downtime_remaining: dict[ProductionStage, float] = dict.fromkeys(stages, 0.0)
        self._stock: dict[PartCode, float] = {
            item.part: item.start_stock for item in config.inventory
        }
        self._shortage_active: dict[ProductionStage, bool] = dict.fromkeys(stages, False)

        self._processed: dict[ProductionStage, int] = dict.fromkeys(stages, 0)
        self._downtime_days: dict[ProductionStage, float] = dict.fromkeys(stages, 0.0)
        self._material_wait_days: dict[ProductionStage, float] = dict.fromkeys(stages, 0.0)

        self._order_meta: dict[str, dict[str, Any]] = {
            o.order_id: {"released": False, "produced": 0, "completed_at": None}
            for o in self._orders
        }
        self._inspections: list[QualityInspection] = []
        self._finished: list[FinishedVehicle] = []
        self._scrapped: list[WorkItem] = []
        self._rework_events = 0
        self._rework_cost = 0.0
        self._scrap_cost = 0.0
        self._ctx: SimulationContext | None = None

    # -- engine protocol --------------------------------------------------

    def step(self, ctx: SimulationContext, dt_days: float) -> None:
        self._ctx = ctx
        self._release_due_orders(ctx)
        self._day += dt_days
        while self._day >= 1.0:
            self._process_day(ctx)
            self._day -= 1.0

    # -- release ----------------------------------------------------------

    def _release_due_orders(self, ctx: SimulationContext) -> None:
        now = ctx.clock.now_days
        while self._release_cursor < len(self._orders):
            order = self._orders[self._release_cursor]
            if order.release_day > now:
                break
            self._release_order(order, now, ctx)
            self._release_cursor += 1

    def _release_order(self, order: ProductionOrder, now: float, ctx: SimulationContext) -> None:
        self._order_meta[order.order_id]["released"] = True
        ctx.emit(
            self.name,
            "order_released",
            order_id=order.order_id,
            quantity=order.quantity,
            variant=f"{order.variant.model.name} {order.variant.trim_name}",
            release_day=order.release_day,
        )
        for sequence in range(1, order.quantity + 1):
            self._item_counter += 1
            item = WorkItem(
                item_id=f"{order.order_id}-{sequence:03d}",
                order_id=order.order_id,
                sequence=sequence,
                stage=ProductionStage.RAW,
                status=WorkStatus.QUEUED,
                entered_at_day=now,
            )
            self._items[item.item_id] = item
            self._queues[ProductionStage.RAW].append(item)

    # -- daily processing -------------------------------------------------

    def _process_day(self, ctx: SimulationContext) -> None:
        self._restock()
        for station in self._config.line:
            self._process_station(station, ctx)

    def _restock(self) -> None:
        for item in self._config.inventory:
            if item.replenish_per_day > 0.0:
                self._stock[item.part] = self._stock.get(item.part, 0.0) + item.replenish_per_day

    def _process_station(self, station: ProductionStation, ctx: SimulationContext) -> None:
        stage = station.stage
        if self._down[stage]:
            if self._downtime_remaining[stage] > 0.0:
                self._downtime_remaining[stage] -= 1.0
                self._downtime_days[stage] += 1.0
                return
            self._down[stage] = False
        elif station.downtime_probability_per_day > 0.0:
            if ctx.rng.random() < station.downtime_probability_per_day:
                self._down[stage] = True
                self._downtime_remaining[stage] = station.mean_downtime_days - 1.0
                self._downtime_days[stage] += 1.0
                ctx.emit(
                    self.name,
                    "station_downtime",
                    stage=stage.value,
                    days=station.mean_downtime_days,
                )
                return

        completed: list[WorkItem] = []
        for item_id, item in list(self._in_process[stage].items()):
            item = item.model_copy(
                update={"station_remaining_days": item.station_remaining_days - 1.0}
            )
            self._in_process[stage][item_id] = item
            if item.station_remaining_days <= 0.0:
                del self._in_process[stage][item_id]
                completed.append(item)

        for item in completed:
            self._complete_item(station, item, ctx)
        self._start_items(station, ctx)

    def _complete_item(
        self, station: ProductionStation, item: WorkItem, ctx: SimulationContext
    ) -> None:
        self._processed[station.stage] += 1
        if (
            station.defect_rate > 0.0
            and item.defect_code is None
            and ctx.rng.random() < station.defect_rate
        ):
            item = item.model_copy(
                update={"defect_stage": station.stage, "defect_code": station.defect_code}
            )
            self._items[item.item_id] = item

        if station.stage == ProductionStage.QUALITY_INSPECTION:
            self._inspect(item, ctx)
        else:
            next_stage = self._config.line[self._stage_index[station.stage] + 1].stage
            item = item.model_copy(update={"stage": next_stage, "status": WorkStatus.QUEUED})
            self._items[item.item_id] = item
            self._queues[next_stage].append(item)

    def _inspect(self, item: WorkItem, ctx: SimulationContext) -> None:
        inspection_id = f"qc-{item.item_id}"
        defect_code = item.defect_code
        if defect_code is None:
            order = self._order_meta[item.order_id]
            cost = item.production_cost_eur + self._order_variant(item.order_id).variable_cost_eur
            vin = f"AF-{item.order_id}-{item.sequence:04d}"
            vehicle = FinishedVehicle(
                vin=vin,
                item_id=item.item_id,
                order_id=item.order_id,
                variant=self._order_variant(item.order_id),
                completed_at_day=ctx.clock.now_days,
                rework_count=item.rework_count,
                production_cost_eur=cost,
            )
            self._finished.append(vehicle)
            self._inspections.append(
                QualityInspection(
                    inspection_id=inspection_id,
                    item_id=item.item_id,
                    order_id=item.order_id,
                    inspected_at_day=ctx.clock.now_days,
                    result=InspectionResult.PASS,
                )
            )
            item = item.model_copy(
                update={
                    "status": WorkStatus.COMPLETE,
                    "stage": ProductionStage.FINISHED,
                    "completed_at_day": ctx.clock.now_days,
                    "production_cost_eur": cost,
                }
            )
            self._items[item.item_id] = item
            order["produced"] += 1
            if order["produced"] == self._order_quantity(item.order_id):
                order["completed_at"] = ctx.clock.now_days
            ctx.emit(
                self.name,
                "vehicle_finished",
                vin=vin,
                order_id=item.order_id,
                completed_at_day=ctx.clock.now_days,
                cost_eur=cost,
            )
            return

        assert item.defect_stage is not None
        defect_station = self._station_by_stage(item.defect_stage)
        assert defect_station is not None
        rework = (
            item.rework_count < self._config.rework_repeat_limit
            and ctx.rng.random() < defect_station.rework_fraction
        )
        if rework:
            item = item.model_copy(
                update={
                    "status": WorkStatus.REWORK,
                    "stage": item.defect_stage,
                    "defect_stage": None,
                    "defect_code": None,
                    "rework_count": item.rework_count + 1,
                    "production_cost_eur": item.production_cost_eur + self._config.rework_cost_eur,
                }
            )
            self._items[item.item_id] = item
            self._queues[item.stage].append(item)
            self._rework_events += 1
            self._rework_cost += self._config.rework_cost_eur
            self._inspections.append(
                QualityInspection(
                    inspection_id=inspection_id,
                    item_id=item.item_id,
                    order_id=item.order_id,
                    inspected_at_day=ctx.clock.now_days,
                    result=InspectionResult.REWORK,
                    defect_code=defect_code,
                )
            )
            ctx.emit(
                self.name,
                "item_reworked",
                item_id=item.item_id,
                stage=item.stage.value,
                rework_count=item.rework_count,
            )
            return

        self._scrapped.append(item)
        item = item.model_copy(update={"status": WorkStatus.SCRAPPED})
        self._items[item.item_id] = item
        self._scrap_cost += self._config.scrap_cost_eur
        self._inspections.append(
            QualityInspection(
                inspection_id=inspection_id,
                item_id=item.item_id,
                order_id=item.order_id,
                inspected_at_day=ctx.clock.now_days,
                result=InspectionResult.FAIL,
                defect_code=defect_code,
            )
        )
        ctx.emit(
            self.name,
            "item_scrapped",
            item_id=item.item_id,
            stage=item.stage.value,
            defect_code=defect_code.value,
        )

    def _start_items(self, station: ProductionStation, ctx: SimulationContext) -> None:
        stage = station.stage
        queue = self._queues[stage]
        if not queue:
            self._clear_shortage(stage)
            return
        max_active = int(math.ceil(station.capacity_per_day * station.cycle_time_days))
        credit = self._credits[stage] + station.capacity_per_day
        while queue and len(self._in_process[stage]) < max_active and credit >= 1.0:
            item = queue[0]
            missing = [p for p in station.consumes if self._stock.get(p, 0.0) < 1.0]
            if missing:
                updated: list[WorkItem] = []
                for queued in queue:
                    if queued.status != WorkStatus.WAITING_FOR_MATERIAL:
                        queued = queued.model_copy(
                            update={"status": WorkStatus.WAITING_FOR_MATERIAL}
                        )
                        self._items[queued.item_id] = queued
                    updated.append(queued)
                queue[:] = updated
                self._material_wait_days[stage] += len(queue)
                if not self._shortage_active[stage]:
                    self._shortage_active[stage] = True
                    ctx.emit(
                        self.name,
                        "material_shortage",
                        stage=stage.value,
                        parts=[p.value for p in missing],
                    )
                break
            self._clear_shortage(stage)
            for part in station.consumes:
                self._stock[part] -= 1.0
            credit -= 1.0
            queue.pop(0)
            started = item.model_copy(
                update={
                    "status": WorkStatus.IN_PROCESS,
                    "stage": stage,
                    "station_remaining_days": station.cycle_time_days,
                }
            )
            self._items[started.item_id] = started
            self._in_process[stage][started.item_id] = started
            ctx.emit(self.name, "item_started", item_id=started.item_id, stage=stage.value)
        self._credits[stage] = credit
        if not queue:
            self._clear_shortage(stage)

    def _clear_shortage(self, stage: ProductionStage) -> None:
        if self._shortage_active[stage]:
            self._shortage_active[stage] = False
            if self._ctx is not None:
                self._ctx.emit(self.name, "material_resolved", stage=stage.value)

    # -- helpers ----------------------------------------------------------

    def _station_by_stage(self, stage: ProductionStage) -> ProductionStation | None:
        index = self._stage_index.get(stage)
        if index is None:
            return None
        return self._config.line[index]

    def _order_variant(self, order_id: str) -> VehicleVariant:
        for order in self._orders:
            if order.order_id == order_id:
                return order.variant
        raise KeyError(order_id)

    def _order_quantity(self, order_id: str) -> int:
        for order in self._orders:
            if order.order_id == order_id:
                return order.quantity
        raise KeyError(order_id)

    # -- results ----------------------------------------------------------

    def build_result(self) -> FactorySimulationResult:
        days = self._ctx.clock.now_days if self._ctx is not None else 0.0
        finished_cost = sum(v.production_cost_eur for v in self._finished)
        orders_completed = sum(
            1 for m in self._order_meta.values() if m["completed_at"] is not None
        )
        orders_late = 0
        for order in self._orders:
            meta = self._order_meta[order.order_id]
            if not meta["released"]:
                continue
            completed_at = meta["completed_at"]
            if (
                completed_at is not None
                and order.target_day is not None
                and completed_at > order.target_day
                or completed_at is None
                and order.target_day is not None
                and order.target_day < days
            ):
                orders_late += 1

        utilizations: dict[ProductionStage, float] = {}
        for station in self._config.line:
            denom = station.capacity_per_day * days
            utilizations[station.stage] = (
                self._processed[station.stage] / denom if denom > 0 else 0.0
            )
        max_util = max(utilizations.values()) if utilizations else 0.0
        bottleneck = tuple(
            s.stage.value
            for s in self._config.line
            if utilizations.get(s.stage, 0.0) >= max_util - 1e-9 and max_util > 0.0
        )

        wip_remaining = sum(len(q) for q in self._queues.values()) + sum(
            len(slot) for slot in self._in_process.values()
        )
        waiting = sum(
            1 for item in self._items.values() if item.status == WorkStatus.WAITING_FOR_MATERIAL
        )
        counts = dict.fromkeys(InspectionResult, 0)
        for inspection in self._inspections:
            counts[inspection.result] += 1

        summary = FactorySummary(
            source_run_id=self._ctx.run_id if self._ctx is not None else "",
            days_simulated=days,
            orders_released=sum(1 for m in self._order_meta.values() if m["released"]),
            items_released=self._item_counter,
            vehicles_finished=len(self._finished),
            scrapped=len(self._scrapped),
            reworks=self._rework_events,
            finished_cost_eur=finished_cost,
            rework_cost_eur=self._rework_cost,
            scrap_cost_eur=self._scrap_cost,
            total_cost_eur=finished_cost + self._scrap_cost,
            bottleneck_stages=bottleneck,
            bottleneck_capacity_per_day=self._config.bottleneck_capacity_per_day,
            throughput_per_day=len(self._finished) / days if days > 0 else 0.0,
            wip_remaining=wip_remaining,
            waiting_for_material=waiting,
            orders_completed=orders_completed,
            orders_late=orders_late,
            inspection_pass=counts[InspectionResult.PASS],
            inspection_rework=counts[InspectionResult.REWORK],
            inspection_fail=counts[InspectionResult.FAIL],
        )
        station_metrics = tuple(
            StationMetrics(
                stage=s.stage,
                processed=self._processed[s.stage],
                downtime_days=self._downtime_days[s.stage],
                material_wait_days=self._material_wait_days[s.stage],
                utilization=utilizations[s.stage],
                bottleneck=s.stage.value in bottleneck,
            )
            for s in self._config.line
        )
        return FactorySimulationResult(
            summary=summary,
            finished_vehicles=tuple(self._finished),
            inspections=tuple(self._inspections),
            station_metrics=station_metrics,
        )


@dataclass(frozen=True)
class FactorySimulation:
    """A completed factory run: engine record plus typed result."""

    run: SimulationRun
    result: FactorySimulationResult
    events: tuple[dict[str, object], ...] = ()


class FactorySimulator:
    """Runs a factory configuration through a set of orders on the shared engine.

    Deterministic per (config, orders, seed, version): all randomness flows
    through the engine's seeded RNG in a fixed draw order.
    """

    def __init__(
        self,
        config: FactoryConfig,
        orders: tuple[ProductionOrder, ...],
    ) -> None:
        self._config = config
        self._orders = orders

    def simulate(self, *, days: float, seed: int = 0) -> FactorySimulation:
        if days <= 0:
            raise ValueError(f"run horizon must be positive, got {days!r}")
        engine = SimulationEngine(
            seed=seed,
            step_days=1.0,
            config={
                "system": "factory",
                "line": [
                    {
                        "stage": s.stage.value,
                        "capacity_per_day": s.capacity_per_day,
                        "cycle_time_days": s.cycle_time_days,
                        "defect_rate": s.defect_rate,
                        "downtime_probability_per_day": s.downtime_probability_per_day,
                        "mean_downtime_days": s.mean_downtime_days,
                        "rework_fraction": s.rework_fraction,
                    }
                    for s in self._config.line
                ],
                "inventory": [
                    {
                        "part": i.part.value,
                        "start_stock": i.start_stock,
                        "replenish_per_day": i.replenish_per_day,
                    }
                    for i in self._config.inventory
                ],
                "rework_repeat_limit": self._config.rework_repeat_limit,
                "orders": [
                    {
                        "order_id": o.order_id,
                        "quantity": o.quantity,
                        "release_day": o.release_day,
                        "target_day": o.target_day,
                    }
                    for o in self._orders
                ],
                "horizon_days": days,
            },
        )
        subsystem = FactorySubsystem(self._config, self._orders)
        engine.add_subsystem(subsystem)
        run = engine.run(days=days)
        result = subsystem.build_result()
        run.result = result.summary.model_dump()
        return FactorySimulation(run=run, result=result, events=tuple(engine.log.entries))
