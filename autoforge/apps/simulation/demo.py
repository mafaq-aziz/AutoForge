"""Foundation demo: wires the minimal pieces into one runnable story.

Creates a company and a vehicle variant, ticks a trivial subsystem over a month,
fires one scheduled event mid-run, and returns the run record plus the events
logged. This exercises the reproducibility contract end to end: the same seed
reproduces the same log.

The vehicle variant is also the input to the EV powertrain (see the powertrain
demo and scripts/demo_powertrain.py); range and energy are validated there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from autoforge.domain.battery import BatteryPack, CellChemistry
from autoforge.domain.company import Company
from autoforge.domain.motor import Motor, MotorType
from autoforge.domain.vehicle import Drivetrain, VehicleModel, VehicleSegment, VehicleVariant
from autoforge.simulation.engine import SimulationContext, SimulationEngine, SimulationRun


class ClockReporter:
    """Example subsystem: logs a structured tick on every step.

    Demonstrates the Subsystem contract without any real domain logic; real
    subsystems (powertrain, factory, fleet) replace this in later phases.
    """

    name = "clock_reporter"

    def step(self, ctx: SimulationContext, dt_days: float) -> None:
        ctx.emit(self.name, "tick", step_days=dt_days, now_days=ctx.clock.now_days)


def build_demo_company() -> Company:
    return Company(name="AutoForge Motors", founded_year=2024, cash_eur=500_000_000)


def build_demo_variant() -> VehicleVariant:
    pack = BatteryPack(
        chemistry=CellChemistry.NMC,
        nominal_energy_kwh=77.0,
        usable_energy_kwh=75.0,
        nominal_voltage_v=400.0,
        max_voltage_v=450.0,
        min_voltage_v=300.0,
        max_charge_c_rate=2.5,
        max_discharge_c_rate=4.0,
        nominal_temperature_k=298.15,
        cells_in_series=108,
        cells_in_parallel=2,
        cycle_life_to_80_soh=1500,
        mass_kg=520.0,
    )
    motor = Motor(
        motor_type=MotorType.PMSM,
        peak_power_kw=230.0,
        continuous_power_kw=150.0,
        peak_efficiency=0.97,
        nominal_efficiency=0.92,
        mass_kg=85.0,
    )
    model = VehicleModel(
        name="Aurora",
        segment=VehicleSegment.SEDAN,
        manufacturer="AutoForge Motors",
        launch_year=2025,
    )
    return VehicleVariant(
        model=model,
        trim_name="Long Range",
        kerb_mass_kg=1900.0,
        length_m=4.9,
        width_m=1.88,
        height_m=1.45,
        frontal_area_m2=2.30,
        drag_coefficient=0.23,
        passenger_capacity=5,
        wheel_radius_m=0.36,
        battery_pack=pack,
        motor=motor,
        drivetrain=Drivetrain.RWD,
        base_price_eur=45_000.0,
        variable_cost_eur=32_000.0,
        range_target_km=550.0,
        target_0_100_kmh_s=5.9,
    )


@dataclass
class DemoResult:
    """Output of the foundation demo, easy to assert on in tests."""

    company: Company
    variant: VehicleVariant
    run: SimulationRun
    events: list[dict[str, Any]] = field(default_factory=list)


def run_demo(*, seed: int, days: float = 30.0) -> DemoResult:
    """Run the foundation demo end to end and return everything observable."""
    company = build_demo_company()
    variant = build_demo_variant()

    engine = SimulationEngine(
        seed=seed,
        step_days=1.0,
        config={"demo": "foundation", "days": days, "company": company.name},
    )
    engine.add_subsystem(ClockReporter())

    def mid_run_event() -> None:
        engine.log.record(
            {
                "time_days": engine.clock.now_days,
                "run_id": engine.run_record.run_id,
                "subsystem": "demo",
                "event": "mid_run",
                "message": f"halfway marker at day {engine.clock.now_days:.1f}",
            }
        )

    engine.schedule(days / 2.0, mid_run_event)
    run = engine.run(days=days)
    return DemoResult(company=company, variant=variant, run=run, events=list(engine.log.entries))
