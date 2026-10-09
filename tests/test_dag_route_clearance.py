"""Orthogonal gutter ports and separation between independent tracks."""
from mudyla.dag.solver.budget import LayoutBudget, OVERALL_BUDGET_SECONDS

import pytest

from mudyla.logging.formatters.layered import Direction, route_supplied_columns


@pytest.mark.parametrize('columns,edges', [
    ((0, 0), ((0, 1), (0, 1), (0, 1))),
    ((0, 2, 4, 0, 2, 4), tuple((source, target) for source in range(3) for target in range(3, 6))),
])
def test_simultaneous_dependency_tracks_have_a_blank_column_between_them(columns, edges):
    geometry = route_supplied_columns(columns, edges, LayoutBudget(OVERALL_BUDGET_SECONDS))
    for cut in range(len(columns) - 1):
        active = sorted(track.column for track in geometry.tracks if track.source <= cut < track.target)
        assert all(right - left >= 2 for left, right in zip(active, active[1:])), 'Independent tracks touch side by side'


def test_endpoint_arms_stay_off_action_marker_rows():
    geometry = route_supplied_columns((0, 4), ((0, 1),), LayoutBudget(OVERALL_BUDGET_SECONDS))
    for row in geometry.action_rows:
        assert all(not cell.directions & (Direction.LEFT | Direction.RIGHT) for cell in row), 'Action row contains a horizontal arm'
    assert any(cell.directions & (Direction.LEFT | Direction.RIGHT)
               for rows in geometry.connector_rows for row in rows for cell in row)


def test_straight_two_action_chain_has_one_connector_row():
    geometry = route_supplied_columns((0, 0), ((0, 1),), LayoutBudget(OVERALL_BUDGET_SECONDS))
    assert len(geometry.connector_rows) == 1 and len(geometry.connector_rows[0]) == 1
    assert len(geometry.connector_rows[0][0]) == 1
    assert geometry.connector_rows[0][0][0].directions == Direction.UP | Direction.DOWN


def test_unrelated_straight_continuations_need_no_connector_rows():
    geometry = route_supplied_columns((0, 2, 0, 2), ((0, 2), (1, 3)), LayoutBudget(OVERALL_BUDGET_SECONDS))
    assert tuple(map(len, geometry.connector_rows)) == (0, 0, 0)
    assert all(cell.directions == Direction.UP | Direction.DOWN
               for rank, row in enumerate(geometry.action_rows) for cell in row
               if cell.column != geometry.action_columns[rank])


@pytest.mark.parametrize('columns,edges,rows', [
    ((0, 4), ((0, 1),), (1,)),
    ((0, 0, 0), ((0, 1), (0, 2)), (2, 1)),
    ((0, 0), ((0, 1), (0, 1), (0, 1)), (3,)),
])
def test_endpoint_spacing_uses_the_smallest_unambiguous_representation(columns, edges, rows):
    geometry = route_supplied_columns(columns, edges, LayoutBudget(OVERALL_BUDGET_SECONDS))
    assert tuple(map(len, geometry.connector_rows)) == rows


def test_crossings_keep_a_straight_cell_before_an_adjacent_turn_or_junction():
    from mudyla.dag.solver.grid import assign_lanes
    endpoints = ((0, 2), (0, 3), (0, 5), (0, 6), (1, 3), (1, 5), (1, 6),
                 (2, 3), (2, 4), (2, 5), (2, 6), (3, 4), (3, 5), (3, 6), (4, 5), (4, 6), (5, 6))
    budget = LayoutBudget(OVERALL_BUDGET_SECONDS)
    budget.start()
    geometry = route_supplied_columns(assign_lanes(7, endpoints, budget), endpoints, budget)
    cells = {}
    y = 0
    for rank, action in enumerate(geometry.action_rows):
        for row in (action, *(geometry.connector_rows[rank] if rank < len(geometry.connector_rows) else ())):
            cells.update({(cell.column, y): cell for cell in row})
            y += 1
    straight = {Direction.UP | Direction.DOWN, Direction.LEFT | Direction.RIGHT}
    steps = {Direction.UP: (0, -1), Direction.DOWN: (0, 1), Direction.LEFT: (-1, 0), Direction.RIGHT: (1, 0)}
    for (x, y), cell in cells.items():
        if cell.crossing:
            for connection in cell.connections:
                for direction, (dx, dy) in steps.items():
                    if connection.directions & direction:
                        neighbor = cells[x + dx, y + dy]
                        owned = next(peer.directions for peer in neighbor.connections if peer.edge == connection.edge)
                        assert owned.bit_count() != 2 or owned in straight, 'Crossing touches an endpoint bend'
                        assert neighbor.crossing or neighbor.directions.bit_count() <= 2, 'Crossing touches a junction'


