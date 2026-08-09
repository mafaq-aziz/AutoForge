"""Tests for the simulation engine foundation: clock, RNG, events, engine."""

from functools import partial

import pytest

from autoforge.simulation.clock import SimulationClock
from autoforge.simulation.engine import SimulationContext, SimulationEngine
from autoforge.simulation.events import EventQueue
from autoforge.simulation.random import SeededRng


class TestClock:
    def test_advances_by_step(self) -> None:
        clock = SimulationClock(step_days=0.5)
        assert clock.advance(0.5) == pytest.approx(0.5)
        assert clock.advance(0.5) == pytest.approx(1.0)

    def test_non_positive_step_rejected(self) -> None:
        with pytest.raises(ValueError):
            SimulationClock(step_days=0.0)
        with pytest.raises(ValueError):
            SimulationClock(step_days=-1.0)

    def test_cannot_rewind(self) -> None:
        clock = SimulationClock(step_days=1.0)
        clock.advance(1.0)
        with pytest.raises(ValueError):
            clock.advance(-0.1)


class TestSeededRng:
    def test_same_seed_same_draws(self) -> None:
        a = SeededRng(7)
        b = SeededRng(7)
        assert [a.uniform(0.0, 1.0) for _ in range(10)] == [b.uniform(0.0, 1.0) for _ in range(10)]

    def test_different_seed_different_draws(self) -> None:
        a = SeededRng(7)
        b = SeededRng(8)
        assert a.uniform(0.0, 1.0) != b.uniform(0.0, 1.0)

    def test_seed_is_exposed_for_records(self) -> None:
        assert SeededRng(42).seed == 42

    def test_draws_stay_in_range(self) -> None:
        rng = SeededRng(1)
        for _ in range(1000):
            value = rng.uniform(-1.0, 2.0)
            assert -1.0 <= value < 2.0


class TestEventQueue:
    def test_fires_due_events_in_time_order(self) -> None:
        queue = EventQueue()
        order: list[str] = []
        for t, tag in [(3.0, "c"), (1.0, "a"), (2.0, "b")]:
            queue.schedule(t, partial(order.append, tag))
        for handler in queue.due(2.5):
            handler()
        assert order == ["a", "b"]
        for handler in queue.due(10.0):
            handler()
        assert order == ["a", "b", "c"]

    def test_same_time_fires_in_insertion_order(self) -> None:
        queue = EventQueue()
        order: list[str] = []
        for tag in ["first", "second", "third"]:
            queue.schedule(1.0, partial(order.append, tag))
        for handler in queue.due(1.0):
            handler()
        assert order == ["first", "second", "third"]

    def test_future_events_are_not_popped(self) -> None:
        queue = EventQueue()
        fired: list[str] = []
        queue.schedule(5.0, lambda: fired.append("future"))
        assert queue.due(4.0) == []
        assert len(queue) == 1
        for handler in queue.due(5.0):
            handler()
        assert fired == ["future"]

    def test_negative_time_rejected(self) -> None:
        queue = EventQueue()
        with pytest.raises(ValueError):
            queue.schedule(-1.0, lambda: None)

    def test_next_time(self) -> None:
        queue = EventQueue()
        assert queue.next_time() is None
        queue.schedule(2.5, lambda: None)
        assert queue.next_time() == pytest.approx(2.5)


class _RecordingSubsystem:
    """Test double: records every step it observes."""

    name = "recorder"

    def __init__(self) -> None:
        self.steps: list[tuple[float, float]] = []

    def step(self, ctx: SimulationContext, dt_days: float) -> None:
        self.steps.append((ctx.clock.now_days, dt_days))
        ctx.emit(self.name, "tick", now_days=ctx.clock.now_days)


class TestEngine:
    def test_run_reaches_exact_horizon_with_partial_step(self) -> None:
        engine = SimulationEngine(seed=1, step_days=3.0)
        run = engine.run(days=10.0)
        assert engine.clock.now_days == pytest.approx(10.0)
        assert run.steps == 4

    def test_run_rejects_non_positive_horizon(self) -> None:
        engine = SimulationEngine(seed=1)
        with pytest.raises(ValueError):
            engine.run(days=0.0)

    def test_run_record_metadata(self) -> None:
        engine = SimulationEngine(seed=123, config={"demo": True})
        run = engine.run(days=2.0)
        assert run.seed == 123
        assert run.autoforge_version == "0.1.0"
        assert run.finished_at is not None
        assert run.steps == 2
        assert run.run_id

    def test_subsystems_see_fixed_steps_and_emit_events(self) -> None:
        engine = SimulationEngine(seed=1, step_days=0.5)
        sub = _RecordingSubsystem()
        engine.add_subsystem(sub)
        engine.run(days=2.0)
        assert sub.steps == [(0.5, 0.5), (1.0, 0.5), (1.5, 0.5), (2.0, 0.5)]
        assert engine.run_record.events_logged == 4

    def test_scheduled_events_fire_during_run(self) -> None:
        engine = SimulationEngine(seed=1)
        fired: list[float] = []

        def record() -> None:
            fired.append(engine.clock.now_days)

        engine.schedule(2.0, record)
        engine.schedule(1.0, record)
        engine.run(days=3.0)
        assert fired == [1.0, 2.0]

    def test_same_seed_reproduces_same_event_log(self) -> None:
        def one_run() -> list[dict[str, object]]:
            engine = SimulationEngine(seed=99, step_days=1.0)
            engine.add_subsystem(_RecordingSubsystem())
            engine.run(days=5.0)
            return list(engine.log.entries)

        assert one_run() == one_run()

    def test_single_step_returns_event_count(self) -> None:
        engine = SimulationEngine(seed=1)
        engine.schedule(1.0, lambda: None)
        engine.schedule(1.0, lambda: None)
        assert engine.step() == 2

    def test_structured_log_entries_carry_context(self) -> None:
        engine = SimulationEngine(seed=1)
        sub = _RecordingSubsystem()
        engine.add_subsystem(sub)
        engine.run(days=1.0)
        entry = engine.log.entries[0]
        assert entry["run_id"] == engine.run_record.run_id
        assert entry["subsystem"] == "recorder"
        assert entry["event"] == "tick"
        assert entry["time_days"] == pytest.approx(1.0)
