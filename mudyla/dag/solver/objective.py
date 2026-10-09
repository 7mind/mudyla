"""Explicit selection objectives for completed native candidates."""

from typing import Protocol

from .budget import LayoutBudget
from .model import LayoutScore, SolverCandidate


class CandidateObjective(Protocol):
    def score(self, candidate: SolverCandidate, budget: LayoutBudget) -> LayoutScore:
        ...


class NativeObjective:
    def score(self, candidate: SolverCandidate, budget: LayoutBudget) -> LayoutScore:
        assert candidate.score is not None
        return candidate.score
