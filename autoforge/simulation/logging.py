"""Structured event logging for simulation runs.

Every emitted event carries the simulation time, the emitting subsystem, and
an event type so runs can be replayed and analysed after the fact. Entries are
kept in memory for the lifetime of the run and, optionally, appended to a JSONL
file for long runs or post-processing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StructuredLog:
    """Append-only list of event dicts with optional JSONL persistence."""

    entries: list[dict[str, object]] = field(default_factory=list)
    sink: Path | None = None

    def record(self, entry: dict[str, object]) -> None:
        """Record one event; the entry must already carry time/run/subsystem keys."""
        self.entries.append(entry)
        if self.sink is not None:
            self.sink.parent.mkdir(parents=True, exist_ok=True)
            with self.sink.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")

    def __len__(self) -> int:
        return len(self.entries)
