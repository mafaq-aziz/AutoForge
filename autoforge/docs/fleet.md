# Connected fleet + telemetry — SIMPLIFIED MODEL

This document specifies the fleet subsystem in `autoforge/services/fleet/`
(`config.py`, `simulator.py`, `result.py`) and the domain models in
`autoforge/domain/fleet.py`. It is an educational simulation, not a fleet
management system, and no telemetry here comes from a real vehicle. Every
simplification is listed at the end.

## Scope

The fleet is the DATA step of the core loop. It consumes the factory's
`FinishedVehicle` records and operates them day by day against a scenario:

1. Each operating vehicle replays the scenario through the existing
   `PowertrainSimulator` and `BatterySimulator` (nested runs, deterministic).
2. Battery telemetry is sampled at a configurable interval
   (`telemetry_interval_s`) straight from the powertrain/battery trajectories.
3. A vehicle's SOC and SOH carry forward between days: SOC depletes over
   repeated drives (no charging is modeled), and SOH accumulates the battery
   model's energy-throughput degradation.
4. Maintenance rules take a vehicle out of service for `maintenance_days`
   when its battery reported a fault, or when carried SOH drops below
   `maintenance_soh_threshold`.

The result is a typed `FleetSimulationResult` with `FleetAnalytics`,
`VehicleOperation` records, `TelemetryPoint` samples, and `MaintenanceEvent`s.

## Determinism and time

The subsystem runs on the shared `SimulationEngine` with one-day steps and
processes exactly one fleet day per whole day accumulated (same pattern as the
factory). The only randomness is the per-vehicle, per-day operation draw
(`operation_probability`) through the engine's `SeededRng`, drawn in fixed VIN
order. Powertrain/battery physics use no randomness. Therefore identical
(config, vehicles, scenario, seed, version) reproduce identical operations,
telemetry, maintenance events, and analytics.

## Vehicle life cycle

For each vehicle in VIN order, each day:

1. If the vehicle is in maintenance (`maintenance_remaining_days > 0`), it
   stays in service for the day and the counter decreases.
2. If the carried SOC is at 0 (depleted), the vehicle is `vehicle_out_of_charge`
   and does not operate — there is no charging model, so a depleted vehicle
   stays off the road. This prevents a depleted vehicle from "operating" at
   zero battery power.
3. Otherwise a seeded draw decides whether the vehicle operates.
4. An operating vehicle replays the scenario; SOC/SOH/odometer advance; a
   `VehicleOperation` is recorded; telemetry is sampled at
   `telemetry_interval_s` (every trajectory point whose `time_s` is an exact
   multiple of the interval).
5. After the drive, if the battery produced any fault code, or carried SOH is
   below `maintenance_soh_threshold`, a `MaintenanceEvent` is scheduled for the
   next day and the vehicle leaves service for `maintenance_days`.

## Telemetry

`TelemetryPoint` is one sampled slice of a vehicle's drive: time, speed,
battery power, SOC, SOH, temperature, current, voltage, cumulative odometer,
and the battery faults active at that instant. The values are copied verbatim
from the powertrain and battery trajectories at the same timestamp; the fleet
never fabricates data.

## Fleet analytics

`FleetAnalytics` aggregates only what the run actually produced:

- `operating_days` / `availability` — vehicle-days driven over
  `fleet_size * days`.
- `total_distance_km`, `total_energy_kwh` (net), `average_consumption_kwh_per_km`.
- `total_telemetry_points`, `fault_counts` (fault code → operating days it
  appeared), `maintenance_events`, `low_soh_vehicles`, `avg_final_soh`.

## Hand calculation (reference case)

Demo pack and default `FleetConfig`, one vehicle, `reference_highway_cycle`
(600 s at 30 m/s, flat) with `telemetry_interval_s = 60`:

- Each operation drives `18.0 km` and consumes about `2.9412 kWh` net, so one
  600 s drive drains `2.9412 / 75 = 0.0392` SOC; SOC carries over day to day
  (`1.0, 0.961, 0.922, ...`).
- Telemetry is sampled at `t = 60, 120, ..., 600` → `10` points per operation.
- SOH after one operation is `1 - 0.2 * (2.9412/75) / 1500 ≈ 0.9999948`, and it
  degrades further on each subsequent day (the battery model carries
  `initial_soh`).
- Three healthy days: `54.0 km`, `8.8236 kWh`, `0.1634 kWh/km`, `30` telemetry
  points, availability `1.0`, no faults, no maintenance.

Asserted in `autoforge/tests/test_fleet.py`
(`TestFleetSimulator::test_hand_calculable_healthy_reference`).

## Maintenance scenarios

- **Fault-driven:** a long highway cycle (20 000 s) depletes the battery in one
  day → the powertrain reports `depleted`, the battery reports `under_voltage`,
  a `MaintenanceEvent` with reason `under_voltage` is scheduled, and the
  vehicle is out of charge for the rest of the run.
- **SOH-driven:** with `maintenance_soh_threshold` set above the natural SOH
  decline, a vehicle is pulled into service once carried SOH crosses the
  threshold, then returns to operation after `maintenance_days`.

## Integration

`FactorySimulator` produces `FinishedVehicle`s (VINs like `AF-<order>-<seq>`);
`FleetSimulator` takes them directly. The factory → fleet path is exercised by
`TestFactoryIntegration`.

## Simplifications (all `SIMPLIFIED MODEL`)

- No charging: SOC carries over and depletes; a depleted vehicle never
  recovers within a run.
- Every vehicle drives the identical scenario each operating day; no route
  planning, driver model, or dispatch.
- Telemetry is a recorded sample of the local simulation, not a real uplink;
  no network, cloud, or message bus.
- Maintenance is a two-line rule (any battery fault, or SOH below threshold)
  with a fixed service duration; no parts, labor, or scheduling logic.
- Fleet-level randomness is limited to the operation-probability draw; vehicle
  physics stays deterministic.
