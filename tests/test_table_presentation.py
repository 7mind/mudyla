"""Table presentation preserves preparation, compact columns, and terminal colors."""

from io import StringIO
import re

import pytest
from rich.console import Console
from rich.text import Text

from mudyla.cli import CLI
from mudyla.dag.graph import ActionKey
from mudyla.logging.action_logger_table import ActionLoggerTable
from mudyla.logging.terminal_background import BackgroundProbe


def table_logger(monkeypatch, width):
    keys = [ActionKey.from_name(name) for name in ["demo-prepare", "demo-build", "demo-check"]]
    logger = ActionLoggerTable(keys)
    logger.console = Console(file=StringIO(), width=width, height=24, force_terminal=True,
                             color_system="truecolor", no_color=False, highlight=False)
    monkeypatch.setattr(logger, "_get_terminal_size", lambda: (width, 24))
    for key in keys:
        logger.mark_done(key, .1)
    return logger


def rendered_lines(logger):
    return [Text.assemble(*[(segment.text, segment.style or "") for segment in line])
            for line in logger.console.render_lines(logger._build_renderable(), pad=False)]


def test_table_counts_follow_rows_without_blank_header_band(monkeypatch):
    lines = [line.plain for line in rendered_lines(table_logger(monkeypatch, 120))]
    header = next(index for index, line in enumerate(lines) if "Action" in line and "Status" in line)
    last_action = next(index for index, line in enumerate(lines) if "demo-check" in line)
    counts = next(index for index, line in enumerate(lines) if "3 done" in line)
    assert lines[header - 1].startswith("╭"), "The table header must immediately follow its top border"
    assert counts > last_action, "Execution counts belong below the action rows"


@pytest.mark.parametrize("width", [100, 160, 240])
def test_table_status_stays_next_to_short_action_names(monkeypatch, width):
    lines = rendered_lines(table_logger(monkeypatch, width))
    row = next(line.plain for line in lines if "demo-prepare" in line.plain)
    assert row.index("done") - row.index("demo-prepare") <= 28, row


@pytest.mark.parametrize("background", [(250, 250, 250), (24, 28, 34)])
def test_table_selection_tints_row_without_reversing_foreground(monkeypatch, background):
    logger = table_logger(monkeypatch, 120)
    probe = BackgroundProbe(0)
    probe.background = background
    logger._background_probe = probe
    selected = next(line for line in rendered_lines(logger) if "demo-prepare" in line.plain)
    expected = probe.selection_style("truecolor").bgcolor
    marker = next(index for index, char in enumerate(selected.plain) if char in ">▶")
    last_cell = selected.plain.rfind("│")
    for offset in range(marker, last_cell - 1):
        style = selected.get_style_at_offset(logger.console, offset)
        assert not style.reverse
        assert style.bgcolor == expected
    assert selected.get_style_at_offset(logger.console, selected.plain.index("done")).color.name == "green"


def test_table_default_plan_and_run_fields_use_shared_sections(tmp_path, monkeypatch, capsys):
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (definitions / "actions.md").write_text(
        '# action: base\n```python\npass\n```\n\n'
        '# action: work\n```python\nmdl.dep("action.base")\npass\n```\n')
    monkeypatch.chdir(tmp_path)
    assert CLI().run(["--without-nix", "--logger", "table", "--force-interactive", "--no-color", "--dry-run", ":work"]) == 0
    output = capsys.readouterr().out
    assert "Run info:" in output
    assert output.index("Execution mode:") < output.index("Contexts:")
    plan = output.split("Plan:\n", 1)[1]
    for column in ["Context", "Action", "Goal", "Deps", "Shared"]:
        assert column in plan
    assert re.search(r"\b1\b.*base", plan)
    assert re.search(r"\b2\b.*work", plan)
    assert "deps ready" not in plan


@pytest.mark.parametrize("mode,option,expected", [("table", [], "table"), ("table", ["--plan-tree"], "tree"),
    ("table", ["--plan-dag"], "dag"), ("pure", [], "dag")])