def test_opposing_endpoint_arms_have_a_straight_row_between_them():
    geometry = route_supplied_columns((0, 0), ((0, 1), (0, 1), (0, 1)), LayoutBudget(OVERALL_BUDGET_SECONDS))
    assert len(geometry.connector_rows[0]) == 3
    assert all(cell.directions == Direction.UP | Direction.DOWN for cell in geometry.connector_rows[0][1])
    assert all(len(route.points) - 2 <= 4 for route in geometry.routes)


@pytest.mark.parametrize('columns,parallel,rows', [((0, 4), False, 1), ((0, 0), True, 3)])
def test_adaptive_spacing_preserves_visible_dependency_strength(columns, parallel, rows):
    from dataclasses import replace
    from rich.text import Text
    from layout_graphs import fixture
    from mudyla.dag.display import build_display_edges
    from mudyla.dag.graph import ActionKey, Dependency
    from mudyla.logging.formatters import OutputFormatter
    from mudyla.logging.formatters.branches import BranchTheme
    from mudyla.logging.formatters.dag import build_dag_layout, execution_dag
    keys = [ActionKey.from_name(name) for name in ('source', 'goal')]

    def gutter(weak):
        dependencies = [(1, Dependency(keys[0], weak=weak, retainer_action=keys[0]))]
        if parallel:
            dependencies.extend([(1, Dependency(keys[0], weak=True)),
                                 (1, Dependency(keys[0], soft=True, retainer_action=keys[1]))])
        sample = fixture('adaptive-strength', keys, dependencies, [1], 180)
        display = build_display_edges(sample.graph, tuple(keys), full=True)
        geometry = route_supplied_columns(columns, ((0, 1),) * len(dependencies), LayoutBudget(OVERALL_BUDGET_SECONDS))
        assert len(geometry.connector_rows[0]) == rows
        layout = replace(build_dag_layout(sample.graph, keys, display=display), geometry=geometry)
        output = OutputFormatter(no_color=True, compact=True)
        dag = execution_dag(sample.graph, keys, output.context, False, {}, lambda key: Text('x'),
                            lambda key: 'dim', lambda: BranchTheme.DISABLED, layout=layout)
        return tuple(dag._gutter(row, ('dim',) * len(dag.edges), False, None).plain
                     for group in geometry.connector_rows for row in group)

    assert gutter(False) != gutter(True), 'Dependency strength has no visible straight stroke'


def test_short_merge_keeps_dependency_strength_visible():
    from io import StringIO
    from rich.console import Console
    from rich.text import Text
    from layout_graphs import nested_forks
    from mudyla.dag.display import build_display_edges
    from mudyla.dag.graph import Dependency
    from mudyla.logging.formatters import OutputFormatter
    from mudyla.logging.formatters.branches import BranchTheme
    from mudyla.logging.formatters.dag import build_dag_layout, execution_dag
    sample = nested_forks()
    keys = list(sample.order)
    source = next(key for key in keys if key.id.name == 'right-8')
    target = next(key for key in keys if key.id.name == 'merge-9')
    output = OutputFormatter(no_color=False, compact=True, console=Console(file=StringIO(), width=500, color_system='truecolor', no_color=False))

    def gutter():
        display = build_display_edges(sample.graph, tuple(keys), full=True)
        layout = build_dag_layout(sample.graph, keys, display=display)
        dag = execution_dag(sample.graph, keys, output.context, False, {}, lambda key: Text('x'),
                            lambda key: 'dim', lambda: BranchTheme.DARK, layout=layout)
        return tuple(str(dag._gutter(row, ('dim',) * len(dag.edges), False, None))
                     for rows in layout.geometry.connector_rows for row in rows)

    strong = gutter()
    dependencies = sample.graph.get_node(target).dependencies
    dependencies.remove(Dependency(source))
    dependencies.add(Dependency(source, weak=True))
    weak = gutter()
    assert strong != weak, 'Changing the short merge dependency to weak changes no visible stroke'
