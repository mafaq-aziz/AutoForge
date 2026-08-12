"""CLI for the smart factory demo.

Runs a small production order through the factory line and prints the summary:
finished vehicles, rework/scrap, bottleneck, cost, and per-station metrics.
With ``--enable-defects`` a seeded defect rate and downtime are added to show
the stochastic side (still reproducible per seed).

Usage:
    python -m autoforge.scripts.demo_factory [--seed 0] [--days 12] [--quantity 30] [--enable-defects]
"""

from __future__ import annotations

import argparse

from autoforge.apps.simulation.demo import build_demo_variant
from autoforge.domain.factory import ProductionOrder, ProductionStage
from autoforge.services.factory.config import FactoryConfig, default_line
from autoforge.services.factory.simulator import FactorySimulator


def _build_config(enable_defects: bool) -> FactoryConfig:
    if not enable_defects:
        return FactoryConfig()
    stations = list(default_line())
    for i, station in enumerate(stations):
        if station.stage == ProductionStage.PAINT:
            stations[i] = station.model_copy(update={"defect_rate": 0.05, "rework_fraction": 0.9})
        if station.stage == ProductionStage.BATTERY:
            stations[i] = station.model_copy(
                update={"downtime_probability_per_day": 0.03, "mean_downtime_days": 2.0}
            )
    return FactoryConfig(line=tuple(stations), rework_repeat_limit=1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the smart factory demo.")
    parser.add_argument(
        "--seed", type=int, default=0, help="Seeded randomness for defects/downtime"
    )
    parser.add_argument("--days", type=int, default=12, help="Simulated factory days")
    parser.add_argument("--quantity", type=int, default=30, help="Vehicles in the single order")
    parser.add_argument(
        "--enable-defects",
        action="store_true",
        help="Add seeded paint defects and battery downtime",
    )
    args = parser.parse_args()

    variant = build_demo_variant()
    config = _build_config(args.enable_defects)
    order = ProductionOrder(
        order_id="ORD-2026-0001",
        variant=variant,
        quantity=args.quantity,
        target_day=args.days + 3,
    )
    outcome = FactorySimulator(config, (order,)).simulate(days=args.days, seed=args.seed)
    s = outcome.result.summary

    print(f"Vehicle      : {variant.model.name} {variant.trim_name}")
    print(
        f"Line         : {len(config.line)} stations, bottleneck "
        f"{s.bottleneck_capacity_per_day:g}/day, cycle 1 day/station"
    )
    if args.enable_defects:
        print("Stochastic   : enabled (paint defects, battery downtime), seed-based")
    print("--- summary ---")
    print(f"  days simulated       : {s.days_simulated:g}")
    print(f"  orders released      : {s.orders_released} (items: {s.items_released})")
    print(f"  vehicles finished    : {s.vehicles_finished}")
    print(f"  scrapped / reworked  : {s.scrapped} / {s.reworks}")
    print(f"  orders completed     : {s.orders_completed} (late: {s.orders_late})")
    print(
        f"  inspections          : {s.inspection_pass} pass, "
        f"{s.inspection_rework} rework, {s.inspection_fail} fail"
    )
    print(f"  bottleneck (metrics) : {', '.join(s.bottleneck_stages)}")
    print(
        f"  throughput / day     : {s.throughput_per_day:.2f} (steady-state {s.bottleneck_capacity_per_day:g})"
    )
    print(f"  cost                 : {s.total_cost_eur:,.0f} EUR")
    if s.finished_cost_eur > 0 or s.scrap_cost_eur > 0 or s.rework_cost_eur > 0:
        print(
            f"    (finished {s.finished_cost_eur:,.0f}, scrap {s.scrap_cost_eur:,.0f}, "
            f"rework {s.rework_cost_eur:,.0f})"
        )
    print("--- station metrics ---")
    for m in outcome.result.station_metrics:
        flags = " <bottleneck>" if m.bottleneck else ""
        print(
            f"  {m.stage.value:>18}: processed {m.processed:>4}, util {m.utilization:.2f}, "
            f"down {m.downtime_days:.0f}d, wait {m.material_wait_days:.0f} item-days{flags}"
        )
    print(
        f"Run          : id={outcome.run.run_id}, seed={outcome.run.seed}, "
        f"version={outcome.run.autoforge_version}, steps={outcome.run.steps}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
