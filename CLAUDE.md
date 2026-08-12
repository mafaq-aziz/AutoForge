# CLAUDE.md — guidance for AI coding agents

This file tells AI coding tools how to work in this repository. It assumes you
have read README.md and ARCHITECTURE.md.

## What this project is

AutoForge is an open-source, AI-native **automobile company simulator** for
education and research. It models an interconnected company (PRODUCT ->
ENGINEERING -> FACTORY -> VEHICLE -> CUSTOMER -> DATA -> AI -> NEXT PRODUCT).
It is a simulation, never software for real safety-critical control.

## Commands

Run these from the repository root.

```bash
make test       # pytest
make lint       # ruff check
make format     # ruff format
make typecheck  # mypy
make check      # lint + format-check + typecheck + test
make demo       # python -m autoforge.scripts.demo_foundation
```

## Conventions

- **Milestones, not monoliths.** Build in small testable steps. Never build the
  whole project in one pass, and never claim untested features work.
- **Domain models** are frozen, validated Pydantic models with explicit unit
  suffixes in field names (`_m2`, `_kwh`, `_kg`, `_v`).
- **Simulation engine** rules: every run has a seed; all randomness flows
  through `SeededRng`; events go through `EventQueue`; subsystems implement
  `step(ctx, dt_days)` and emit structured events via `ctx.emit(...)`. Do not
  add unseeded randomness or global mutable state.
- **Powertrain** rules: energy is always conserved (SOC change × usable energy
  equals net energy); regen energy is never created — unstoreable regen is
  discarded and counted in `regen_discarded_kwh`; SOC is clamped to
  `[soc_min, soc_max]`; unmet demand sets `depleted`/`power_limited`; the model
  is deterministic (no RNG), and everything simplified carries a
  `SIMPLIFIED MODEL` label. See `autoforge/docs/powertrain.md` for equations
  and the hand-derived reference cycle.
- **Battery/BMS** rules: the battery layer (`autoforge/services/battery/`)
  consumes powertrain results and never overrides them — the powertrain stays
  authoritative for SOC/energy; the battery's own SOC is only a consistency
  check (`max_soc_error`). Current/voltage obey `V*I == P` exactly on the
  linear-OCV physical branch (power-limited beyond capability), temperature is
  lumped I²R heat with Newton cooling, SOH is a linear energy-throughput
  baseline, and faults are deterministic threshold rules (no ML, no synthetic
  fault data). No randomness. See `autoforge/docs/battery.md`.
- **Honest labels.** Label anything simplified or not yet real with
  `SIMPLIFIED MODEL`, `PLACEHOLDER`, or `TODO`. No fake AI, physics,
  benchmarks, accuracy, or real-world predictions.
- **ML discipline.** Before any ML claim, establish a baseline and evaluate on
  held-out data. Report only metrics from actual evaluation.
- **Comments explain why**, assumptions, trade-offs, non-obvious logic,
  limitations, or safety — not what the code obviously does.
- **Code quality.** No obvious comments, over-abstraction, giant functions,
  duplication, dead code, generic names, or AI-slop.

## Definition of done

A feature is done only when it is implemented, tested, edge cases handled,
errors handled, documented, configured, integrated, and free of known hidden
critical issues.

## When in doubt

Ask the user before expanding scope. If a change is not justified by
engineering evidence, prefer the simpler option.
