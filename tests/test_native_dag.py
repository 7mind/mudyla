"""Native solver ownership, exact search and semantic terminal projection."""
from io import BytesIO, StringIO, TextIOWrapper
import os
from pathlib import Path
import subprocess
import sys
import pytest
from rich.console import Console
from rich.text import Text
from mudyla.cli import CLI
from mudyla.dag.context import ContextId
from mudyla.dag.display import build_display_edges
from mudyla.dag.graph import ActionKey, Dependency
from mudyla.dag.solver.budget import LayoutBudget, OVERALL_BUDGET_SECONDS
from mudyla.dag.solver.factory import create_solver
from mudyla.dag.solver.objective import NativeObjective
from mudyla.dag.solver.graph import build_solver_input
from mudyla.dag.solver.model import Component, DagEdge, Node, Point, Route, NodeSize
from mudyla.logging.formatters import OutputFormatter
from mudyla.logging.formatters.native_dag import RowProjectionObjective
from mudyla.logging.formatters import native_dag as native
from mudyla.logging.formatters.branches import BranchTheme
from mudyla.logging.formatters.dag import build_dag_layout, execution_dag
from mudyla.logging.formatters.layered import Direction, route_supplied_columns
from mudyla.logging.formatters.details import context_label
from mudyla.logging.action_logger_pure import ActionLoggerPure
from mudyla.logging.action_logger_table import TaskStatus
from layout_graphs import fixture, layered_kinds, nested_forks, repeated_diamonds

def started_budget(seconds):
    budget = LayoutBudget(seconds)
    budget.start()
    return budget


def fixed_node_sizes(keys):
    return {key: NodeSize(180, 42) for key in keys}


def row_projection(result):
    projection = RowProjectionObjective(result.graph)
    candidate = next(candidate for candidate in result.candidates if candidate.components is result.components)
    projection.score(candidate, started_budget(5))
    return projection

def test_native_action_rows_preserve_existing_runtime_columns_and_viewport():
    names = ('setup-env', 'setup-jvm-options', 'setup-scala', 'build',
             'setup-scala-publish', 'scala-publish-local', 'scala-local')
    keys = [ActionKey.from_name(name) for name in names]
    endpoints = ((0, 2), (0, 3), (0, 5), (0, 6), (1, 3), (1, 5), (1, 6),
                 (2, 3), (2, 4), (2, 5), (2, 6), (3, 4), (3, 5), (3, 6), (4, 5), (4, 6), (5, 6))
    sample = fixture('native-action-rows', keys,
        [(target, Dependency(keys[source], weak=target == 3)) for source, target in endpoints], [6], 180)
    output = OutputFormatter(no_color=True, compact=True, console=Console(file=StringIO(), width=180, height=30, force_terminal=True))
    sizes = {key: NodeSize(1, 1) for key in keys}
    result = create_solver('auto', build_solver_input(sample.graph, keys, sizes, display=build_display_edges(sample.graph, tuple(keys), full=True)), objective=NativeObjective(), budget=LayoutBudget(OVERALL_BUDGET_SECONDS)).solve()
    logger = ActionLoggerPure(keys, output, True, graph=sample.graph, dag_layout=native.build_native_row_layout(result, projection=row_projection(result), preparation_ms=(result).solve_ms))
    for index, key in enumerate(keys):
        logger.tasks[key].status = TaskStatus.DONE
        logger.tasks[key].duration = index + 1.25
        logger.tasks[key].latest = f'OUTPUT_{index}'
    rows, anchors = logger._action_lines()
    text = [''.join(segment.text for segment in row) for row in rows]
    assert not any(character in ''.join(text) for character in '┌┐└┘'), 'Native layout replaced action rows with cards'
    for index, key in enumerate(keys):
        row = text[anchors[key]]
        assert key.id.name in row and '@global' in row
        assert f'{index + 1.25:.1f}s' in row and f'OUTPUT_{index}' in row
    assert len(rows) <= logger._get_content_height(), 'Seven action rows exceed the ordinary viewport'


