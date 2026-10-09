"""Depth/barycenter layering with finite quadratic coordinate relaxation."""
import time
from .budget import LayoutBudget
from .base import DagSolver
from .geometry import NODE_GAP, stack_components
from .layered import emit_polylines, vertex_widths
from .layering import Topology, normalize, order_layers
from .model import SolverMode, SolverAttempt, SolverCandidate, SolverResult
from .score import score_native_layout
RELAXATION_PASSES = 32

def quadratic_coordinates(topology: Topology, layers: list[list[int]], widths: tuple[float, ...], budget: LayoutBudget) -> dict[int, float]:
    budget.check('quadratic_coordinates')
    x = {}
    for layer in layers:
        budget.check('quadratic_coordinates')
        offset = 0.0
        for vertex in layer:
            budget.check('quadratic_coordinates')
            x[vertex] = offset + widths[vertex] / 2
            offset += widths[vertex] + NODE_GAP
        for vertex in layer:
            budget.check('quadratic_coordinates')
            x[vertex] -= offset / 2
    for iteration in range(RELAXATION_PASSES):
        budget.check('quadratic_coordinates')
        moving = layers if iteration % 2 == 0 else list(reversed(layers))
        for layer in moving:
            budget.check('quadratic_coordinates')
            desired = {vertex: sum((x[neighbor] for neighbor in budget.iterate(topology.upper[vertex] + topology.lower[vertex], 'quadratic_coordinates'))) / len(topology.upper[vertex] + topology.lower[vertex]) if topology.upper[vertex] + topology.lower[vertex] else x[vertex] for vertex in budget.iterate(layer, 'quadratic_coordinates')}
            for index, vertex in enumerate(layer):
                budget.check('quadratic_coordinates')
                lower = x[layer[index - 1]] + (widths[layer[index - 1]] + widths[vertex]) / 2 + NODE_GAP if index else float('-inf')
                x[vertex] = max(lower, (x[vertex] + desired[vertex]) / 2)
            for index in reversed(range(len(layer) - 1)):
                budget.check('quadratic_coordinates')
                vertex, right = layer[index:index + 2]
                x[vertex] = min(x[vertex], x[right] - (widths[vertex] + widths[right]) / 2 - NODE_GAP)
    return x

class SugiyamaSolver(DagSolver):
    mode: SolverMode = 'sugiyama'

    def _solve(self) -> SolverResult:
        started = time.monotonic()
        components = []
        for indices in self.graph.components:
            self.budget.check('_solve')
            topology = normalize(self.graph, indices, False, self.budget)
            layers = order_layers(topology, True, self.budget)
            x = quadratic_coordinates(topology, layers, vertex_widths(self.graph, topology, self.budget), self.budget)
            components.append(emit_polylines(self.graph, indices, topology, x, (('ranker', 'dependency-depth'), ('ordering', 'barycenter'), ('coordinates', 'quadratic-relaxation'), ('relaxation_passes', str(RELAXATION_PASSES))), 'Sugiyama heuristic variant', self.budget))
        geometry = stack_components(tuple(components), self.budget)
        score = score_native_layout(geometry, self.graph.edges, self.budget)
        elapsed = time.monotonic() - started
        attempt = SolverAttempt('sugiyama', elapsed, self.budget.seconds, False, True, False, 'complete')
        candidate = SolverCandidate('sugiyama', geometry, (attempt,), elapsed * 1000, score, None, None)
        return SolverResult(self.graph, geometry, 'sugiyama', 'sugiyama', (attempt,), elapsed * 1000, (candidate,), None, False)
