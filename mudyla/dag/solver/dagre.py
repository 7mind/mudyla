"""Longest-path ranks, weighted-median ordering and corrected BK compaction."""
import time
from .base import DagSolver
from .coordinates import bk_coordinates
from .geometry import stack_components
from .layered import emit_polylines, vertex_widths
from .layering import normalize, order_layers
from .model import SolverMode, SolverAttempt, SolverCandidate, SolverResult
from .score import score_native_layout

class DagreSolver(DagSolver):
    mode: SolverMode = 'dagre'

    def _solve(self) -> SolverResult:
        started = time.monotonic()
        components = []
        for indices in self.graph.components:
            self.budget.check('_solve')
            topology = normalize(self.graph, indices, True, self.budget)
            layers = order_layers(topology, False, self.budget)
            x = bk_coordinates(topology, layers, vertex_widths(self.graph, topology, self.budget), self.budget)
            components.append(emit_polylines(self.graph, indices, topology, x, (('ranker', 'longest-path'), ('ordering', 'weighted-median/transpose'), ('coordinates', 'corrected-BK')), 'Dagre layered variant', self.budget))
        geometry = stack_components(tuple(components), self.budget)
        score = score_native_layout(geometry, self.graph.edges, self.budget)
        elapsed = time.monotonic() - started
        attempt = SolverAttempt('dagre', elapsed, self.budget.seconds, False, True, False, 'complete')
        candidate = SolverCandidate('dagre', geometry, (attempt,), elapsed * 1000, score, None, None)
        return SolverResult(self.graph, geometry, 'dagre', 'dagre', (attempt,), elapsed * 1000, (candidate,), None, False)