@pytest.mark.parametrize('name,encoding,context,shared_count', [
    ('漢字漢字漢字', 'utf-8', ContextId.empty(), 1),
    ('e\u0301-identify', 'ascii', ContextId.empty(), 1),
    ('shared', 'cp1252', ContextId.from_dict({'platform': 'windows'}), 3),
    ('e\u0301' * 80, 'utf-8', ContextId.empty(), 1),
])
def test_native_action_labels_preserve_encoded_contexts_and_goal(name, encoding, context, shared_count):
    key = ActionKey.from_name(name, context)
    sample = fixture('native-label', [key], [], [0], 180)
    output = OutputFormatter(no_color=True, compact=True)
    shared = {key: shared_count}
    result = create_solver('dagre', build_solver_input(sample.graph, [key], {key: NodeSize(1, 1)}, display=build_display_edges(sample.graph, tuple([key]), full=True)), objective=NativeObjective(), budget=LayoutBudget(OVERALL_BUDGET_SECONDS)).solve()
    dag = execution_dag(sample.graph, [key], output.context, False, shared,
                        lambda key: Text('o '), lambda key: 'dim', lambda: BranchTheme.DISABLED, layout=native.build_native_row_layout(result, projection=row_projection(result), preparation_ms=(result).solve_ms))
    name_part, annotation = dag.label_parts(key)
    expected = (name_part + Text(' ') + annotation).plain.encode(encoding, errors='replace').decode(encoding)
    buffer = BytesIO()
    stream = TextIOWrapper(buffer, encoding=encoding, errors='replace', newline='\n')
    console = Console(file=stream, width=180, no_color=True)
    console.print(dag)
    stream.flush()
    assert expected in buffer.getvalue().decode(encoding)
    stream.close()


@pytest.mark.parametrize('first_name', ['構築部署検査', 'e\u0301' * 12])
def test_parallel_native_action_rows_preserve_cell_aligned_labels(first_name):
    keys = [ActionKey.from_name(name, ContextId.from_dict({'platform': 'linux'})) for name in (first_name, 'check', 'finish')]
    sample = fixture('parallel-native-labels', keys, [(2, Dependency(keys[0])), (2, Dependency(keys[1]))], [2], 180)
    output = OutputFormatter(no_color=True, compact=True)
    result = create_solver('dagre', build_solver_input(sample.graph, keys, {key: NodeSize(1, 1) for key in keys}, display=build_display_edges(sample.graph, tuple(keys), full=True)), objective=NativeObjective(), budget=LayoutBudget(OVERALL_BUDGET_SECONDS)).solve()
    dag = execution_dag(sample.graph, keys, output.context, False, {},
                        lambda key: Text('o '), lambda key: 'dim', lambda: BranchTheme.DISABLED, layout=native.build_native_row_layout(result, projection=row_projection(result), preparation_ms=(result).solve_ms))
    console = Console(file=StringIO(), width=180)
    lines, anchors = dag.visual_lines(console, console.options,
        lambda key, width: Text(' ').join(dag.label_parts(key)))
    for key in keys:
        row = ''.join(segment.text for segment in lines[anchors[key].start])
        assert Text(' ').join(dag.label_parts(key)).plain in row
        assert sum(segment.cell_length for segment in lines[anchors[key].start]) <= console.width


@pytest.mark.parametrize('mode', ['grid-low', 'grid-medium', 'grid-high', 'grid-opt', 'dagre', 'elk', 'sugiyama', 'auto'])
def test_native_selector_and_cached_solution(mode):
    public_mode = 'grid-auto' if mode == 'auto' else mode
    args = CLI().parser.parse_args(['--plan-dag-solver', public_mode])
    assert args.plan_dag_solver == public_mode
    keys = [ActionKey.from_name(name) for name in ('source', 'left', 'right', 'goal')]
    sample = fixture('native-diamond', keys, [(1, Dependency(keys[0])), (2, Dependency(keys[0])), (3, Dependency(keys[1])), (3, Dependency(keys[2], weak=True))], [3], 120)
    graph = build_solver_input(sample.graph, keys, fixed_node_sizes(keys), display=build_display_edges(sample.graph, tuple(keys), full=True))
    solver = create_solver(mode, graph, objective=NativeObjective(), budget=LayoutBudget(OVERALL_BUDGET_SECONDS))
    result = solver.solve()
    assert solver.solve() is result and result.graph is graph
    assert sorted((route.edge for component in result.components for route in component.routes)) == list(range(4))
    assert result.execution_order == tuple(keys)

