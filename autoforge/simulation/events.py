"""Discrete-event scheduling.

Events are scheduled on the simulation time axis and fired by the engine when
the clock passes their due time. Ordering is stable: same-time events fire in
insertion order, which keeps runs deterministic and easy to reason about.
"""

from __future__ import annotations

import heapq
from collections.abc import Callable
from dataclasses import dataclass, field

EventHandler = Callable[[], None]


@dataclass(order=True)
class _EventEntry:
    time_days: float
    seq: int
    priority: int = field(compare=False)
    handler: EventHandler = field(compare=False)


class EventQueue:
    """Min-heap of scheduled events keyed by due time."""

    def __init__(self) -> None:
        self._heap: list[_EventEntry] = []
        self._seq = 0

    def schedule(self, time_days: float, handler: EventHandler, priority: int = 0) -> None:
        """Schedule ``handler`` to fire when the clock reaches ``time_days``."""
        if time_days < 0:
            raise ValueError(f"cannot schedule events before time 0, got {time_days!r}")
        entry = _EventEntry(time_days, self._seq, priority, handler)
        self._seq += 1
        heapq.heappush(self._heap, entry)

    def due(self, now_days: float) -> list[EventHandler]:
        """Pop and return every handler due at or before ``now_days``.

        The result preserves (time, insertion) ordering, so callers can process
        events in the same order regardless of when they were scheduled.
        """
        due_handlers: list[EventHandler] = []
        while self._heap and self._heap[0].time_days <= now_days:
            due_handlers.append(heapq.heappop(self._heap).handler)
        return due_handlers

    def next_time(self) -> float | None:
        """Due time of the next pending event, or None if the queue is empty."""
        return self._heap[0].time_days if self._heap else None

    def __len__(self) -> int:
        return len(self._heap)
