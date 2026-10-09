"""Project cached native node placement into the established action-row gutter."""

from ...dag.solver.budget import LayoutBudget
from ...dag.solver.model import Component, LayoutScore, SolverCandidate, SolverInput, SolverResult
from .dag import NativeDagLayout
from .layered import LayeredLayout, plan_supplied_columns, rasterize_plan


_COLUMN_SPACING = 2


class RowProjectionObjective:
    def __init__(self, graph: SolverInput) -> None:
        self.graph = graph
        self._positions = {key: index for index, key in enumerate(graph.presentation_order)}
        self._endpoints = tuple((self._positions[edge.source], self._positions[edge.target]) for edge in graph.edges)
        self._columns_by_components: dict[tuple[Component, ...], tuple[int, ...]] = {}
        self._layouts: dict[tuple[int, ...], tuple[LayeredLayout, LayoutScore]] = {}

    def _columns(self, components: tuple[Component, ...], budget: LayoutBudget) -> tuple[int, ...]:
        columns = [0] * len(self.graph.presentation_order)
        for component in budget.iterate(components, 'row_columns'):
            ordered_x = sorted({node.center.x for node in budget.iterate(component.nodes, 'row_columns')})
            lanes = {x: _COLUMN_SPACING * index for index, x in enumerate(ordered_x)}
            for node in budget.iterate(component.nodes, 'row_columns'):
                columns[self._positions[self.graph.execution_order[node.index]]] = lanes[node.center.x]
        return tuple(columns)

    def score(self, candidate: SolverCandidate, budget: LayoutBudget) -> LayoutScore:
        assert candidate.score is not None
        columns = self._columns_by_components.get(candidate.components)
        if columns is None:
            columns = self._columns(candidate.components, budget)
            if columns not in self._layouts:
                plan = plan_supplied_columns(columns, self._endpoints, budget)
                geometry = rasterize_plan(plan, budget)
                # Distinct tracks and separate endpoint ports exclude unrelated overlaps.
                score = LayoutScore(0, geometry.crossings, plan.width * plan.height / max(1, len(columns)))
                self._layouts[columns] = geometry, score
            self._columns_by_components[candidate.components] = columns
        return self._layouts[columns][1]

    def geometry(self, result: SolverResult) -> LayeredLayout:
        assert result.components in self._columns_by_components, "Selected projection must be complete before publication"
        return self._layouts[self._columns_by_components[result.components]][0]


def build_native_row_layout(result: SolverResult, *, projection: RowProjectionObjective,
                            preparation_ms: float) -> NativeDagLayout:
    assert projection.graph is result.graph
    return NativeDagLayout(result.keys, result.graph.display, projection.geometry(result), result.execution_order, result,
                           preparation_ms)
