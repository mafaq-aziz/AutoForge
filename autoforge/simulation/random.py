"""Deterministic randomness for reproducible simulation runs.

Every run draws from a single seeded stream. Two runs with the same seed and
configuration produce identical sequences of draws and therefore identical
results, which is the reproducibility contract of the whole simulator.

Trade-off: one shared stream means a subsystem's draws depend on how often and
in what order other subsystems draw. That is acceptable for now and keeps the
engine simple; per-subsystem streams can be introduced if debugging demands
isolation between modules.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


class SeededRng:
    """Thin wrapper around ``random.Random`` exposing only what modules need."""

    def __init__(self, seed: int) -> None:
        self._seed = seed
        self._rng = random.Random(seed)

    @property
    def seed(self) -> int:
        """The seed the stream was constructed with (recorded on runs)."""
        return self._seed

    def uniform(self, lo: float, hi: float) -> float:
        return self._rng.uniform(lo, hi)

    def gauss(self, mu: float, sigma: float) -> float:
        return self._rng.gauss(mu, sigma)

    def random(self) -> float:
        return self._rng.random()

    def randint(self, a: int, b: int) -> int:
        return self._rng.randint(a, b)

    def choice(self, seq: Sequence[T]) -> T:
        return self._rng.choice(seq)

    def shuffle(self, seq: list[T]) -> None:
        """Shuffle a list in place."""
        self._rng.shuffle(seq)
