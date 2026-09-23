"""SplitMix64 as specified in docs/generator.md. Must stay bit-identical to CritterRng.cs."""

from __future__ import annotations

from typing import Sequence, TypeVar

MASK = (1 << 64) - 1
T = TypeVar("T")


class SplitMix64:
    def __init__(self, seed: int):
        seed &= MASK
        self.state = seed if seed != 0 else 1

    def next(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & MASK
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK
        return z ^ (z >> 31)

    def below(self, n: int) -> int:
        if n <= 0:
            raise ValueError("below(n) requires n > 0")
        return (self.next() >> 33) % n

    def pick(self, items: Sequence[T]) -> T:
        return items[self.below(len(items))]

    def roll(self, pct: int) -> bool:
        return self.below(100) < pct
