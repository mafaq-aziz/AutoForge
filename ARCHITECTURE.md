# Architecture

AutoForge is a **modular monolith**: one deployable, layered Python package,
not microservices. Microservices would add distribution, consistency, and
deployment cost that a single-process simulator does not need. The layering
keeps modules independently testable while the shared simulation engine keeps
them consistent in time.

## Layers

```mermaid
graph TD
    APP["apps: entry points"] --> SVC["services: subsystems"]
    SVC --> DOM["domain: typed models"]
    SVC --> SIM["simulation: engine"]
    SIM --> DOM
    ML["ml: evaluation-backed models"] --> DOM
```

- **domain/** — frozen Pydantic models describing company, vehicles, battery
  packs, motors, and later factories, fleets, markets. No behavior, only
  validated structure and explicit units.
- **simulation/** — the engine: clock (days), seeded RNG, discrete-event queue,
  subsystem protocol, structured logging, and run records.
- **services/** — subsystems that implement `step(ctx, dt_days)` and emit
  structured events (powertrain, BMS, ADAS, factory, fleet, market, finance).
  Landing in later phases.
- **apps/** — runnable entry points: the headless simulation app today, a
  FastAPI backend and web dashboard later.
- **ml/** — only ML modules that have been evaluated on data; classical methods
  are preferred where they are better.

Dependencies point inward: apps -> services -> simulation/domain. Services
never import apps; subsystems never reach into the engine internals.

## The core loop

```mermaid
graph LR
    P["PRODUCT"] --> E["ENGINEERING"]
    E --> F["FACTORY"]
    F --> V["VEHICLE"]
    V --> C["CUSTOMER"]
    C --> D["DATA"]
    D --> A["AI"]
    A --> N["NEXT PRODUCT"]
    N --> P
```

Each phase of the loop is a subsystem; events and telemetry travel through the
shared structured log so the loop can be traced end to end.

## Reproducibility contract

A run is defined by `(seed, config, autoforge_version)`. The engine:

- advances a single `SimulationClock` in fixed day steps (partial final step
  to hit an exact horizon);
- draws all randomness from one seeded `random.Random` stream;
- schedules discrete events on a min-heap with stable (time, insertion) order;
- writes every emitted event to a `StructuredLog` (in memory, optionally
  JSONL) tagged with simulation time, run id, and subsystem;
- records seed, config, version, timestamps, step count, and results on the
  `SimulationRun`.

Same inputs therefore reproduce the same run id and event log. Trade-offs:

- **Single RNG stream.** A subsystem's draws depend on the draw order of other
  subsystems. Simple and fine now; per-subsystem streams are a documented
  future improvement for isolation.
- **Days as base unit.** Physics modules will convert to SI seconds
  internally; field names carry explicit unit suffixes (e.g. `_m2`, `_kwh`,
  `_kg`) so there is never ambiguity.

## Units and validation

- Domain models are **frozen** and validated at construction; nonsense
  configurations (reversed voltage bounds, continuous power above peak,
  negative mass) fail fast.
- SI preferred; industry-conventional units (km, kWh) allowed where the field
  name states the unit.
- No secrets are ever committed; runtime configuration lives in `.env` (see
  `.env.example`) and run configuration in `autoforge/configs/`.

## Evolution path

1. **Phase 3-4**: vehicle designer + EV powertrain as the first real
   subsystems, exercising the engine end to end.
2. **Phase 14-15**: finance and the feedback loop, the first cross-subsystem
   integrations.
3. **apps/web**: React dashboard on top of the FastAPI app; the dashboard only
   reads what the simulation actually produces.
4. Microservices, ROS2, CARLA, MQTT/Kafka, Redis, CUDA, or cloud services will
   only be added when a concrete requirement justifies them — not for prestige.

## Safety boundary

AutoForge simulates software and never controls hardware. ADAS, BMS, robotics,
and any ML modules are experimental simulation models and must not be
represented as safety-critical production software. See [SECURITY.md](SECURITY.md).
