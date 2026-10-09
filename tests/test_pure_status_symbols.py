"""Action markers distinguish readiness and execution outcomes."""

from tests.logger_fixtures import prepared_logger, static_tree

from io import BytesIO, StringIO, TextIOWrapper

import pytest
from rich.console import Console
from rich.style import Style

from mudyla.logging.terminal_logger_pure import PureTerminalLogger
from mudyla.logging.terminal_logger_table import TaskStatus
from mudyla.logging.formatters import OutputFormatter
from tests.test_plan_dag import crossing_graph


@pytest.mark.parametrize('status,ready,expected,color', [
    (TaskStatus.TBD, False, '◌', None),
    (TaskStatus.TBD, True, '○', 'cyan'),
    (TaskStatus.DONE, True, '●', 'green'),
    (TaskStatus.FAILED, True, '⊗', 'red'),
])
def test_pure_node_symbols_follow_readiness_and_outcome(status, ready, expected, color):
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=False, compact=True, console=Console(file=StringIO(), force_terminal=True, color_system='truecolor', no_color=False))
    logger = prepared_logger(PureTerminalLogger, keys, output, True, graph=graph)
    key = keys[0] if ready else keys[1]
    logger.tasks[key].status = status
    marker = logger._tree_status(key)
    assert marker.plain.strip() == expected
    style = Style.parse(str(marker.style))
    assert (style.color.name if style.color else None) == color
    assert bool(style.dim) == (status == TaskStatus.TBD and not ready)


def test_pure_running_marker_uses_yellow_half_circle():
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=False, compact=True, console=Console(file=StringIO(), force_terminal=True, color_system='truecolor', no_color=False))
    logger = prepared_logger(PureTerminalLogger, keys, output, True, graph=graph)
    logger.tasks[keys[0]].status = TaskStatus.RUNNING
    frames = set()
    for tick in range(16):
        logger._tree_frame_time = tick / 8
        marker = logger._tree_status(keys[0])
        frames.add(marker.plain.strip())
        assert marker.plain.strip() in {'◐', '◓', '◑', '◒'}
        assert Style.parse(str(marker.style)).color.name == 'yellow'
    assert len(frames) > 1


@pytest.mark.parametrize('status,glyph,color,dim', [(TaskStatus.RESTORED, '◉', 'green', False),
                                                  (TaskStatus.SKIPPED, '⊖', None, True),
                                                  (TaskStatus.CANCELLED, '⊘', 'yellow', False)])
def test_pure_terminal_status_markers_use_circle_symbols(status, glyph, color, dim):
    output = OutputFormatter(no_color=False, compact=True, console=Console(file=StringIO(), force_terminal=True))
    _, keys = crossing_graph()
    logger = prepared_logger(PureTerminalLogger, keys, output, True)
    logger.tasks[keys[0]].status = status
    marker = logger._status_marker(keys[0], 0)
    assert marker.plain.strip() == glyph
    style = Style.parse(str(marker.style))
    assert (style.color.name if style.color else None) == color
    assert bool(style.dim) == dim


@pytest.mark.parametrize("encoding", ["utf-8", "ascii", "cp1252"])
@pytest.mark.parametrize("no_color", [False, True])
def test_status_markers_keep_one_cell_and_survive_output_encoding(encoding, no_color):
    stream = TextIOWrapper(BytesIO(), encoding=encoding)
    output = OutputFormatter(no_color=no_color, compact=True,
                             console=Console(file=stream, force_terminal=True, color_system="truecolor", no_color=no_color))
    _, keys = crossing_graph()
    logger = prepared_logger(PureTerminalLogger, keys, output, True)
    for status in TaskStatus:
        logger.tasks[keys[0]].status = status
        marker = logger._status_marker(keys[0], 0)
        assert marker.cell_len == 2
        assert "?" not in marker.plain
        output.console.print(marker)
    stream.flush()
    encoded = stream.buffer.getvalue()
    assert (b"\x1b[" not in encoded) if no_color else (b"\x1b[" in encoded)


def test_flat_rows_share_graph_readiness_and_stop_policy():
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=False, compact=True, console=Console(file=StringIO(), force_terminal=True, color_system="truecolor", no_color=False))
    logger = prepared_logger(PureTerminalLogger, keys, output, True, graph=graph, plan_style="tree")
    assert logger._status_marker(keys[1], 0).plain == "◌ "
    logger.tasks[keys[0]].status = TaskStatus.RESTORED
    assert logger._status_marker(keys[1], 0).plain == "○ "
    assert "○ " in logger._action_rows()[1].plain
    logger.kill_requested = True
    assert logger._status_marker(keys[1], 0).plain == "◌ "


def test_static_plan_markers_and_legend_match_live_readiness():
    from rich.text import Text

    from mudyla.cli import CLI
    from mudyla.dag.display import build_display_edges
    from mudyla.logging.formatters.plan import tree_section

    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=True, compact=True, console=Console(file=StringIO(), width=180, force_terminal=True))
    display = build_display_edges(graph, tuple(keys), full=True)
    tree = static_tree(graph, keys, output, True, {}, display=display)
    with output.console.capture() as capture:
        output.console.print(tree_section(tree, output.symbols))
    plain = Text.from_ansi(capture.get()).plain
    assert "○ " + keys[0].id.name in plain
    assert "◌ " + keys[1].id.name in plain
    assert "○ deps ready" in plain and "◌ waiting" in plain


@pytest.mark.parametrize("encoding", ["utf-8", "ascii", "cp1252"])
def test_running_marker_holds_each_frame_for_quarter_second(encoding):
    stream = TextIOWrapper(BytesIO(), encoding=encoding)
    output = OutputFormatter(no_color=False, compact=True,
                             console=Console(file=stream, force_terminal=True, color_system="truecolor", no_color=False))
    _, keys = crossing_graph()
    logger = prepared_logger(PureTerminalLogger, keys, output, True)
    logger.tasks[keys[0]].status = TaskStatus.RUNNING
    for start in (0, .25, .5, .75):
        initial = logger._status_marker(keys[0], start).plain
        assert logger._status_marker(keys[0], start + .249).plain == initial
        assert logger._status_marker(keys[0], start + .25).plain != initial
    assert logger._status_marker(keys[0], 1).plain == logger._status_marker(keys[0], 0).plain


def test_running_marker_rotates_half_circle_in_order():
    output = OutputFormatter(no_color=False, compact=True, console=Console(file=StringIO(), force_terminal=True, color_system="truecolor", no_color=False))
    _, keys = crossing_graph()
    logger = prepared_logger(PureTerminalLogger, keys, output, True)
    logger.tasks[keys[0]].status = TaskStatus.RUNNING
    frames = [logger._status_marker(keys[0], tick / 4) for tick in range(12)]
    assert "".join(frame.plain.strip() for frame in frames) == "◐◓◑◒" * 3
    assert all(frame.cell_len == 2 and Style.parse(str(frame.style)).color.name == "yellow" for frame in frames)
