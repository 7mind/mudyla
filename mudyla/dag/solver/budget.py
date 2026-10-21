"""One explicitly shared optional wall-clock deadline for a complete layout operation."""

from collections.abc import Iterable, Iterator
import math
import time
from typing import TypeVar

OVERALL_BUDGET_SECONDS = 0.2
T = TypeVar("T")


class BudgetExpired(Exception):
    def __init__(self, phase: str) -> None:
        super().__init__(f"Overall layout budget expired during {phase}")
        self.phase = phase


class LayoutBudget:
    def __init__(self, seconds: float | None) -> None:
        assert seconds is None or (math.isfinite(seconds) and seconds > 0)
        self.seconds = seconds
        self._started: float | None = None
        self._deadline: float | None = None

    def start(self) -> None:
        if self._started is None:
            self._started = time.monotonic()
            if self.seconds is not None:
                self._deadline = self._started + self.seconds

    @property
    def deadline(self) -> float | None:
        assert self._started is not None, "Layout budget has not started"
        return self._deadline

    def remaining(self) -> float | None:
        assert self._started is not None, "Layout budget has not started"
        if self._deadline is None:
            return None
        return max(0.0, self._deadline - time.monotonic())

    def elapsed(self) -> float:
        assert self._started is not None, "Layout budget has not started"
        return time.monotonic() - self._started

    def limit_to(self, seconds: float) -> None:
        """Shorten the duration measured from the original start, without restarting."""
        assert math.isfinite(seconds) and seconds > 0
        assert self._started is not None, "Layout budget has not started"
        self.seconds = seconds if self.seconds is None else min(self.seconds, seconds)
        self._deadline = self._started + self.seconds

    def check(self, phase: str) -> None:
        remaining = self.remaining()
        if remaining is not None and remaining <= 0:
            raise BudgetExpired(phase)

    def iterate(self, values: Iterable[T], phase: str) -> Iterator[T]:
        for value in values:
            self.check(phase)
            yield value