def test_plan_default_depends_on_logger_but_explicit_options_are_preserved(mode, option, expected):
    cli = CLI()
    args = cli.parser.parse_args(["--logger", mode, "--force-interactive", *option])
    cli._apply_platform_defaults(args, True)
    assert args.plan_style == expected


@pytest.mark.parametrize("background,no_color", [(None, False), ((250, 250, 250), True)])
def test_table_selection_without_a_supported_theme_uses_only_cursor(monkeypatch, background, no_color):
    logger = table_logger(monkeypatch, 80)
    logger.no_color = no_color
    logger._background_probe = BackgroundProbe(0)
    logger._background_probe.background = background
    selected = next(line for line in rendered_lines(logger) if "demo-prepare" in line.plain)
    assert ">" in selected.plain
    for offset in range(len(selected.plain)):
        style = selected.get_style_at_offset(logger.console, offset)
        assert not style.reverse and style.bgcolor is None


def test_static_table_keeps_contextual_goals_and_dependency_kinds():
    from mudyla.ast.models import ActionDefinition, SourceLocation
    from mudyla.dag.context import ContextId
    from mudyla.dag.graph import ActionGraph, ActionId, ActionNode, Dependency
    from mudyla.logging.formatters import OutputFormatter
    from mudyla.logging.formatters.plan import execution_table

    keys = [ActionKey.from_name("base"), *[
        ActionKey(ActionId("work"), ContextId(axis_values=(("mode", value),))) for value in ["fast", "slow"]]]
    nodes = {key: ActionNode(key, ActionDefinition(key.id.name, [], {}, SourceLocation("fixture", 1, key.id.name))) for key in keys}
    nodes[keys[1]].dependencies.add(Dependency(keys[0], weak=True))
    nodes[keys[2]].dependencies.add(Dependency(keys[0], soft=True))
    graph = ActionGraph(nodes, {keys[2]})
    output = OutputFormatter(no_color=True)
    table = execution_table(graph, keys, output.context, False, {keys[0]: 2}, True)
    assert [cell.plain if isinstance(cell, Text) else cell for cell in table.columns[3]._cells] == ["", "", "*"]
    assert table.columns[4]._cells == ["-", "~1", "?1"]
    assert table.columns[5]._cells == ["2", "-", "-"]
    contexts = [cell.plain for cell in table.columns[1]._cells]
    assert contexts[1] != contexts[2]


@pytest.mark.parametrize("height", [6, 8, 12])
def test_table_directory_and_controls_fit_in_viewport(monkeypatch, height):
    keys = [ActionKey.from_name(f"action-{index:03d}") for index in range(100)]
    logger = ActionLoggerTable(keys, show_dirs=True)
    logger.console = Console(file=StringIO(), width=40, height=height, force_terminal=True, no_color=True)
    monkeypatch.setattr(logger, "_get_terminal_size", lambda: (40, height))
    logger.selected_index = 50
    lines = rendered_lines(logger)
    assert len(lines) <= height
    assert "action-050" in "\n".join(line.plain for line in lines)
    assert "q kill" in lines[-1].plain


def test_live_table_distinguishes_same_action_in_different_contexts(monkeypatch):
    from mudyla.dag.context import ContextId
    from mudyla.dag.graph import ActionId
    from mudyla.logging.formatters.details import context_label

    keys = [ActionKey(ActionId("work"), ContextId(axis_values=(("mode", value),))) for value in ["fast", "slow"]]
    logger = ActionLoggerTable(keys)
    logger.console = Console(file=StringIO(), width=120, height=24, force_terminal=True, no_color=False)
    monkeypatch.setattr(logger, "_get_terminal_size", lambda: (120, 24))
    lines = rendered_lines(logger)
    rendered = "\n".join(line.plain for line in lines)
    for key in keys:
        identity = context_label(key.context_id, logger._context_formatter, True).plain
        assert identity in rendered
        row = next(line for line in lines if identity in line.plain)
        assert row.get_style_at_offset(logger.console, row.plain.index("work")).bold
        style = row.get_style_at_offset(logger.console, row.plain.index(identity))
        assert style.dim and not style.bold


