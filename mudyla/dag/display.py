"""Select a reachability-equivalent ordering view of a final pruned graph."""

from .graph import ActionGraph, ActionKey
from .solver.model import DagEdge, DisplayEdges
from .solver.budget import LayoutBudget


def build_display_edges(graph: ActionGraph, execution_order: tuple[ActionKey, ...], *, full: bool,
                        budget: LayoutBudget | None = None) -> DisplayEdges:
    positions = {key: index for index, key in enumerate(execution_order)}
    assert len(positions) == len(execution_order), 'Display action keys must be unique'
    declarations = []
    for target_key in execution_order:
        for dependency in graph.get_node(target_key).dependencies:
            if budget is not None:
                budget.check('display_edges')
            if dependency.action in positions:
                declarations.append(DagEdge(dependency.action, target_key, dependency))
    edges = tuple(sorted(declarations,
                        key=lambda edge: (positions[edge.source], positions[edge.target],
                            {'strong': 0, 'weak': 1, 'soft': 2}[edge.kind], repr(edge.dependency.retainer_action))))
    assert all(positions[edge.source] < positions[edge.target] for edge in edges), 'Display requires topological execution order'
    if full:
        return DisplayEdges(edges, tuple(range(len(edges))))
    outgoing: list[set[int]] = [set() for _ in execution_order]
    for edge in edges:
        if budget is not None:
            budget.check('display_edges')
        outgoing[positions[edge.source]].add(positions[edge.target])
    reachable = [0] * len(execution_order)
    retained: set[tuple[int, int]] = set()
    for source in reversed(range(len(execution_order))):
        for target in sorted(outgoing[source]):
            if budget is not None:
                budget.check('display_reduction')
            if not reachable[source] & (1 << target):
                retained.add((source, target))
            reachable[source] |= (1 << target) | reachable[target]
    visible = tuple(index for index, edge in enumerate(edges)
                    if (positions[edge.source], positions[edge.target]) in retained)
    return DisplayEdges(edges, visible)
