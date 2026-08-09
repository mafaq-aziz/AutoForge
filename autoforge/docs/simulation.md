# Simulation engine

The engine is the shared skeleton every subsystem plugs into. Its job is to
keep all modules on the same notion of time and randomness and to make every
run reproducible and auditable.

## Components

| Module | Responsibility |
| --- | --- |
| `simulation/clock.py` | `SimulationClock`: fixed day steps, partial final step support |
| `simulation/random.py` | `SeededRng`: one deterministic stream per run |
| `simulation/events.py` | `EventQueue`: min-heap of events, stable ordering |
| `simulation/logging.py` | `StructuredLog`: in-memory + optional JSONL event sink |
| `simulation/engine.py` | `SimulationEngine`, `SimulationContext`, `SimulationRun` |

## How a step works

1. The clock advances by `dt_days` (the full step, or a partial step to hit an
   exact run horizon).
2. All events due at or before the new time fire, in (time, insertion) order.
3. Every registered subsystem is called as `subsystem.step(ctx, dt_days)`.
4. Subsystems publish structured events through `ctx.emit(...)`, which stamps
   time, run id, subsystem, and event type.

## Subsystem contract

```python
class Subsystem(Protocol):
    name: str

    def step(self, ctx: SimulationContext, dt_days: float) -> None: ...
```

A subsystem must be stateless about time: it receives the current time via the
context and should store its own state (queues, SOC, cash) as instance state,
persisting it only through emitted events. It must not reach into the engine.

## Units

- Company-scale time is measured in **days**.
- Physics modules convert to SI seconds internally; field names always carry
  their unit suffix (`_m2`, `_kwh`, `_kg`, `_v`).

## Reproducibility

`(seed, config, autoforge_version)` determines a run. The run id is a digest
of seed and config, so identical inputs are visibly identical on the record.
Same inputs => same event log.
