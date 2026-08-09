# Contributing

Thanks for considering contributing to AutoForge.

## Ground rules

- **Simulation only.** This project simulates an automobile company. No module
  here controls real vehicles, factories, or robots, and no contribution may
  present itself as safety-critical production software.
- **Honest by construction.** Do not claim that simulated or hypothetical
  results are real. Label simplifications. Report only metrics from actual
  evaluation on held-out data.
- **Small, testable steps.** A feature is done only when implemented, tested,
  documented, configured, integrated, and free of known critical issues.

## Development setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Before you submit

Run the full check suite and make sure it is green:

```bash
make check
```

This runs lint (ruff), format-check, typecheck (mypy), and tests (pytest).

- New behavior must come with tests covering normal cases **and** edge cases.
- Keep modules independently testable; no global mutable state, no unseeded
  randomness.
- Use explicit unit suffixes in field names and SI units internally.

## Commits

Use Conventional Commits, e.g.:

```
feat(simulation): add discrete-event queue
fix(battery): validate voltage ordering
docs: explain reproducibility contract
```

One logical change per commit; meaningful commit messages that state *why*.

## Code review

Reviewers check for correctness, honest claims, over-engineering, duplication,
dead code, and whether the change follows the architecture in ARCHITECTURE.md.

## Scope

If in doubt, ask. Prefer the simpler option unless the change is justified by
engineering evidence.
