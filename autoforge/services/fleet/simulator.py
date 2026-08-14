"""SIMPLIFIED CONNECTED FLEET SIMULATOR.

Consumes factory ``FinishedVehicle`` records and operates them day by day
against a scenario on the shared engine. Each operating vehicle runs the
existing powertrain and battery simulators (nested and deterministic); battery
telemetry is sampled at a configurable interval; and battery faults / carried
SOH feed simple maintenance rules that take a vehicle out of service.

Fleet-level randomness (which vehicles operate on which days) flows through the
engine's seeded RNG, so the same (config, vehicles, scenario, seed, version)
reproduces the same operations, telemetry, maintenance events, and analytics.

SIMPLIFIED: no charging is modeled (SOC carries over and can deplete), every
vehicle drives the same scenario, and there is no driver model, route planning,
or live uplink. Telemetry is a recorded sample of the local simulation, not a
real data pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autoforge.domain.factory import FinishedVehicle
from autoforge.domain.fleet import MaintenanceEvent, TelemetryPoint, VehicleOperation
from autoforge.domain.scenario import DrivingScenario
from autoforge.services.battery.config import BatteryConfig
from autoforge.services.battery.model import BatterySimulator
from autoforge.services.fleet.config import FleetConfig
from autoforge.services.fleet.result import FleetAnalytics, FleetSimulationResult
from autoforge.services.vehicle.powertrain import PowertrainSimulator
from autoforge.simulation.engine import SimulationContext, SimulationEngine, SimulationRun


class _VehicleState:
    """Mutable per-vehicle runtime state (SOC, SOH, odometer, service)."""

    __slots__ = (
        "soc",
        "soh",
        "odometer_km",
        "maintenance_remaining_days",
        "operated_days",
        "low_soh",
    )

    def __init__(self) -> None:
        self.soc = 1.0
        self.soh = 1.0
        self.odometer_km = 0.0
        self.maintenance_remaining_days = 0.0
        self.operated_days = 0
        self.low_soh = False


class FleetSubsystem:
    """Engine subsystem that operates one fleet day per whole engine day."""

    name = "fleet"

    def __init__(
        self,
        config: FleetConfig,
        vehicles: tuple[FinishedVehicle, ...],
        scenario: DrivingScenario,
        battery_config: BatteryConfig | None = None,
    ) -> None:
        self._config = config
        self._vehicles = tuple(sorted(vehicles, key=lambda v: v.vin))
        self._scenario = scenario
        self._battery_config = battery_config if battery_config is not None else BatteryConfig()

        self._day = 0.0
        self._states: dict[str, _VehicleState] = {v.vin: _VehicleState() for v in self._vehicles}
        self._operations: list[VehicleOperation] = []
        self._telemetry: list[TelemetryPoint] = []
        self._maintenance: list[MaintenanceEvent] = []

    # -- engine protocol --------------------------------------------------

    def step(self, ctx: SimulationContext, dt_days: float) -> None:
        self._day += dt_days
        while self._day >= 1.0:
            self._process_day(ctx)
            self._day -= 1.0

    # -- daily processing -------------------------------------------------

    def _process_day(self, ctx: SimulationContext) -> None:
        day = ctx.clock.now_days
        for vehicle in self._vehicles:
            state = self._states[vehicle.vin]
            if state.maintenance_remaining_days > 0.0:
                state.maintenance_remaining_days -= 1.0
                ctx.emit(
                    self.name,
                    "vehicle_in_service",
                    vin=vehicle.vin,
                    day=day,
                    remaining_days=state.maintenance_remaining_days,
                )
                continue
            if state.soc <= 1e-6:
                ctx.emit(
                    self.name, "vehicle_out_of_charge", vin=vehicle.vin, day=day, soc=state.soc
                )
                continue
            if ctx.rng.random() >= self._config.operation_probability:
                ctx.emit(self.name, "vehicle_idle", vin=vehicle.vin, day=day)
                continue
            self._operate(vehicle, state, day, ctx)

    def _operate(
        self,
        vehicle: FinishedVehicle,
        state: _VehicleState,
        day: float,
        ctx: SimulationContext,
    ) -> None:
        powertrain = PowertrainSimulator(
            variant=vehicle.variant, scenario=self._scenario, seed=0
        ).simulate(initial_soc=state.soc)
        battery = BatterySimulator(vehicle.variant, self._battery_config).simulate(
            powertrain, initial_soc=state.soc, initial_soh=state.soh
        )
        pt_summary = powertrain.result.summary
        bat_summary = battery.summary

        faults = tuple(bat_summary.fault_counts.keys())
        operation = VehicleOperation(
            vin=vehicle.vin,
            order_id=vehicle.order_id,
            day=day,
            scenario_name=self._scenario.name,
            distance_km=pt_summary.distance_km,
            energy_kwh=pt_summary.net_energy_kwh,
            peak_power_kw=pt_summary.peak_power_kw,
            min_soc=pt_summary.min_soc,
            final_soc=bat_summary.final_soc,
            final_soh=bat_summary.final_soh,
            max_temperature_k=bat_summary.max_temperature_k,
            fault_codes=faults,
            power_limited=pt_summary.power_limited_seconds > 0.0,
            depleted=pt_summary.final_soc <= 1e-9,
        )
        self._operations.append(operation)
        ctx.emit(
            self.name,
            "fleet_operation",
            vin=vehicle.vin,
            day=day,
            distance_km=round(pt_summary.distance_km, 6),
            energy_kwh=round(pt_summary.net_energy_kwh, 6),
            fault_codes=list(faults),
        )

        self._sample_telemetry(
            vehicle, state, day, powertrain.result.trajectory, battery.trajectory, ctx
        )

        state.soc = bat_summary.final_soc
        state.soh = bat_summary.final_soh
        state.odometer_km += pt_summary.distance_km
        state.operated_days += 1
        if state.soh < self._config.maintenance_soh_threshold:
            state.low_soh = True

        self._maybe_schedule_maintenance(vehicle, state, day, faults, ctx)

    def _sample_telemetry(
        self,
        vehicle: FinishedVehicle,
        state: _VehicleState,
        day: float,
        pt_trajectory: Any,
        bat_trajectory: Any,
        ctx: SimulationContext,
    ) -> None:
        interval = self._config.telemetry_interval_s
        samples = [
            (pt, bat)
            for pt, bat in zip(pt_trajectory, bat_trajectory, strict=True)
            if abs(pt.time_s % interval) < 1e-6
        ]
        for pt, bat in samples:
            point = TelemetryPoint(
                vin=vehicle.vin,
                day=day,
                time_s=pt.time_s,
                speed_mps=pt.speed_mps,
                battery_power_kw=pt.battery_power_kw,
                soc=bat.soc,
                soh=bat.soh,
                temperature_k=bat.temperature_k,
                current_a=bat.current_a,
                voltage_v=bat.voltage_v,
                odometer_km=state.odometer_km + pt.distance_km,
                faults=bat.faults,
            )
            self._telemetry.append(point)
            ctx.emit(
                self.name,
                "telemetry_sample",
                vin=vehicle.vin,
                day=day,
                time_s=pt.time_s,
                soc=round(bat.soc, 6),
                faults=[f.value for f in bat.faults],
            )

    def _maybe_schedule_maintenance(
        self,
        vehicle: FinishedVehicle,
        state: _VehicleState,
        day: float,
        faults: tuple[str, ...],
        ctx: SimulationContext,
    ) -> None:
        reason: str | None = None
        if faults:
            reason = faults[0]
        elif state.soh < self._config.maintenance_soh_threshold:
            reason = "low_soh"
        if reason is None:
            return
        event = MaintenanceEvent(
            vin=vehicle.vin,
            start_day=day + 1,
            duration_days=self._config.maintenance_days,
            reason=reason,
        )
        self._maintenance.append(event)
        state.maintenance_remaining_days = self._config.maintenance_days
        ctx.emit(
            self.name,
            "maintenance_scheduled",
            vin=vehicle.vin,
            start_day=event.start_day,
            duration_days=event.duration_days,
            reason=reason,
        )

    # -- result -----------------------------------------------------------

    def build_result(self, *, run_id: str, days: float) -> FleetSimulationResult:
        fleet_size = len(self._vehicles)
        operating_days = sum(s.operated_days for s in self._states.values())
        total_distance_km = sum(o.distance_km for o in self._operations)
        total_energy_kwh = sum(o.energy_kwh for o in self._operations)
        fault_counts: dict[str, int] = {}
        for op in self._operations:
            for code in op.fault_codes:
                fault_counts[code] = fault_counts.get(code, 0) + 1
        low_soh_vehicles = sum(1 for s in self._states.values() if s.low_soh)

        analytics = FleetAnalytics(
            source_run_id=run_id,
            days=days,
            fleet_size=fleet_size,
            operating_days=operating_days,
            availability=operating_days / (fleet_size * days),
            total_distance_km=total_distance_km,
            total_energy_kwh=total_energy_kwh,
            average_consumption_kwh_per_km=(
                total_energy_kwh / total_distance_km if total_distance_km > 0 else None
            ),
            total_telemetry_points=len(self._telemetry),
            fault_counts=fault_counts,
            maintenance_events=len(self._maintenance),
            low_soh_vehicles=low_soh_vehicles,
            avg_final_soh=sum(s.soh for s in self._states.values()) / fleet_size,
        )
        return FleetSimulationResult(
            analytics=analytics,
            operations=tuple(self._operations),
            telemetry=tuple(self._telemetry),
            maintenance=tuple(self._maintenance),
        )


@dataclass(frozen=True)
class FleetSimulation:
    """A completed fleet run: engine record plus typed result."""

    run: SimulationRun
    result: FleetSimulationResult
    events: tuple[dict[str, object], ...] = ()


class FleetSimulator:
    """Runs finished vehicles through daily fleet operations on the shared engine.

    Deterministic per (config, vehicles, scenario, seed, version): the only
    randomness is the per-vehicle daily operation draw through the engine RNG.
    """

    def __init__(
        self,
        config: FleetConfig,
        vehicles: tuple[FinishedVehicle, ...],
        scenario: DrivingScenario,
        battery_config: BatteryConfig | None = None,
    ) -> None:
        self._config = config
        self._vehicles = tuple(sorted(vehicles, key=lambda v: v.vin))
        self._scenario = scenario
        self._battery_config = battery_config if battery_config is not None else BatteryConfig()

    def simulate(self, *, days: float, seed: int = 0) -> FleetSimulation:
        if days <= 0:
            raise ValueError(f"run horizon must be positive, got {days!r}")
        if not self._vehicles:
            raise ValueError("fleet needs at least one finished vehicle")
        engine = SimulationEngine(
            seed=seed,
            step_days=1.0,
            config={
                "system": "fleet",
                "scenario": {
                    "name": self._scenario.name,
                    "duration_s": self._scenario.duration_s,
                    "timestep_s": self._scenario.timestep_s,
                },
                "fleet_config": self._config.model_dump(),
                "battery_config": self._battery_config.model_dump(),
                "vehicles": [
                    {
                        "vin": v.vin,
                        "order_id": v.order_id,
                        "completed_at_day": v.completed_at_day,
                    }
                    for v in self._vehicles
                ],
                "horizon_days": days,
            },
        )
        subsystem = FleetSubsystem(
            self._config, self._vehicles, self._scenario, self._battery_config
        )
        engine.add_subsystem(subsystem)
        run = engine.run(days=days)
        result = subsystem.build_result(run_id=run.run_id, days=days)
        run.result = result.analytics.model_dump()
        return FleetSimulation(run=run, result=result, events=tuple(engine.log.entries))
