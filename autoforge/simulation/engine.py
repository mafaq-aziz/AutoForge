"""The simulation engine: clock, event queue, subsystems, and run records.

The engine advances a shared clock, fires due discrete events, then calls every
registered subsystem's ``step`` so each module observes the same "now" and can
publish structured events. Subsystems are intentionally kept independent: they
communicate only through the context handles and the shared structured log,
which is what lets modules be developed and tested in isolation.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field

import autoforge
from autoforge.simulation.clock import SimulationClock
from autoforge.simulation.events import EventHandler, EventQueue
from autoforge.simulation.logging import StructuredLog
from autoforge.simulation.random import SeededRng


class Subsystem(Protocol):
    """Minimal contract for anything the engine can tick."""

    name: str

    def step(self, ctx: SimulationContext, dt_days: float) -> None: ...


class SimulationContext:
    """Handles handed to subsystems each tick.

    Deliberately small: a subsystem receives time, randomness, the event
    scheduler, and the log. It must not reach into the engine itself, which
    keeps the dependency direction one-way (engine -> subsystem).
    """

    def __init__(
        self,
        clock: SimulationClock,
        rng: SeededRng,
        events: EventQueue,
        log: StructuredLog,
        run_id: str,
    ) -> None:
        self.clock = clock
        self.rng = rng
        self.events = events
        self.log = log
        self.run_id = run_id

    def emit(self, subsystem: str, event_type: str, **payload: Any) -> None:
        """Record a structured event from ``subsystem`` at the current time."""
        entry = {
            "time_days": self.clock.now_days,
            "run_id": self.run_id,
            "subsystem": subsystem,
            "event": event_type,
            **payload,
        }
        self.log.record(entry)


class SimulationRun(BaseModel):
    """Metadata and outcome of one simulation run.

    Together with the seed this identifies a fully reproducible run: same seed,
    same config, same autoforge version reproduces the same event log.
    """

    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    seed: int
    config: dict[str, Any] = Field(default_factory=dict)
    autoforge_version: str = autoforge.__version__
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    steps: int = 0
    events_logged: int = 0
    result: dict[str, Any] = Field(default_factory=dict)


class SimulationEngine:
    """Drives subsystems forward in time with reproducible randomness."""

    def __init__(
        self,
        *,
        seed: int,
        step_days: float = 1.0,
        run_id: str | None = None,
        config: dict[str, Any] | None = None,
        log_sink: Any = None,
    ) -> None:
        self.clock = SimulationClock(step_days=step_days)
        self.rng = SeededRng(seed)
        self.events = EventQueue()
        self.log = StructuredLog(sink=log_sink)
        self.subsystems: list[Subsystem] = []
        self.run_record = SimulationRun(
            run_id=run_id or self._derive_run_id(seed, config or {}),
            seed=seed,
            config=config or {},
        )

    @staticmethod
    def _derive_run_id(seed: int, config: dict[str, Any]) -> str:
        """Stable run identity so identical inputs produce identical run ids.

        The run id is a digest of the seeding inputs, not a random label, which
        makes "same seed + config + version" observable on the record itself.
        """
        serialized = json.dumps({"seed": seed, "config": config}, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]

    def add_subsystem(self, subsystem: Subsystem) -> SimulationEngine:
        """Register a subsystem to receive a ``step`` call every tick."""
        self.subsystems.append(subsystem)
        return self

    def schedule(self, time_days: float, handler: EventHandler, priority: int = 0) -> None:
        """Schedule an event on the shared queue (see ``EventQueue.schedule``)."""
        self.events.schedule(time_days, handler, priority)

    def step(self) -> int:
        """Advance one timestep: fire due events, then tick subsystems.

        Returns the number of events processed this step.
        """
        self.clock.advance(self.clock.step_days)
        ctx = SimulationContext(self.clock, self.rng, self.events, self.log, self.run_record.run_id)
        due_handlers = self.events.due(self.clock.now_days)
        for handler in due_handlers:
            handler()
        for subsystem in self.subsystems:
            subsystem.step(ctx, self.clock.step_days)
        self.run_record.steps += 1
        return len(due_handlers)

    def run(self, days: float) -> SimulationRun:
        """Run until the clock has advanced ``days`` from the current time.

        Uses partial final steps so the run stops exactly on the requested
        horizon regardless of the configured step size. Returns the populated
        run record.
        """
        if days <= 0:
            raise ValueError(f"run horizon must be positive, got {days!r}")
        target = self.clock.now_days + days
        while self.clock.now_days < target - 1e-9:
            dt = min(self.clock.step_days, target - self.clock.now_days)
            self.clock.advance(dt)
            ctx = SimulationContext(
                self.clock, self.rng, self.events, self.log, self.run_record.run_id
            )
            for handler in self.events.due(self.clock.now_days):
                handler()
            for subsystem in self.subsystems:
                subsystem.step(ctx, dt)
            self.run_record.steps += 1
        return self._finalize()

    def _finalize(self) -> SimulationRun:
        """Stamp completion metadata onto the run record."""
        self.run_record.finished_at = datetime.now(UTC)
        self.run_record.events_logged = len(self.log.entries)
        return self.run_record
