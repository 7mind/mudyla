"""Pure presents execution state on one selectable dependency graph."""

from io import StringIO

import pytest
from rich.console import Console
from rich.text import Text

from mudyla.ast.models import ActionDefinition, SourceLocation
from mudyla.dag.context import ContextId
from mudyla.dag.graph import ActionGraph, ActionKey, ActionNode, Dependency
from mudyla.logging.action_logger_pure import ActionLoggerPure
from mudyla.logging.action_logger_table import TaskStatus
from mudyla.logging.formatters import OutputFormatter
from mudyla.logging.terminal_background import BackgroundProbe
from tests.terminal_capture import terminal_text
from tests.test_plan_dag import crossing_graph


@pytest.mark.parametrize("finished", [False, True])
def test_pure_dag_renders_one_actions_graph_with_execution_data(finished):
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=True, compact=True, console=Console(file=StringIO(), width=120, height=60, force_terminal=True))
    logger = ActionLoggerPure(keys, output, True, graph=graph)
    logger.selected_index = 3
    logger.tasks[keys[1]].status = TaskStatus.DONE
    logger.tasks[keys[1]].duration = 1.25
    logger.tasks[keys[1]].latest = "COMPILE_LOG_TOKEN"
    logger.stop_flag = finished
    output.print(logger._build_renderable())
    rendered = terminal_text(output.console.file.getvalue()).plain
    assert "Plan:" not in rendered
    assert rendered.count("Actions:") == 1
    nodes = [line for line in rendered.splitlines() if "@global" in line]
    for key in keys:
        assert sum(key.id.name in line for line in nodes) == 1
    assert "1.2s" in rendered and "COMPILE_LOG_TOKEN" in rendered
    selected = next(line for line in rendered.splitlines() if "cache" in line)
    assert selected.startswith("> ")


def test_resize_keeps_selected_node_visible_across_wrapped_graph_rows():
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=True, compact=True, console=Console(file=StringIO(), width=100, height=24, force_terminal=True))
    logger = ActionLoggerPure(keys, output, True, graph=graph)
    for _ in keys[1:]:
        logger._handle_key_table("down")
    for width, height in [(100, 24), (25, 5), (100, 24)]:
        output.console.size = (width, height)
        logger._overview_content()
        anchor = logger._overview_prefix_length + logger._action_anchors[keys[-1]]
        assert logger._overview_offset <= anchor < logger._overview_offset + logger._get_content_height()
        assert logger._get_selected_action_key() == keys[-1]


@pytest.mark.parametrize("selected_index", [1, 4])
@pytest.mark.parametrize("no_color", [False, True])
def test_cursor_and_palette_highlight_do_not_restyle_graph_or_font_weights(selected_index, no_color):
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=no_color, compact=True, console=Console(file=StringIO(), width=100, height=24, force_terminal=True, color_system="truecolor", no_color=False))
    logger = ActionLoggerPure(keys, output, True, graph=graph)
    logger._background_probe = BackgroundProbe(0)
    logger._background_probe.feed("\x1b]11;rgb:fa/fa/fa\x07", 0)
    logger.selected_index = selected_index
    selected = keys[selected_index]
    logger.tasks[selected].status = TaskStatus.RUNNING
    rows, anchors = logger._action_lines()
    texts = [Text.assemble(*[(segment.text, segment.style or "") for segment in row]) for row in rows]
    line = texts[anchors[selected]]
    assert line.plain.startswith("> ")
    assert sum(text.plain.startswith("> ") for text in texts) == 1
    name_start = line.plain.index(selected.id.name)
    for offset in range(name_start):
        background = line.get_style_at_offset(output.console, offset).bgcolor
        assert (background is None) == no_color
    name_style = line.get_style_at_offset(output.console, name_start)
    assert bool(name_style.bold) == (selected in graph.goals)
    for offset in range(name_start + len(selected.id.name), line.cell_len):
        assert line.get_style_at_offset(output.console, offset).bold is not True
    if no_color:
        assert all(segment.style is None or segment.style.bgcolor is None for row in rows for segment in row)
    else:
        assert name_style.color is None and name_style.bgcolor.get_truecolor() == (242, 242, 242)
        assert line.cell_len == output.console.width
        assert line.get_style_at_offset(output.console, line.cell_len - 1).bgcolor == name_style.bgcolor
    assert all(not text.plain.startswith("> ") for index, text in enumerate(texts) if index not in anchors.values())


