"""One explicitly shared wall-clock deadline for a complete layout operation."""

from collections.abc import Iterable, Iterator
import time
from typing import TypeVar

OVERALL_BUDGET_SECONDS = 0.2
AUTO_FIRST_SOLUTION_BUDGET_SECONDS = 1.0
T = TypeVar("T")


class BudgetExpired(Exception):
    def __init__(self, phase: str) -> None:
        super().__init__(f"Overall layout budget expired during {phase}")
        self.phase = phase


class LayoutBudget:
    def __init__(self, seconds: float) -> None:
        assert seconds > 0
        self.seconds = seconds
        self._deadline: float | None = None

    def start(self) -> None:
        if self._deadline is None:
            self._deadline = time.monotonic() + self.seconds

    def remaining(self) -> float:
        assert self._deadline is not None, "Layout budget has not started"
        return max(0.0, self._deadline - time.monotonic())

    def limit_to(self, seconds: float) -> None:
        """Shorten the duration measured from the original start, without restarting."""
        assert seconds > 0
        assert self._deadline is not None, "Layout budget has not started"
        duration = min(self.seconds, seconds)
        self._deadline -= self.seconds - duration
        self.seconds = duration

    def check(self, phase: str) -> None:
        if self.remaining() <= 0:
            raise BudgetExpired(phase)

    def iterate(self, values: Iterable[T], phase: str) -> Iterator[T]:
        for value in values:
            self.check(phase)
            yield value