@pytest.mark.parametrize("width,show_dirs", [(40, False), (80, False), (120, True), (160, True)])
def test_long_action_labels_preserve_status_metrics_and_context(monkeypatch, width, show_dirs):
    from mudyla.dag.context import ContextId
    from mudyla.dag.graph import ActionId
    from mudyla.logging.formatters.details import context_label

    keys = [ActionKey(ActionId("compile-library-with-a-deliberately-long-action-identifier"),
                      ContextId(axis_values=(("flavor", value),))) for value in ["alpha", "beta"]]
    logger = ActionLoggerTable(keys, show_dirs=show_dirs, use_short_ids=False)
    logger.console = Console(file=StringIO(), width=width, height=24, force_terminal=True, no_color=False)
    monkeypatch.setattr(logger, "_get_terminal_size", lambda: (width, 24))
    for key in keys:
        logger.mark_done(key, 1.5)
        logger.tasks[key].stdout_size = 123
        logger.tasks[key].stderr_size = 45
        label = logger._action_formatter.format_label_plain(key, False)
        logger.action_dirs_map[label] = ".mdl/runs/20261007-TEST/" + str(key)
    rows = [line.plain for line in rendered_lines(logger)]
    header = next(line for line in rows if "│" in line and "Action" in line)
    assert "Status" in header and "Time" in header, header
    for key in keys:
        identity = context_label(key.context_id, logger._context_formatter, False).plain
        row = next(line for line in rows if identity in line)
        assert "done" in row and "1.5s" in row, row
        if width >= 76:
            assert "123B" in row and "45B" in row, row
    assert any(">" in line for line in rows)
    assert len(rows) <= 24


def test_static_plan_wraps_full_action_and_context_identities_at_40_columns():
    from mudyla.ast.models import ActionDefinition, SourceLocation
    from mudyla.dag.graph import ActionGraph, ActionNode, Dependency
    from mudyla.logging.formatters import OutputFormatter
    from mudyla.logging.formatters.plan import execution_table

    names = ["demo-prepare", "demo-build", "demo-check", "demo-package"]
    keys = [ActionKey.from_name(name) for name in names]
    nodes = {key: ActionNode(key, ActionDefinition(key.id.name, [], {}, SourceLocation("fixture", 1, key.id.name))) for key in keys}
    for previous, key in zip(keys, keys[1:]):
        nodes[key].dependencies.add(Dependency(previous))
    graph = ActionGraph(nodes, {keys[-1]})
    output = OutputFormatter(no_color=True)
    table = execution_table(graph, keys, output.context, True, {}, False)
    console = Console(file=StringIO(), width=40, no_color=True)
    rows = {}
    current = None
    for segments in console.render_lines(table, pad=False):
        line = "".join(segment.text for segment in segments)
        if "│" not in line:
            continue
        cells = [cell.strip() for cell in line.split("│")[1:-1]]
        if cells[0].isdigit():
            current = int(cells[0])
            rows[current] = ["" for _ in cells]
        if current is not None:
            rows[current] = [value + part for value, part in zip(rows[current], cells)]
    for index, name in enumerate(names, 1):
        assert rows[index][1] == "@global"
        assert rows[index][2] == name
        assert rows[index][4] == (str(index - 1) if index > 1 else "-")


@pytest.mark.parametrize("mode", ["pure", "table", "simple", "verbose"])
def test_explicit_plan_table_selects_static_table_for_every_logger(mode):
    cli = CLI()
    args = cli.parser.parse_args(["--logger", mode, "--force-interactive", "--plan-table"])
    cli._apply_platform_defaults(args, True)
    assert args.plan_style == "table"