@pytest.mark.parametrize("width", [100, 25, 8])
def test_selection_background_covers_full_physical_node_row(width):
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=False, compact=True, console=Console(file=StringIO(), width=width, height=24, force_terminal=True, color_system="truecolor", no_color=False))
    logger = ActionLoggerPure(keys, output, True, graph=graph)
    logger.selected_index = 1
    logger._background_probe = BackgroundProbe(0)
    logger._background_probe.background = (250, 250, 250)
    logger.selected_index = 0
    original_rows, original_anchors = logger._action_lines()
    logger.selected_index = 1
    rows, anchors = logger._action_lines()
    assert anchors == original_anchors
    start = anchors[keys[1]]
    selected_rows = range(start, start + logger._dag._rendered_rows[keys[1]].label_rows)
    assert len(selected_rows) > 1 if width < 100 else len(selected_rows) == 1
    for index, row in enumerate(rows):
        if index in selected_rows:
            assert sum(segment.cell_length for segment in row) == output.console.width
            assert all(segment.style is not None and segment.style.bgcolor is not None
                       and segment.style.bgcolor.get_truecolor() == (242, 242, 242) for segment in row)
            before = Text.assemble(*[(segment.text, segment.style or "") for segment in original_rows[index]])
            after = Text.assemble(*[(segment.text, segment.style or "") for segment in row])
            for offset in range(len(before)):
                old, new = (text.get_style_at_offset(output.console, offset) for text in [before, after])
                assert (old.color, old.bold, old.dim) == (new.color, new.bold, new.dim)
        else:
            assert all(segment.style is None or segment.style.bgcolor is None for segment in row)
    assert all(segment.style is None or segment.style.bgcolor is None
               for block in logger._dag._rendered_rows.values() for line in block.lines for segment in line)


@pytest.mark.parametrize("width", [100, 25, 8])
@pytest.mark.parametrize("no_color", [False, True])
def test_flat_actions_share_the_full_row_selection_policy(width, no_color):
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=no_color, compact=True, console=Console(file=StringIO(), width=width, force_terminal=True, color_system="truecolor", no_color=False))
    logger = ActionLoggerPure(keys, output, True, graph=graph, plan_style="tree")
    logger.selected_index = 1
    before = [Text.assemble(*[(segment.text, segment.style or "") for segment in row]) for row in logger._action_lines()[0]]
    logger._background_probe = BackgroundProbe(0)
    logger._background_probe.background = (250, 250, 250)
    after = [Text.assemble(*[(segment.text, segment.style or "") for segment in row]) for row in logger._action_lines()[0]]
    for index, line in enumerate(after):
        if index == 1 and not no_color:
            assert line.cell_len == width
        for offset in range(len(line)):
            style = line.get_style_at_offset(output.console, offset)
            assert (style.bgcolor is not None) == (index == 1 and not no_color)
            if offset < len(before[index]):
                original = before[index].get_style_at_offset(output.console, offset)
                assert (style.color, style.bold, style.dim) == (original.color, original.bold, original.dim)


@pytest.mark.parametrize("width,height", [(100, 24), (25, 5), (8, 5)])
def test_keyboard_selection_skips_connector_and_wrapped_rows(width, height):
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=True, compact=True, console=Console(file=StringIO(), width=width, height=height, force_terminal=True))
    logger = ActionLoggerPure(keys, output, True, graph=graph)
    for index, key in enumerate(keys):
        if index:
            logger._handle_key_table("down")
        logger._overview_content()
        assert logger._get_selected_action_key() == key
        assert list(logger._action_anchors) == keys
        anchor = logger._overview_prefix_length + logger._action_anchors[key]
        assert logger._overview_offset <= anchor < logger._overview_offset + logger._get_content_height()
    logger._handle_key_table("page_up")
    assert logger._get_selected_action_key() == keys[-1]
    logger._handle_key_table("up")
    assert logger._get_selected_action_key() == keys[-2]


def test_narrow_node_anchor_keeps_status_and_first_name_fragment_together():
    keys = [ActionKey.from_name(name) for name in ["source", "very_long_compile_action", "finish"]]
    nodes = {key: ActionNode(key, ActionDefinition(key.id.name, [], {}, SourceLocation("fixture", 1, key.id.name)))
             for key in keys}
    for key in keys[1:]:
        nodes[key].dependencies.add(Dependency(keys[0]))
    output = OutputFormatter(no_color=True, compact=True, console=Console(file=StringIO(), width=12, height=5, force_terminal=True))
    logger = ActionLoggerPure(keys, output, True, graph=ActionGraph(nodes, {keys[-1]}))
    logger.selected_index = 1
    logger.tasks[keys[1]].status = TaskStatus.DONE
    rows, anchors = logger._action_lines()
    selected = "".join(segment.text for segment in rows[anchors[keys[1]]])
    assert selected.startswith("> ● very"), selected


