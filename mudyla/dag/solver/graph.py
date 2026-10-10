"""Freeze full-key inputs and stable connected-component presentation order."""
from collections.abc import Mapping
import math
from .budget import LayoutBudget
from ..graph import ActionGraph, ActionKey
from .model import DisplayEdges, NodeSize, SolverInput

def build_solver_input(graph: ActionGraph, execution_order: list[ActionKey], node_sizes: Mapping[ActionKey, NodeSize],
                       *, display: DisplayEdges, budget: LayoutBudget | None = None) -> SolverInput:
    original = tuple(execution_order)
    positions = {key: index for index, key in enumerate(original)}
    assert len(positions) == len(original), 'Plan action keys must be unique'
    assert set(node_sizes) == set(original), 'Measured node sizes must match execution keys'
    sizes = tuple((node_sizes[key] for key in original))
    assert all((math.isfinite(size.width) and math.isfinite(size.height) and (size.width > 0) and (size.height > 0) for size in sizes)), 'Measured node dimensions must be finite and positive'
    assert all(edge.source in positions and edge.target in positions
               and edge.dependency in graph.get_node(edge.target).dependencies for edge in display.original)
    edges = display.visible
    assert all((positions[edge.source] < positions[edge.target] for edge in edges)), 'Solver input requires topological execution order'
    neighbors: dict[ActionKey, set[ActionKey]] = {key: set() for key in original}
    for edge in edges:
        if budget is not None:
            budget.check('solver_input')
        neighbors[edge.source].add(edge.target)
        neighbors[edge.target].add(edge.source)
    unseen = set(original)
    components = []
    for first in original:
        if budget is not None:
            budget.check('solver_input')
        if first not in unseen:
            continue
        pending = [first]
        connected: set[ActionKey] = set()
        while pending:
            if budget is not None:
                budget.check('solver_input')
            key = pending.pop()
            if key in connected:
                continue
            connected.add(key)
            pending.extend(neighbors[key] - connected)
        unseen.difference_update(connected)
        components.append(tuple(sorted(positions[key] for key in connected)))
    presentation = tuple((original[index] for component in components for index in component))
    return SolverInput(original, presentation, display, tuple(components), sizes)

def component_edges(graph: SolverInput, indices: tuple[int, ...], budget: LayoutBudget) -> tuple[tuple[int, int, int], ...]:
    budget.check('component_edges')
    positions = {graph.execution_order[index]: rank for rank, index in budget.iterate(enumerate(indices), 'component_edges')}
    return tuple(((edge_id, positions[edge.source], positions[edge.target]) for edge_id, edge in budget.iterate(enumerate(graph.edges), 'component_edges') if edge.source in positions))
