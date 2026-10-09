"""Continue one owned Grid search through its effort checkpoints."""

from dataclasses import replace

from .base import DagSolver
from .grid import GRID_EFFORT_LIMITS, GridSearch
from .model import SolverCandidate, SolverMode, SolverResult


class AutoSolver(DagSolver):
    mode: SolverMode = 'auto'

    def __init__(self, search: GridSearch) -> None:
        super().__init__(search.graph, search.budget, search.objective)
        self.search = search

    def _solve(self) -> SolverResult:
        candidates: list[SolverCandidate] = []
        for mode, limit in GRID_EFFORT_LIMITS:
            candidate = self.search.advance(limit)
            progress = candidate.progress
            assert progress is not None
            if limit is None or progress.stop_reason != 'effort_limit':
                candidates.append(replace(candidate, mode='grid-opt'))
                break
            candidates.append(replace(candidate, mode=mode))
        chosen = candidates[-1]
        assert chosen.solve_ms is not None
        return SolverResult(self.graph, chosen.components, self.mode, 'grid-opt', chosen.attempts,
                            chosen.solve_ms, tuple(candidates), chosen.progress, False)
