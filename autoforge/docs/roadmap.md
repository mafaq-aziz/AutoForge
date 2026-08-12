# Roadmap

Implementation follows the phase plan below in small, tested milestones. A
phase is only marked done when its modules are implemented, tested, and
integrated. Nothing here claims production readiness.

## Phase status

| Phase | Scope | Status |
| --- | --- | --- |
| 0 | Foundation: structure, tooling, docs, configs | done |
| 1 | Domain models (company, vehicle, battery, motor) | done (core set) |
| 2 | Simulation engine (clock, RNG, events, engine, logging, run records) | done |
| 3 | Vehicle product designer (models/variants config) | done (model layer: dimensions + target performance on `VehicleVariant`; config-driven designer still later) |
| 4 | EV powertrain (energy use, SOC, range, power, regen) | done |
| 5 | Battery/BMS (SOC, SOH, voltage, current, temperature, degradation) | done |
| 6 | Smart factory (stations, queues, bottlenecks, defects, inventory) | done |
| 7 | Connected fleet (telemetry, faults, maintenance, ADAS events) | planned |
| 8 | ADAS and driver monitoring (perception vs decision separation) | planned |
| 9 | Factory robotics (arm, pick-and-place, motion planning) | planned |
| 10 | AI quality control (vision inspection, structured defects) | planned |
| 11 | Market simulator (customers, segments, competitors, demand) | planned |
| 12 | Finance/strategy (revenue, costs, margin, cash, R&D) | planned |
| 13 | Decision engine (explainable recommendations, decision support) | planned |
| 14 | Executive dashboard (metrics the simulation actually produces) | planned |
| 15 | Feedback loop (telemetry -> insights -> next product) | planned |
| 16 | Performance/reliability (scale, checkpointing, observability) | planned |
| 17 | Documentation, security, licensing, release | planned |

## Cross-cutting principles

- **Baselines before ML.** Classical/statistical baselines precede any ML
  module; ML claims require evaluation on held-out data with reported metrics.
- **Explicitly simulated.** Market, finance, demand, and AI outputs are
  simulated phenomena with documented simplifications, never real-world
  forecasts.
- **Safe boundaries.** ADAS, BMS, robotics, and ML never control real
  actuators and are never presented as safety-critical software.

## Next milestone

Phase 7: the **connected fleet** — finished vehicles enter a fleet, drive
scenarios, stream telemetry, and produce fleet analytics. It closes the loop
from FACTORY -> VEHICLE -> DATA by consuming the factory's `FinishedVehicle`s
and the powertrain/battery physics for each vehicle operation.
