# Smart factory — SIMPLIFIED MODEL

This document specifies the factory subsystem in `autoforge/services/factory/`
(`config.py`, `simulator.py`, `result.py`) and the domain models in
`autoforge/domain/factory.py`. It is an educational simulation, not a
production-planning or MES tool. Every simplification is listed at the end.

## Scope

Orders for a `VehicleVariant` are released onto a fixed assembly line and each
unit moves through the line as a discrete `WorkItem`. Per day, each station
starts up to `capacity_per_day` items (bounded by in-process slots
`capacity_per_day * cycle_time_days`) and every in-process item advances by one
day. Stations can break down, flag defects, and run out of material. Defects
are caught at QC and either reworked (returned to the flagging station) or
scrapped. Vehicles that pass QC become `FinishedVehicle`s — the input to the
fleet phase.

The default line (7 stations, one day each) is:

```
RAW -> BATTERY -> BODY -> PAINT -> POWERTRAIN -> FINAL_ASSEMBLY -> QUALITY_INSPECTION -> FINISHED
```

Default capacities (items/day): RAW 1000, BATTERY 10, BODY 12, PAINT 10,
POWERTRAIN 11, FINAL_ASSEMBLY 10, QC 15. The bottleneck is therefore 10/day.

## Determinism and time

The subsystem runs on the shared `SimulationEngine` with one-day steps. It
accumulates engine time and processes exactly one factory day per whole day
accumulated; a partial final step contributes less than one day and is
ignored, so the factory granularity is one day. All randomness (defect draws,
rework-vs-scrap draws, downtime onset) flows through the engine's `SeededRng`
in a fixed draw order, so identical (config, orders, seed, version) reproduce
the identical event log and results.

Orders with `release_day <= now` are released on the first engine day at or
after their release day. An item spends one day per station, so with the
7-station line the earliest finish for an order released on day 1 is day 8.

## Station processing (per day)

For each station in line order:

1. **Downtime.** If down, consume one remaining downtime day and stop (in-process
   items are frozen). When the last downtime day ends, the station recovers.
   If healthy, a draw `rng.random() < downtime_probability_per_day` triggers a
   downtime episode of `mean_downtime_days` full days.
2. **Completions.** Every in-process item's `station_remaining_days` decreases
   by one; items reaching zero complete. Each completion is counted, a defect
   may be flagged (`rng.random() < defect_rate`), and the item is pushed to the
   next station's input queue.
3. **Starts.** Credit accumulates at `capacity_per_day` per day. While credit
   and in-process slots remain and the input queue is non-empty, the station
   starts items, withdrawing one unit of each `consumes` part from inventory.
   If a required part is out of stock, the queued items wait
   (`WAITING_FOR_MATERIAL`) and `material_wait_days` grows.

FIFO queues sit between stations; there is no other buffer policy.

## Quality

A station that flags an item sets its `defect_stage`/`defect_code` (first
defect kept). At QC completion:

- no defect -> `PASS`, a `FinishedVehicle` is produced and the order's
  produced count increments;
- defect -> draw `rng.random() < defect_station.rework_fraction`: reworked
  (cleared of the defect, `rework_count += 1`, returned to the flagging
  station's queue) or scrapped (`FAIL`). An item whose `rework_count` has
  reached `rework_repeat_limit` is scrapped regardless.

Every QC decision is recorded as a `QualityInspection` (PASS/REWORK/FAIL).

## Inventory

`InventoryItem(part, start_stock, replenish_per_day)`. Stock is drained when
stations start items and refilled at the continuous per-day rate. If a
`consumes` part reaches 0 while items wait, the station is starved until stock
returns; `material_shortage` / `material_resolved` events mark the episode.

## Cost foundation

- `FinishedVehicle.production_cost_eur = variable_cost_eur + rework_cost_eur * rework_count`.
- `rework_cost_eur` is charged each time an item is reworked;
  `scrap_cost_eur` is charged for each scrapped item.
- `total_cost_eur = finished_cost + scrap_cost_eur` (rework cost is already
  inside finished costs).

This is deliberately a foundation: no fixed costs, labor, energy, tooling
amortization, or overhead allocation.

## Reference scenario (hand calculation)

Default line and inventory, no defects, no downtime. Order ORD1 for 30 units
released on day 1, horizon 10 days.

Every station processes in whole days and the bottleneck is 10/day, so:

```
release day             = 1
first finish            = 1 + 7 stations = day 8
last finish (30 units)  = day 8 + ceil(30/10) - 1 = day 10
finished vehicles       = 30
throughput (steady)     = min(capacity) = 10/day
inspections             = 30 PASS, 0 REWORK, 0 FAIL
cost                    = 30 * 32 000 = 960 000 EUR
bottleneck (utilization) = battery, paint, final_assembly (all 0.30 at 10 days)
```

A longer run confirms steady state: 200 units over 27 days finishes exactly
200 (10/day from day 8 to day 27), i.e. measured throughput equals the
bottleneck capacity. These are asserted in
`autoforge/tests/test_factory.py` (`TestReferenceScenario`).

Seed-locked stochastic cases (also in the test suite) cover rework vs scrap,
the `rework_repeat_limit`, material shortage with `start_stock=10`, continuous
replenishment, and a station that breaks down with probability 1 (10 downtime
days and 90 completed items over 20 days).

## Validation

- `ProductionStation` requires a `defect_code` whenever `defect_rate > 0`, and
  `mean_downtime_days >= 1` whenever `downtime_probability_per_day > 0` (day
  granularity).
- `FactoryConfig` requires a non-empty line starting at RAW and ending at
  QUALITY_INSPECTION, with each stage exactly once and in canonical order.
- `ProductionOrder` requires a positive quantity; `WorkItem`/`FinishedVehicle`
  fields are bounded.

## Simplifications (all `SIMPLIFIED MODEL`)

- Continuous one-day time slicing; no shifts, calendars, setups, or batching.
- Stations are single-machine nodes with a scalar capacity and fixed cycle
  time; no parallel-cell packing, no line-balancing optimization.
- FIFO queues with no overflow or WIP limits — queues are unbounded.
- Defect and downtime rates are constant draws from the seeded RNG; there is
  no real quality or reliability data, no root-cause attribution, no repair
  workflow beyond the rework loop.
- Rework loops only repeat the flagging station's stage (through QC again);
  no parallel rework cells.
- Inventory is a continuous per-day refill; no order quantities, lead times,
  holding cost, or supplier choice.
- Cost is variable + rework + scrap only; no fixed or operating cost structure.
- Orders are produced first-in-first-out with no scheduling or priority logic
  beyond `release_day`.
