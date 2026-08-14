"""CLI for the connected fleet demo.

Builds a small fleet from factory finished vehicles (or directly if none are
given), operates it day by day against the reference highway cycle, samples
battery telemetry, and prints fleet analytics plus any maintenance events.

Usage:
    python -m autoforge.scripts.demo_fleet [--seed 0] [--days 5] [--vehicles 3]
                                          [--maintenance-days 2]
"""

from __future__ import annotations

import argparse

from autoforge.apps.simulation.demo import build_demo_variant
from autoforge.data.scenarios import reference_highway_cycle
from autoforge.domain.factory import FinishedVehicle, ProductionOrder
from autoforge.services.factory.config import FactoryConfig
from autoforge.services.factory.simulator import FactorySimulator
from autoforge.services.fleet.config import FleetConfig
from autoforge.services.fleet.simulator import FleetSimulator


def _finished_vehicles(count: int) -> tuple[FinishedVehicle, ...]:
    variant = build_demo_variant()
    order = ProductionOrder(
        order_id="ORD-2026-0001", variant=variant, quantity=count, target_day=15.0
    )
    factory = FactorySimulator(FactoryConfig(), (order,)).simulate(days=12, seed=7)
    finished = factory.result.finished_vehicles
    if len(finished) < count:
        print(
            f"(only {len(finished)}/{count} vehicles finished in 12 factory days; "
            "using those available)"
        )
    return tuple(finished)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the connected fleet demo.")
    parser.add_argument("--seed", type=int, default=0, help="Seeded randomness for operations")
    parser.add_argument("--days", type=int, default=5, help="Simulated fleet days")
    parser.add_argument(
        "--vehicles", type=int, default=3, help="Vehicles built by the factory and put in the fleet"
    )
    parser.add_argument(
        "--maintenance-days", type=float, default=2.0, help="Days out of service per maintenance"
    )
    args = parser.parse_args()

    vehicles = _finished_vehicles(args.vehicles)
    config = FleetConfig(maintenance_days=args.maintenance_days)
    outcome = FleetSimulator(config, vehicles, reference_highway_cycle()).simulate(
        days=args.days, seed=args.seed
    )
    result = outcome.result
    a = result.analytics

    print(f"Fleet        : {a.fleet_size} vehicles (from factory finished vehicles)")
    scenario_name = result.operations[0].scenario_name if result.operations else "-"
    print(f"Scenario     : {scenario_name} ({a.days:g} days)")
    print("--- fleet analytics ---")
    print(f"  operating vehicle-days: {a.operating_days}")
    print(f"  availability          : {a.availability:.2f}")
    print(f"  total distance        : {a.total_distance_km:.1f} km")
    print(f"  total net energy      : {a.total_energy_kwh:.2f} kWh")
    cons = a.average_consumption_kwh_per_km
    if cons is not None:
        print(f"  avg consumption       : {cons:.4f} kWh/km")
    else:
        print("  avg consumption       : n/a")
    print(f"  telemetry points      : {a.total_telemetry_points}")
    if a.fault_counts:
        print(
            "  battery faults        : " + ", ".join(f"{k}={v}" for k, v in a.fault_counts.items())
        )
    else:
        print("  battery faults        : none")
    print(f"  maintenance events    : {a.maintenance_events}")
    print(f"  low-SOH vehicles      : {a.low_soh_vehicles}")
    if a.avg_final_soh is not None:
        print(f"  avg final SOH         : {a.avg_final_soh:.6f}")
    else:
        print("  avg final SOH         : n/a")

    if result.maintenance:
        print("--- maintenance ---")
        for m in result.maintenance:
            print(f"  {m.vin}: day {m.start_day:g}, {m.duration_days:g} days, reason={m.reason}")

    print("--- per-vehicle ---")
    for vehicle in vehicles:
        vins = [o for o in result.operations if o.vin == vehicle.vin]
        if not vins:
            print(f"  {vehicle.vin}: no operations")
            continue
        total_km = sum(o.distance_km for o in vins)
        print(
            f"  {vehicle.vin}: {len(vins)} ops, {total_km:.1f} km, "
            f"final SOC {vins[-1].final_soc:.2f}, final SOH {vins[-1].final_soh:.6f}"
        )
    print(
        f"Run          : id={outcome.run.run_id}, seed={outcome.run.seed}, "
        f"version={outcome.run.autoforge_version}, steps={outcome.run.steps}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
