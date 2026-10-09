"""Branch hue identity and execution-state intensity."""

from tests.logger_fixtures import prepared_logger

from io import StringIO

from rich.console import Console
from rich.style import Style

from mudyla.dag.graph import ActionKey, Dependency
from mudyla.logging.terminal_logger_pure import PureTerminalLogger
from mudyla.logging.terminal_logger_table import TaskStatus
from mudyla.logging.formatters import OutputFormatter
from mudyla.logging.terminal_background import BackgroundProbe
from layout_graphs import fixture


def branch_logger():
    keys = [ActionKey.from_name(name) for name in ('root', 'a', 'b', 'a2', 'b2', 'merge', 'goal')]
    endpoints = ((0, 1), (1, 3), (3, 5), (0, 2), (2, 4), (4, 5), (5, 6))
    sample = fixture('branch-colors', keys,
        [(target, Dependency(keys[source])) for source, target in endpoints], [6], 120)
    output = OutputFormatter(no_color=False, compact=True, console=Console(file=StringIO(), width=120, height=30, force_terminal=True,
                              color_system='truecolor', no_color=False))
    logger = prepared_logger(PureTerminalLogger, keys, output, True, graph=sample.graph)
    logger._background_probe = BackgroundProbe(0)
    logger._background_probe.background = (20, 20, 20)
    return logger, keys


def edge_style(logger, source, target):
    dag = logger._dag
    edge = next(index for index, value in enumerate(dag.edges) if value.source == source and value.target == target)
    styles = tuple(dag.edge_style(value.target) for value in dag.edges)
    from mudyla.logging.formatters.layered import Direction
    rows = (*dag.layout.geometry.action_rows,
            *(row for connectors in dag.layout.geometry.connector_rows for row in connectors))
    for row in rows:
        for cell in row:
            if tuple(connection.edge for connection in cell.connections) == (edge,) and cell.directions in {
                    Direction.UP | Direction.DOWN, Direction.LEFT | Direction.RIGHT}:
                gutter = dag._gutter(row, styles, False, None)
                return gutter.get_style_at_offset(logger.console, cell.column)
    raise AssertionError('Dependency has no private visible cell')


def test_ready_dependencies_use_normal_intensity():
    logger, keys = branch_logger()
    logger.tasks[keys[0]].status = TaskStatus.DONE
    assert logger._tree_status(keys[1]).plain.strip() == '○'
    assert Style.parse(logger._plan_edge_style(keys[1])).dim is False
    assert Style.parse(logger._plan_edge_style(keys[3])).dim is True


def test_chain_inherits_hue_and_forks_and_merges_start_distinct_segments():
    logger, keys = branch_logger()
    for task in logger.tasks.values():
        task.status = TaskStatus.DONE
    first = edge_style(logger, keys[0], keys[1]).color
    second = edge_style(logger, keys[0], keys[2]).color
    merged = edge_style(logger, keys[5], keys[6]).color
    assert first is not None and second is not None and merged is not None
    assert len({first, second, merged}) == 3, 'All branches use the task-status hue'
    assert edge_style(logger, keys[1], keys[3]).color == first
    assert edge_style(logger, keys[3], keys[5]).color == first
    assert edge_style(logger, keys[2], keys[4]).color == second
    assert edge_style(logger, keys[4], keys[5]).color == second


def test_full_key_branch_identity_survives_colliding_display_strings_and_edge_order():
    from mudyla.dag.context import ContextId
    from mudyla.dag.solver.model import DagEdge
    from mudyla.logging.formatters.branches import branch_segments
    root = ActionKey.from_name('root')
    scalar = ActionKey.from_name('work', ContextId.from_dict({}, args={'value': 'a,b'}))
    multiple = ActionKey.from_name('work', ContextId.from_dict({}, args={'value': ('a', 'b')}))
    assert scalar != multiple and str(scalar) == str(multiple)
    edges = tuple(DagEdge(root, target, Dependency(root)) for target in (scalar, multiple))
    forward = dict(zip(edges, branch_segments(edges)))
    backward = dict(zip(reversed(edges), branch_segments(tuple(reversed(edges)))))
    assert forward == backward and len(set(forward.values())) == 2


def test_theme_resolution_updates_cached_frame_without_resolving_geometry():
    from mudyla.logging.formatters.branches import BranchTheme, branch_palette
    logger, keys = branch_logger()
    dag = logger._dag
    layout = dag.layout
    logger.tasks[keys[0]].status = TaskStatus.DONE
    logger._action_lines()
    dark = edge_style(logger, keys[0], keys[1])
    logger._background_probe.background = (250, 250, 250)
    logger._action_lines()
    light = edge_style(logger, keys[0], keys[1])
    assert dag.layout is layout
    assert dark.color != light.color and dark.dim == light.dim
    assert light.color.name in branch_palette(BranchTheme.LIGHT)
    logger._background_probe.background = None
    logger._action_lines()
    assert edge_style(logger, keys[0], keys[1]).color.name in branch_palette(BranchTheme.TERMINAL)
    logger.console.no_color = True
    logger._action_lines()
    assert edge_style(logger, keys[0], keys[1]).color is None


def test_shared_junction_hue_stays_fixed_when_activity_changes_and_cuts_ignore_horizontal_owner():
    from mudyla.logging.formatters.layered import Direction, EdgeConnection, LayoutCell
    logger, keys = branch_logger()
    dag = logger._dag
    edge_ids = [next(index for index, edge in enumerate(dag.edges) if edge.source == keys[0] and edge.target == key)
                for key in keys[1:3]]
    first, second = sorted(edge_ids, key=lambda edge: dag._branch_segments[edge])
    vertical = Direction.UP | Direction.DOWN
    horizontal = Direction.LEFT | Direction.RIGHT
    junction = LayoutCell(0, (EdgeConnection(first, vertical), EdgeConnection(second, vertical)), vertical, False)
    styles = ['dim'] * len(dag.edges)
    styles[first] = 'not dim'
    initial = dag._gutter((junction,), tuple(styles), False, None).get_style_at_offset(logger.console, 0)
    styles[first], styles[second] = 'dim', 'not dim'
    changed = dag._gutter((junction,), tuple(styles), False, None).get_style_at_offset(logger.console, 0)
    assert initial.color == changed.color and initial.dim is False and changed.dim is False
    crossing = LayoutCell(0, (EdgeConnection(first, horizontal), EdgeConnection(second, vertical)), vertical | horizontal, True)
    styles[first], styles[second] = 'not dim', 'dim'
    crossed = dag._gutter((crossing,), tuple(styles), False, None).get_style_at_offset(logger.console, 0)
    only_vertical = dag._gutter((LayoutCell(0, (EdgeConnection(second, vertical),), vertical, False),),
                                tuple(styles), False, None).get_style_at_offset(logger.console, 0)
    assert crossed == only_vertical and crossed.dim is True


def test_parallel_kinds_and_retainers_share_the_same_segment():
    from mudyla.dag.solver.model import DagEdge
    from mudyla.logging.formatters.branches import branch_segments
    source, target, goal = [ActionKey.from_name(name) for name in ('source', 'target', 'goal')]
    edges = tuple(DagEdge(source, target, dependency) for dependency in (
        Dependency(source), Dependency(source, weak=True), Dependency(source, soft=True, retainer_action=goal)))
    edges += (DagEdge(target, goal, Dependency(target)),)
    assert len(set(branch_segments(edges))) == 1
