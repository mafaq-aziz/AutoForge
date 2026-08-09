"""CLI for the EV powertrain demo.

Runs the demo vehicle through the reference highway cycle and prints the
summary, run metadata, and (optionally) the trajectory.

Usage:
    python -m autoforge.scripts.demo_powertrain [--seed 0] [--show-trajectory]
"""

from __future__ import annotations

import argparse

from autoforge.apps.simulation.demo import build_demo_variant
from autoforge.data.scenarios import reference_highway_cycle
from autoforge.services.vehicle.powertrain import PowertrainConfig, PowertrainSimulator


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the EV powertrain demo.")
    parser.add_argument("--seed", type=int, default=0, help="Recorded seed (no randomness used)")
    parser.add_argument(
        "--show-trajectory", action="store_true", help="Print every trajectory point"
    )
    args = parser.parse_args()

    variant = build_demo_variant()
    scenario = reference_highway_cycle()
    config = PowertrainConfig()
    simulation = PowertrainSimulator(
        variant=variant, scenario=scenario, config=config, seed=args.seed
    )
    outcome = simulation.simulate()
    summary = outcome.result.summary

    print(f"Vehicle      : {variant.model.name} {variant.trim_name}")
    print(
        f"Scenario     : {scenario.name} ({scenario.duration_s:.0f} s, "
        f"dt={scenario.timestep_s:g} s, {summary.distance_km:.2f} km)"
    )
    print(
        f"Powertrain   : motor eff {config.motor_efficiency:.0%}, drivetrain "
        f"{config.drivetrain_efficiency:.0%}, regen {config.regen_efficiency:.0%}, "
        f"aux {config.auxiliary_power_kw:.1f} kW"
    )
    print("--- summary ---")
    print(f"  energy consumed      : {summary.energy_consumed_kwh:.3f} kWh")
    print(f"  energy recovered     : {summary.energy_recovered_kwh:.3f} kWh")
    if summary.regen_discarded_kwh > 0:
        print(f"  regen discarded      : {summary.regen_discarded_kwh:.3f} kWh")
    avg = summary.average_consumption_kwh_per_km
    print(
        f"  consumption          : {avg:.3f} kWh/km"
        if avg is not None
        else "  consumption          : n/a"
    )
    range_km = summary.estimated_range_km
    print(
        f"  estimated range      : {range_km:.0f} km"
        if range_km is not None
        else "  estimated range      : n/a"
    )
    print(f"  peak battery power   : {summary.peak_power_kw:.1f} kW")
    print(f"  final SOC            : {summary.final_soc:.1%}")
    print(f"  target range         : {variant.range_target_km:.0f} km")
    if range_km is not None:
        delta = variant.range_target_km - range_km
        print(f"  vs estimated         : {range_km:.0f} km ({delta:+.0f} km vs target)")
    print(
        f"Run          : id={outcome.run.run_id}, seed={outcome.run.seed}, "
        f"version={outcome.run.autoforge_version}, steps={outcome.run.steps}"
    )

    if args.show_trajectory:
        print("--- trajectory ---")
        for p in outcome.result.trajectory:
            flags = " |LIMITED" if p.power_limited else ""
            print(
                f"  t={p.time_s:>7.1f}s v={p.speed_mps:>6.2f} m/s "
                f"wheel={p.wheel_power_kw:>8.2f} kW batt={p.battery_power_kw:>8.2f} kW "
                f"soc={p.soc:.3f}{flags}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
