"""CLI for the battery/BMS demo.

Runs the demo vehicle through the reference highway cycle with the powertrain,
then adds the BMS-style battery view (current, voltage, temperature, SOH,
degradation, faults) over the powertrain trace.

Usage:
    python -m autoforge.scripts.demo_battery [--seed 0] [--show-trajectory]
"""

from __future__ import annotations

import argparse

from autoforge.apps.simulation.demo import build_demo_variant
from autoforge.data.scenarios import reference_highway_cycle
from autoforge.services.battery.config import BatteryConfig
from autoforge.services.battery.model import BatterySimulator
from autoforge.services.vehicle.powertrain import PowertrainSimulator


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the battery/BMS demo.")
    parser.add_argument("--seed", type=int, default=0, help="Recorded seed (no randomness used)")
    parser.add_argument(
        "--show-trajectory", action="store_true", help="Print every battery trajectory point"
    )
    args = parser.parse_args()

    variant = build_demo_variant()
    config = BatteryConfig()
    powertrain = PowertrainSimulator(
        variant=variant, scenario=reference_highway_cycle(), seed=args.seed
    ).simulate()
    result = BatterySimulator(variant, config).simulate(powertrain)
    summary = result.summary
    pt_summary = powertrain.result.summary

    print(f"Vehicle      : {variant.model.name} {variant.trim_name}")
    print(
        f"Scenario     : reference highway cycle "
        f"({pt_summary.distance_km:.2f} km, {pt_summary.duration_s:.0f} s)"
    )
    print(
        f"Battery config: R={config.internal_resistance_ohm:g} ohm, "
        f"C_th={config.thermal_capacity_j_per_k:g} J/K, "
        f"k_cool={config.cooling_coefficient_w_per_k:g} W/K, "
        f"T_amb={config.ambient_temperature_k:g} K"
    )
    print("--- summary ---")
    print(f"  final SOC            : {summary.final_soc:.3f}")
    print(f"  final SOH            : {summary.final_soh:.4f}")
    print(
        f"  temperature          : {summary.initial_temperature_k:.2f} K -> {summary.final_temperature_k:.2f} K"
    )
    print(
        f"  temperature range    : {summary.min_temperature_k:.2f} .. {summary.max_temperature_k:.2f} K"
    )
    print(f"  max |current|        : {summary.max_absolute_current_a:.1f} A")
    print(f"  throughput           : {summary.throughput_kwh:.3f} kWh")
    print(f"  equivalent full cycles: {summary.equivalent_full_cycles:.4f}")
    print(f"  max soc error vs PT  : {summary.max_soc_error:.2e}")
    if summary.fault_counts:
        print(
            "  faults               : "
            + ", ".join(f"{k}={v}" for k, v in summary.fault_counts.items())
        )
    else:
        print("  faults               : none")
    print(
        f"Run          : id={powertrain.run.run_id}, seed={powertrain.run.seed}, "
        f"version={powertrain.run.autoforge_version}, steps={powertrain.run.steps}"
    )

    if args.show_trajectory:
        print("--- battery trajectory ---")
        for p in result.trajectory:
            flags = ""
            if p.power_limited:
                flags += " |LIMITED"
            if p.faults:
                flags += " |" + ",".join(f.value for f in p.faults)
            print(
                f"  t={p.time_s:>7.1f}s I={p.current_a:>8.1f} A V={p.voltage_v:>7.2f} V "
                f"T={p.temperature_k:>7.3f} K soc={p.soc:.3f} soh={p.soh:.4f}{flags}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