def test_default_solver_is_resolved_only_at_argument_normalization():
    cli = CLI()
    args = cli.parser.parse_args([])
    assert args.plan_dag_solver is None
    cli._apply_platform_defaults(args, quiet_mode=True)
    assert args.plan_style == 'dag' and args.plan_dag_solver == 'grid-auto'

def test_default_dependency_crossings_use_cuts_without_bridge_glyphs():
    keys = [ActionKey.from_name(name) for name in ('a', 'b', 'c', 'x', 'y', 'z')]
    sample = fixture('nonplanar-default', keys,
        [(target, Dependency(keys[source])) for source in range(3) for target in range(3, 6)], [3, 4, 5], 120)
    graph = sample.graph
    output = OutputFormatter(no_color=True, compact=True)
    layout = build_dag_layout(graph, keys, display=build_display_edges(graph, tuple(keys), full=True))
    assert layout.geometry.crossings > 0
    dag = execution_dag(graph, keys, output.context, False, {}, lambda key: Text('o '),
                        lambda key: 'dim', lambda: BranchTheme.DISABLED, layout=layout)
    stream = StringIO()
    Console(file=stream, width=120, no_color=True).print(dag)
    assert '╪' not in stream.getvalue(), 'Default crossings still use bridge glyphs'


def test_native_dagre_actual_cli_preserves_action_labels(tmp_path):
    (tmp_path / '.git').mkdir()
    definitions = tmp_path / '.mdl' / 'defs'
    definitions.mkdir(parents=True)
    (definitions / 'actions.md').write_text('''# action: source

```python
pass
```

# action: goal

```python
mdl.dep("action.source")
pass
```
''', encoding='utf-8')
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run([sys.executable, '-m', 'mudyla', '--without-nix', '--dry-run', '--plan-dag-solver', 'dagre', ':goal'],
        cwd=tmp_path, env=dict(os.environ, PYTHONPATH=str(root), PYTHONIOENCODING='utf-8', COLUMNS='180'),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', timeout=10)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'source (@global)' in completed.stdout and 'goal (@global; goal)' in completed.stdout


def test_narrow_soft_references_preserve_contexts_and_retainer_identities():
    keys = [ActionKey.from_name('work', ContextId.from_dict({'platform': value})) for value in ('linux', 'windows')]
    retainers = [ActionKey.from_name('keep', ContextId.from_dict({'mode': value})) for value in ('a', 'b')]
    sample = fixture('native-retainers', keys, [(1, Dependency(keys[0], soft=True, retainer_action=key)) for key in retainers], [1], 9)
    graph = build_solver_input(sample.graph, keys, fixed_node_sizes(keys), display=build_display_edges(sample.graph, tuple(keys), full=True))
    result = create_solver('grid-low', graph, objective=NativeObjective(), budget=LayoutBudget(OVERALL_BUDGET_SECONDS)).solve()
    output = OutputFormatter(no_color=True, compact=True)
    dag = execution_dag(sample.graph, list(sample.order), output.context, False, {}, lambda key: Text('o '), lambda key: 'dim', lambda: BranchTheme.DISABLED, layout=native.build_native_row_layout(result, projection=row_projection(result), preparation_ms=(result).solve_ms))
    stream = StringIO()
    Console(file=stream, width=9, no_color=True).print(dag)
    compact = ''.join(stream.getvalue().split())
    assert compact.count('retainer:keep') == 2
    for key in (*keys, *retainers):
        identity = ''.join(context_label(key.context_id, output.context, False).plain.split())
        assert identity in compact

def score_control(paths, endpoints):
    keys = tuple((ActionKey.from_name(f'key{index}') for index in range(4)))
    edges = tuple((DagEdge(keys[source], keys[target], Dependency(keys[source])) for source, target in endpoints))
    centers = {index: (50 + index * 10, 50) for index in range(4)}
    for path, (source, target) in zip(paths, endpoints):
        centers[source], centers[target] = (path[0], path[-1])
    nodes = tuple((Node(index, Point(*centers[index]), 0.2, 0.2) for index in range(4)))
    routes = tuple((Route(index, tuple((Point(*point) for point in path)), (index,)) for index, path in enumerate(paths)))
    component = Component(tuple(range(4)), nodes, routes, 100, 100, 0, (), 'control')
    return (component, edges)

@pytest.mark.parametrize('paths,endpoints,expected', [((((0, 5), (10, 5)), ((5, 0), (5, 10))), ((0, 1), (2, 3)), (0, 1)), ((((0, 5), (10, 5)), ((2, 5), (4, 5), (8, 5))), ((0, 1), (2, 3)), (1, 0)), ((((0, 0), (5, 0), (5, 10)), ((0, 0), (5, 0), (10, 0))), ((0, 1), (0, 2)), (0, 0)), ((((0, 0), (0, 5), (5, 5)), ((0, 0), (2, 0), (2, 5), (5, 5))), ((0, 1), (0, 2)), (1, 0))])
def test_native_score_distinguishes_overlaps_crossings_and_real_shared_prefixes(paths, endpoints, expected):
    from mudyla.dag.solver.score import score_native_layout
    component, edges = score_control(paths, endpoints)
    score = score_native_layout((component,), edges, started_budget(5))
    assert (score.overlaps, score.crossings) == expected

def test_native_score_ranks_overlaps_before_crossings_and_area():
    from mudyla.dag.solver.model import LayoutScore
    assert LayoutScore(0, 3, 100) < LayoutScore(1, 0, 1)
    assert LayoutScore(0, 1, 100) < LayoutScore(0, 2, 1)
    assert LayoutScore(0, 1, 10) < LayoutScore(0, 1, 11)

def segment_enters_node(first, last, node):
    low, high = (0.0, 1.0)
    for start, end, center, size in ((first.x, last.x, node.center.x, node.width), (first.y, last.y, node.center.y, node.height)):
        minimum, maximum = (center - size / 2, center + size / 2)
        delta = end - start
        if delta == 0:
            if not minimum < start < maximum:
                return False
        else:
            entry, exit = sorted(((minimum - start) / delta, (maximum - start) / delta))
            low, high = (max(low, entry), min(high, exit))
            if high <= low:
                return False
    return True

@pytest.mark.parametrize('mode', ['dagre', 'sugiyama'])
def test_layered_routes_avoid_foreign_node_bodies(mode):
    keys = [ActionKey.from_name(f'node{index}') for index in range(7)]
    endpoints = ((0, 2), (0, 3), (0, 5), (0, 6), (1, 3), (1, 5), (1, 6), (2, 3), (2, 4), (2, 5), (2, 6), (3, 4), (3, 5), (3, 6), (4, 5), (4, 6), (5, 6))
    sample = fixture('layered-node-clearance', keys, [(target, Dependency(keys[source])) for source, target in endpoints], [6], 160)
    graph = build_solver_input(sample.graph, keys, fixed_node_sizes(keys), display=build_display_edges(sample.graph, tuple(keys), full=True))
    result = create_solver(mode, graph, objective=NativeObjective(), budget=LayoutBudget(OVERALL_BUDGET_SECONDS)).solve()
    for component in result.components:
        for route in component.routes:
            edge = graph.edges[route.edge]
            for node in component.nodes:
                if graph.execution_order[node.index] in (edge.source, edge.target):
                    continue
                for first, last in zip(route.points, route.points[1:]):
                    assert not segment_enters_node(first, last, node), (mode, route.edge, node.index, first, last)


def test_components_preserve_scheduler_order_full_key_anchors_and_blank_separator():
    from mudyla.logging.formatters.dag import execution_dag
    keys = [ActionKey.from_name(name, ContextId.from_dict({'component': component})) for name, component in (('source', 'a'), ('source', 'c'), ('goal', 'a'), ('goal', 'c'))]
    sample = fixture('native-components', keys, [(2, Dependency(keys[0])), (3, Dependency(keys[1]))], [2, 3], 160)
    graph = build_solver_input(sample.graph, keys, fixed_node_sizes(keys), display=build_display_edges(sample.graph, tuple(keys), full=True))
    assert graph.execution_order == tuple(keys)
    assert graph.presentation_order == (keys[0], keys[2], keys[1], keys[3])
    result = create_solver('dagre', graph, objective=NativeObjective(), budget=LayoutBudget(OVERALL_BUDGET_SECONDS)).solve()
    output = OutputFormatter(no_color=True, compact=True)
    dag = execution_dag(sample.graph, keys, output.context, False, {}, lambda key: Text('o '), lambda key: 'dim', lambda: BranchTheme.DISABLED, layout=native.build_native_row_layout(result, projection=row_projection(result), preparation_ms=(result).solve_ms))
    with pytest.raises(AssertionError, match='scheduler order'):
        execution_dag(sample.graph, list(result.keys), output.context, False, {}, lambda key: Text(), lambda key: 'dim', lambda: BranchTheme.DISABLED, layout=native.build_native_row_layout(result, projection=row_projection(result), preparation_ms=(result).solve_ms))
    for width in (160, 9):
        console = Console(file=StringIO(), width=width)
        lines, anchors = dag.visual_lines(console, console.options, lambda key, available: Text(key.id.name))
        assert list(anchors) == list(graph.presentation_order)
        assert any(not ''.join(segment.text for segment in row).strip() for row in lines)
        assert set(anchors) == set(keys)
    assert graph.execution_order == tuple(keys)

@pytest.mark.parametrize('columns,endpoints', [
    ((0, 2, 4, 2, 0), ((0, 4), (1, 3), (2, 3), (2, 4))),
    ((4, 0, 2, 6), ((0, 2), (1, 2), (2, 3))),
    ((0, 0), ((0, 1), (0, 1), (0, 1))),
])
def test_native_row_routes_keep_all_edge_ids_orthogonal_ports_and_private_tracks(columns, endpoints):
    geometry = route_supplied_columns(columns, endpoints, LayoutBudget(OVERALL_BUDGET_SECONDS))
    expected = {}
    for edge, route in enumerate(geometry.routes):
        assert route.source == endpoints[edge][0] and route.target == endpoints[edge][1]
        assert len(route.points) <= 6
        for first, last in zip(route.points, route.points[1:]):
            dx = (last[0] > first[0]) - (last[0] < first[0])
            dy = (last[1] > first[1]) - (last[1] < first[1])
            assert (dx == 0) != (dy == 0)
            forward = Direction.RIGHT if dx > 0 else Direction.LEFT if dx < 0 else Direction.DOWN
            backward = Direction.LEFT if dx > 0 else Direction.RIGHT if dx < 0 else Direction.UP
            for step in range(abs(last[0] - first[0]) + abs(last[1] - first[1])):
                a = first[0] + dx * step, first[1] + dy * step
                b = a[0] + dx, a[1] + dy
                for point, direction in ((a, forward), (b, backward)):
                    expected.setdefault(point, {})[edge] = expected.get(point, {}).get(edge, Direction(0)) | direction
    actual = {}
    y = 0
    for rank, row in enumerate(geometry.action_rows):
        for cells in (row, *(geometry.connector_rows[rank] if rank < len(geometry.connector_rows) else ())):
            for cell in cells:
                actual[cell.column, y] = {connection.edge: connection.directions for connection in cell.connections}
            y += 1
    assert actual == expected
    vertical = Direction.UP | Direction.DOWN
    horizontal = Direction.LEFT | Direction.RIGHT
    for track in geometry.tracks:
        assert any((connections.get(track.edge) == vertical
                    and all(value == horizontal for owner, value in connections.items() if owner != track.edge))
                   or (connections.get(track.edge) == horizontal and len(connections) == 1)
                   for connections in actual.values())


@pytest.mark.parametrize('mode,build_fixture', [('dagre', nested_forks), ('sugiyama', repeated_diamonds), ('elk', layered_kinds)])
def test_native_complex_geometry_preserves_all_edges_and_node_clearance(mode, build_fixture):
    sample = build_fixture()
    graph = build_solver_input(sample.graph, list(sample.order), fixed_node_sizes(sample.order), display=build_display_edges(sample.graph, tuple(list(sample.order)), full=True))
    result = create_solver(mode, graph, objective=NativeObjective(), budget=LayoutBudget(OVERALL_BUDGET_SECONDS)).solve()
    assert sorted((route.edge for component in result.components for route in component.routes)) == list(range(len(graph.edges)))
    for component in result.components:
        for index, node in enumerate(component.nodes):
            for other in component.nodes[index + 1:]:
                assert abs(node.center.x - other.center.x) >= (node.width + other.width) / 2 or abs(node.center.y - other.center.y) >= (node.height + other.height) / 2
        for route in component.routes:
            edge = graph.edges[route.edge]
            for node in component.nodes:
                if graph.execution_order[node.index] not in (edge.source, edge.target):
                    assert not any((segment_enters_node(first, last, node) for first, last in zip(route.points, route.points[1:])))
