"""State-owning solver interface with one immutable result per input."""
from abc import ABC, abstractmethod
from dataclasses import replace
import time
from .budget import BudgetExpired, LayoutBudget
from .model import SolverAttempt, SolverInput, SolverMode, SolverResult
from .objective import CandidateObjective

class SolverFailure(ValueError):

    def __init__(self, message: str, attempts: tuple[SolverAttempt, ...]) -> None:
        super().__init__(message)
        self.attempts = attempts

class OverallTimeout(SolverFailure):

    def __init__(self, phase: str, attempts: tuple[SolverAttempt, ...]) -> None:
        super().__init__(f'Overall layout budget expired during {phase}', attempts)
        self.phase = phase

class DagSolver(ABC):
    mode: SolverMode

    def __init__(self, graph: SolverInput, budget: LayoutBudget, objective: CandidateObjective) -> None:
        self.graph = graph
        self.budget = budget
        self.objective = objective
        self._result: SolverResult | None = None

    def solve(self) -> SolverResult:
        if self._result is None:
            self.budget.start()
            started = time.monotonic()
            result = None
            try:
                self.budget.check(self.mode)
                result = self._solve()
                if result.candidates[0].selection_score is None:
                    candidate = result.candidates[0]
                    selection_score = self.objective.score(candidate, self.budget)
                    self.budget.check('solver_commit')
                    candidate = replace(candidate, selection_score=selection_score)
                    result = replace(result, candidates=(candidate,))
            except BudgetExpired as error:
                attempt = SolverAttempt(self.mode, time.monotonic() - started, self.budget.seconds, True, False, False, error.phase)
                raise OverallTimeout(error.phase, result.attempts if result is not None else (attempt,)) from error
            assert result.graph is self.graph, 'Solver result must retain its input'
            self._result = replace(result, solve_ms=(time.monotonic() - started) * 1000)
        return self._result

    @abstractmethod
    def _solve(self) -> SolverResult:
        raise NotImplementedError