def test_equal_action_names_select_distinct_full_context_keys_and_details(tmp_path):
    keys = [ActionKey.from_name("build", ContextId.from_dict({"platform": platform})) for platform in ["linux", "windows"]]
    nodes = {key: ActionNode(key, ActionDefinition("build", [], {}, SourceLocation("fixture", 1, "build"))) for key in keys}
    output = OutputFormatter(no_color=True, compact=True, console=Console(file=StringIO(), width=40, height=5, force_terminal=True))
    logger = ActionLoggerPure(keys, output, True, graph=ActionGraph(nodes, set(keys)))
    for index, key in enumerate(keys):
        directory = tmp_path / str(index)
        directory.mkdir()
        (directory / "stdout.log").write_text(f"CONTEXT_{index}")
        logger.mark_running(key, directory)
    logger._handle_key_table("down")
    assert logger._get_selected_action_key() == keys[1]
    logger._handle_key_table("l")
    with output.console.capture() as capture:
        output.console.print(logger._build_renderable())
    assert "CONTEXT_1" in capture.get() and "CONTEXT_0" not in capture.get()
    logger._handle_key_scroll("q")
    logger._handle_key_table("i")
    assert logger._input_action == keys[1]


def test_selection_preserves_name_context_and_time_foreground_attributes():
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=False, compact=True, console=Console(file=StringIO(), width=100, height=24, force_terminal=True, color_system="truecolor", no_color=False))
    logger = ActionLoggerPure(keys, output, True, graph=graph)
    logger._background_probe = BackgroundProbe(0)
    logger._background_probe.feed("\x1b]11;rgb:fa/fa/fa\x07", 0)
    attributes = []
    for selected in [0, 1]:
        logger.selected_index = selected
        rows, anchors = logger._action_lines()
        line = Text.assemble(*[(segment.text, segment.style or "") for segment in rows[anchors[keys[1]]]])
        attributes.append([(style.color, style.bold, style.dim) for offset in range(line.plain.index("compile"), line.plain.index(":"))
                           for style in [line.get_style_at_offset(output.console, offset)]])
    assert attributes[0] == attributes[1]


def test_render_cache_preserves_current_status_selection_latest_and_resize(monkeypatch):
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=False, compact=True, console=Console(file=StringIO(), width=100, height=24, force_terminal=True, color_system="truecolor", no_color=False))
    logger = ActionLoggerPure(keys, output, True, graph=graph)
    logger._background_probe = BackgroundProbe(0)
    monkeypatch.setattr("mudyla.logging.action_logger_pure.time.time", lambda: 100.0)
    for width in [100, 8, 40, 100]:
        output.console.width = width
        for index, status in enumerate([TaskStatus.RUNNING, TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.RESTORED]):
            logger.selected_index = index
            logger._background_probe.background = (250, 250, 250) if index % 2 else (20, 25, 30)
            task = logger.tasks[keys[index]]
            task.status = status
            task.start_time = 98
            task.latest = f"message {index} 界\x1b[31m red\x1b[0m"
            task.stream = "stderr" if index % 2 else "stdout"
            warm = logger._action_lines()
            logger._dag._rendered_rows.clear()
            cold = logger._action_lines()
            assert warm == cold
            assert len(logger._dag._rendered_rows) == len(keys)


@pytest.mark.parametrize('interactive', [False, True])
def test_pure_omits_action_count_banner(interactive):
    stream = StringIO()
    output = OutputFormatter(no_color=True, compact=True, console=Console(file=stream, width=100, height=30, force_terminal=interactive))
    logger = ActionLoggerPure([ActionKey.from_name('work')], output, True)
    if interactive:
        output.print(logger._build_renderable())
    else:
        logger.start()
    assert 'mudyla /' not in stream.getvalue()
    if interactive:
        assert terminal_text(stream.getvalue()).plain.splitlines()[0] == 'Actions:'
    else:
        assert stream.getvalue() == ''


@pytest.mark.parametrize('show_dirs,has_directory', [(False, False), (True, False), (True, True)])
def test_pure_banner_removal_reclaims_only_unused_heading_row(tmp_path, show_dirs, has_directory):
    output = OutputFormatter(no_color=True, compact=True, console=Console(file=StringIO(), width=100, height=30, force_terminal=True))
    key = ActionKey.from_name('work')
    logger = ActionLoggerPure([key], output, True, show_dirs=show_dirs, fullscreen=True)
    if has_directory:
        logger.tasks[key].action_dir = tmp_path / 'selected-action-directory'
    assert logger._get_content_height() == 30 - 2 - int(show_dirs and has_directory)
    output.print(logger._build_renderable())
    rendered = output.console.file.getvalue()
    assert 'mudyla /' not in rendered
    assert ('selected-action-directory' in rendered) == (show_dirs and has_directory)
    assert len(rendered.splitlines()) <= 30
    if not (show_dirs and has_directory):
        assert terminal_text(rendered).plain.splitlines()[0].rstrip() == 'Actions:'
