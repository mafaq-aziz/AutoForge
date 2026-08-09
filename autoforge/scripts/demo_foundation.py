"""CLI entry point for the foundation demo.

Usage:
    python -m autoforge.scripts.demo_foundation [--seed 42] [--days 30]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

from autoforge.apps.simulation.demo import run_demo

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "demo.json"


def _load_defaults() -> dict[str, Any]:
    with DEFAULT_CONFIG.open(encoding="utf-8") as fh:
        return cast(dict[str, Any], json.load(fh))


def main() -> int:
    defaults = _load_defaults()
    parser = argparse.ArgumentParser(description="Run the AutoForge foundation demo.")
    parser.add_argument("--seed", type=int, default=defaults["seed"], help="RNG seed")
    parser.add_argument("--days", type=float, default=defaults["days"], help="Simulated days")
    args = parser.parse_args()

    result = run_demo(seed=args.seed, days=args.days)
    company = result.company
    variant = result.variant

    print(
        f"Company      : {company.name} (est. {company.founded_year}), cash {company.cash_eur:,.0f} EUR"
    )
    print(
        f"Vehicle      : {variant.model.name} {variant.trim_name}, segment {variant.model.segment.value}"
    )
    print(
        f"  battery    : {variant.battery_pack.nominal_energy_kwh:.0f} kWh "
        f"{variant.battery_pack.chemistry.value.upper()} "
        f"({variant.battery_pack.total_cell_count} cells, "
        f"{variant.battery_pack.nominal_voltage_v:.0f} V)"
    )
    print(
        f"  motor      : {variant.motor.peak_power_kw:.0f} kW peak, "
        f"{variant.motor.nominal_efficiency:.0%} nominal efficiency"
    )
    print(
        f"  target     : {variant.range_target_km:.0f} km range, "
        f"{variant.base_price_eur:,.0f} EUR price"
    )
    print(f"Run          : id={result.run.run_id}, seed={result.run.seed}")
    print(f"  version    : {result.run.autoforge_version}")
    print(f"  steps      : {result.run.steps}")
    finished_at = result.run.finished_at
    assert finished_at is not None
    print(f"  wall time  : {finished_at - result.run.started_at}")
    print(f"  events     : {result.run.events_logged}")
    print("Event sample (first 5):")
    for entry in result.events[:5]:
        print(f"  t={entry['time_days']:>6.1f}d [{entry['subsystem']}] {entry['event']}")
    print(f"  ... {result.run.events_logged - 5} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
