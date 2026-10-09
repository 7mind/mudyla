"""Empty latest-output fields remain explicit without replacing real output."""

from io import StringIO

import pytest
from rich.console import Console
from rich.text import Text

from mudyla.logging.action_logger_pure import ActionLoggerPure
from mudyla.logging.action_logger_table import TaskStatus
from mudyla.logging.formatters import OutputFormatter
from tests.test_plan_dag import crossing_graph


@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("status", list(TaskStatus))
def test_empty_latest_output_follows_duration_in_existing_action_row(connected, status):
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=False, compact=True,
                             console=Console(file=StringIO(), width=180, color_system="truecolor", force_terminal=True))
    logger = ActionLoggerPure(keys, output, True, graph=graph if connected else None)
    task = logger.tasks[keys[0]]
    task.status = status
    task.duration = 1.25
    lines, anchors = logger._action_lines()
    row = Text.assemble(*[(segment.text, segment.style or "") for segment in lines[anchors[keys[0]]]])
    assert logger._format_duration(task.duration) + ": <empty>" in row.plain
    assert row.get_style_at_offset(output.console, row.plain.index("<empty>")).dim


@pytest.mark.parametrize("connected", [False, True])
def test_real_latest_error_payload_is_preserved(connected):
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=False, compact=True,
                             console=Console(file=StringIO(), width=180, color_system="truecolor", force_terminal=True))
    logger = ActionLoggerPure(keys, output, True, graph=graph if connected else None)
    logger.tasks[keys[0]].latest = "[failure] exact payload"
    logger.tasks[keys[0]].stream = "stderr"
    lines, anchors = logger._action_lines()
    row = Text.assemble(*[(segment.text, segment.style or "") for segment in lines[anchors[keys[0]]]])
    assert "[failure] exact payload" in row.plain and "<empty>" not in row.plain
    assert row.get_style_at_offset(output.console, row.plain.index("[failure]")).color.name == "red"
