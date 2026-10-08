"""Pure checklist layout and dependency plans."""

from io import BytesIO, StringIO, TextIOWrapper
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest
from rich.console import Console
from rich.console import Group
from rich.text import Text

from mudyla.ast.models import ActionDefinition, SourceLocation
from mudyla.cli import CLI
from mudyla.dag.context import ContextId
from mudyla.dag.graph import ActionGraph, ActionId, ActionKey, ActionNode, Dependency
from mudyla.logging.action_logger_pure import ActionLoggerPure, MAX_LOG_CHARS
from mudyla.logging.action_logger_table import TaskStatus
from mudyla.executor.retainer_executor import RetainerResult
from mudyla.logging.formatters import OutputFormatter
from tests.terminal_capture import terminal_text


@pytest.mark.parametrize("interactive", [False, True])
@pytest.mark.parametrize("plan_style", ["table", "tree", "dag"])
def test_final_snapshot_contains_selected_graph_once_without_replaying_run_info(interactive, plan_style):
    source, goal = [ActionKey.from_name(name) for name in ["source", "goal"]]
    nodes = {key: ActionNode(key, ActionDefinition(key.id.name, [], {}, SourceLocation("test.md", 1, key.id.name)))
             for key in [source, goal]}
    nodes[goal].dependencies.add(Dependency(source))
    output = OutputFormatter(no_color=True, compact=True)
    output._console = Console(file=StringIO(), width=100, height=24, force_terminal=interactive)
    logger = ActionLoggerPure([source, goal], output, True, graph=ActionGraph(nodes, {goal}),
                              plan_style=plan_style, run_info=Text("ONLY_PREPARATION"))
    for key in [source, goal]:
        logger.mark_done(key, .1)
    logger.stop()
    if interactive:
        output.console.print(logger._build_renderable())
    logger.stop()
    frame = Text.from_ansi(output.console.file.getvalue()).plain
    assert frame.count("Plan:") == (0 if plan_style == "dag" else 1)
    assert "ONLY_PREPARATION" not in frame
    assert ("✓ source" if interactive else "+ source") in frame or "✓ source" in frame
    assert frame.count("Actions:") == 1


def test_tree_starts_with_actual_source_and_only_goal_names_are_bold():
    keys = [ActionKey.from_name(name) for name in ["source", "intermediate_goal", "last"]]
    nodes = {key: ActionNode(key, ActionDefinition(key.id.name, [], {}, SourceLocation("test.md", 1, key.id.name))) for key in keys}
    nodes[keys[1]].dependencies.add(Dependency(keys[0]))
    nodes[keys[2]].dependencies.add(Dependency(keys[1]))
    output = OutputFormatter(no_color=False, compact=True)
    output._console = Console(file=StringIO(), width=100)
    tree = CLI()._build_execution_tree(ActionGraph(nodes, set(keys[1:])), keys, output, True, {})
    rows = output.console.render_lines(tree, pad=False)
    first = Text.assemble(*[(segment.text, segment.style or "") for segment in rows[0]])
    assert first.plain.startswith("◇ source"), first.plain
    for name, bold in [("source", False), ("intermediate_goal", True), ("last", True)]:
        row = next(Text.assemble(*[(segment.text, segment.style or "") for segment in line])
                   for line in rows if name in "".join(segment.text for segment in line))
        assert bool(row.get_style_at_offset(output.console, row.plain.index(name)).bold) == bold


def test_checklist_preserves_authoritative_execution_order_and_selected_identity():
    keys = [ActionKey.from_name(name) for name in ["goal", "right", "source", "left"]]
    goal, right, source, left = keys
    nodes = {key: ActionNode(key, ActionDefinition(key.id.name, [], {}, SourceLocation("test.md", 1, key.id.name))) for key in keys}
    for dependent, prerequisite in [(left, source), (right, source), (goal, left), (goal, right)]:
        nodes[dependent].dependencies.add(Dependency(prerequisite))
        nodes[prerequisite].dependents.add(Dependency(dependent))
    graph = ActionGraph(nodes, {goal})
    order = graph.get_execution_order()
    output = OutputFormatter(no_color=True, compact=True)
    output._console = Console(file=StringIO(), width=100)
    logger = ActionLoggerPure(order, output, True, graph=graph)
    logger.selected_index = order.index(right)
    before = [row.plain.split()[2 if row.plain.startswith(">") else 1] for row in logger._action_rows()]
    logger.mark_done(source, .1)
    logger.mark_running(right)
    after = [row.plain.split()[2 if row.plain.startswith(">") else 1] for row in logger._action_rows()]
    assert before == after == [key.id.name for key in order]
    assert logger._get_selected_action_key() == right
    assert all(order.index(dep.action) < order.index(key) for key in order for dep in nodes[key].dependencies)


@pytest.mark.parametrize("width", [40, 100])
def test_live_tree_updates_dependency_readiness_and_shared_references_without_moving_list(width):
    keys = [ActionKey.from_name(name) for name in ["base", "left", "right", "goal"]]
    base, left, right, goal = keys
    nodes = {key: ActionNode(key, ActionDefinition(key.id.name, [], {}, SourceLocation("test.md", 1, key.id.name))) for key in keys}
    for child, prerequisite in [(left, base), (right, base), (goal, left), (goal, right)]:
        nodes[child].dependencies.add(Dependency(prerequisite))
    graph = ActionGraph(nodes, {goal})
    output = OutputFormatter(no_color=True, compact=True)
    output._console = Console(file=StringIO(), width=width, height=24, force_terminal=True)
    logger = ActionLoggerPure(keys, output, True, graph=graph, plan_style="tree", run_info=Text("RUN_INFORMATION"))

    def document():
        rows = logger._overview_prefix()
        return "\n".join("".join(segment.text for segment in row) for row in rows)

    initial = document()
    assert "Plan:" in initial
    assert "ready" in initial and "waiting" in initial
    logger._handle_key_table("bottom")
    before = logger._overview_offset - logger._overview_prefix_length
    logger.mark_running(base)
    running = document()
    running_glyph = logger._tree_status(base).plain.strip()
    assert running.count(running_glyph + " base") == 1
    assert running.count("○ goal") == 2
    assert logger._overview_offset - logger._overview_prefix_length == before
    logger.mark_done(base, .2)
    ready = document()
    assert logger._tree_status(left).plain.strip() == logger._tree_status(right).plain.strip() == "◇"
    logger.mark_running(left)
    logger.mark_failed(left, .3)
    failed = document()
    assert "✕" in failed
    assert all(logger._tree_status(key).plain.strip() == "○" for key in [right, goal])
    logger.mark_execution_complete()
    assert all(logger.tasks[key].status == TaskStatus.SKIPPED for key in [right, goal])
    assert "- right" in document()


@pytest.mark.parametrize("depth", [3, 8])
@pytest.mark.parametrize("encoding", ["ascii", "cp1252", "utf-8"])
def test_twelve_column_tree_keeps_deep_leaf_identifiers(depth, encoding):
    keys = [ActionKey.from_name("base" if index == 0 else f"node{index}") for index in range(depth)]
    nodes = {key: ActionNode(key, ActionDefinition(key.id.name, [], {}, SourceLocation("test.md", 1, key.id.name))) for key in keys}
    for parent, child in zip(keys[1:], keys):
        nodes[parent].dependencies.add(Dependency(child))
    with TextIOWrapper(BytesIO(), encoding=encoding) as stream:
        output = OutputFormatter(no_color=True, compact=True)
        output._console = Console(file=stream, width=12)
        output.print(CLI()._build_execution_tree(ActionGraph(nodes, {keys[-1]}), keys, output, True, {}))
        stream.flush()
        text = stream.buffer.getvalue().decode(encoding)
    unwrapped = "".join(line.strip() for line in text.splitlines())
    assert all(key.id.name in unwrapped for key in keys)
    assert all(Text(line).cell_len <= 12 for line in text.splitlines())


@pytest.mark.parametrize("encoding", ["ascii", "cp1252", "utf-8"])
def test_preparation_snapshot_preserves_rich_renderables_and_excludes_later_output(encoding):
    from rich.tree import Tree

    with TextIOWrapper(BytesIO(), encoding=encoding) as stream:
        output = OutputFormatter(no_color=False, compact=True)
        output._console = Console(file=stream, width=50, force_terminal=True, highlight=False)
        output.start_recording()
        output.print("[bold cyan]Contexts:[/bold cyan]")
        output.print(Text("[literal] context", style="yellow"))
        tree = Tree("plan")
        tree.add("shared")
        output.print(tree)
        snapshot = output.stop_recording()
        stream.flush()
        original = stream.buffer.getvalue()
        output.print("ACTION_NOT_PART_OF_PLAN")
        stream.flush()
        offset = len(stream.buffer.getvalue())
        output.console.print(snapshot)
        stream.flush()
        rendered = stream.buffer.getvalue()[offset:]
    assert rendered == original
    assert b"ACTION_NOT_PART_OF_PLAN" not in rendered
    assert b"[literal] context" in rendered
    assert b"[bold cyan]" not in rendered


def test_overview_scrolls_run_information_and_all_actions_without_moving_selection():
    output = OutputFormatter(no_color=True, compact=True)
    output._console = Console(file=StringIO(), width=90, height=30, force_terminal=True)
    keys = [ActionKey.from_name(f"task{index:02d}") for index in range(36)]
    logger = ActionLoggerPure(keys, output, True, keep_running=True)
    logger._run_info = Group(Text("Retainers: kept shared"), Text("Execution plan:"),
                             Text("goal\n└── shared\n    └── dependency"))

    def frame():
        with output.console.capture() as capture:
            output.console.print(logger._build_renderable())
        return Text.from_ansi(capture.get()).plain

    assert "task20" in frame(), "overview should use the terminal height, not a twelve-action cap"
    logger._handle_key_table("top")
    assert "Retainers: kept shared" in frame()
    assert "Execution plan:" in frame()
    assert logger.selected_index == 0
    logger._handle_key_table("bottom")
    assert "task35" in frame()
    assert logger.selected_index == 0
    logger._handle_key_table("down")
    assert "> ○ task01" in frame()
    for _ in range(30):
        logger._handle_key_table("down")
    assert "> ○ task31" in frame()
    before = logger._overview_offset
    logger._handle_key_table("l")
    logger._handle_key_scroll("q")
    assert logger._overview_offset == before
    logger._handle_key_table("wheel_up")
    assert logger._overview_offset < before
    assert logger.selected_index == 31


def test_overview_resize_preserves_action_position_after_prefix_rewrap():
    output = OutputFormatter(no_color=True, compact=True)
    output._console = Console(file=StringIO(), width=80, height=12, force_terminal=True)
    keys = [ActionKey.from_name(f"task{index:02d}") for index in range(36)]
    logger = ActionLoggerPure(keys, output, True, keep_running=True)
    logger._run_info = Text("retainer " * 35)
    for _ in range(25):
        logger._handle_key_table("down")
    def visible_actions():
        with output.console.capture() as capture:
            output.console.print(logger._build_renderable())
        frame = Text.from_ansi(capture.get()).plain
        assert "> ○ task25" in frame
        return re.findall(r"\btask\d+", frame)

    before = visible_actions()
    output.console.size = (40, 12)
    assert visible_actions() == before


@pytest.mark.parametrize("encoding", ["ascii", "cp1252", "utf-8"])
def test_overview_reuses_retainer_results_and_shared_dependency_tree(encoding):
    shared, first, second = [ActionKey.from_name(name) for name in ["shared", "build-é-構築", "check"]]
    keys = [shared, first, second]
    nodes = {key: ActionNode(key, ActionDefinition(key.id.name, [], {}, SourceLocation("test.md", 1, key.id.name))) for key in keys}
    for key in keys[1:]:
        nodes[key].dependencies.add(Dependency(shared))
    graph = ActionGraph(nodes, {first, second})
    with TextIOWrapper(BytesIO(), encoding=encoding, newline="\r\n") as stream:
        output = OutputFormatter(no_color=True, compact=True)
        output._console = Console(file=stream, width=65, height=24, force_terminal=True)
        cli = CLI()
        retained = RetainerResult(ActionKey.from_name("keep-shared"), [shared], True, 3)
        run_info = Group(cli._build_retainer_results([retained], output, True), Text("Execution plan"),
                         cli._build_execution_tree(graph, keys, output, True, {}))
        logger = ActionLoggerPure(keys, output, True, keep_running=True, run_info=run_info)
        logger._handle_key_table("top")
        output.console.print(logger._build_renderable())
        stream.flush()
        frame = terminal_text(stream.buffer.getvalue().decode(encoding)).plain
    assert "Retainers" in frame and "keep-shared" in frame and "3ms: shared" in frame
    assert "Execution plan" in frame and "shared (@global)" in frame
    assert "goal" in frame and "Actions" in frame


@pytest.mark.parametrize("width,height", [(100, 24), (40, 12), (24, 6), (12, 4)])
def test_checklist_bounds_active_action_and_latest_output(width, height):
    output = OutputFormatter(no_color=True, compact=True)
    output._console = Console(file=StringIO(), width=width, height=height, force_terminal=True)
    keys = [ActionKey.from_name("live25" if i == 25 else f"task{i:02d}") for i in range(30)]
    logger = ActionLoggerPure(keys, output, True)
    logger.mark_running(keys[25])
    logger.selected_index = 25
    logger.write_output(keys[25], "old\n" + "x" * 10000 + "LATEST", "stdout")
    stream = StringIO()
    console = Console(file=stream, width=width, height=height)
    console.print(logger._render_checklist())
    frame = stream.getvalue()
    assert len(frame.splitlines()) <= height
    assert all(Text(line).cell_len <= console.width for line in frame.splitlines())
    selected = [line for line in frame.splitlines() if line.startswith("> ")]
    assert len(selected) == 1
    name = selected[0].split()[2]
    if width >= 24:
        assert name == keys[25].id.name
    else:
        assert name.endswith("…") and len(name[:-1]) >= 2
        assert keys[25].id.name.startswith(name[:-1])
    assert "q" in frame
    assert "old" not in frame
    assert logger.tasks[keys[25]].latest.endswith("LATEST")
    assert len(logger._partial_lines[(keys[25], "stdout")]) <= MAX_LOG_CHARS
    assert "RUN" not in output.console.file.getvalue()


@pytest.mark.parametrize("encoding", ["ascii", "cp1252", "utf-8"])
def test_checklist_encoding_statuses_and_partial_prompt(encoding):
    with TextIOWrapper(BytesIO(), encoding=encoding) as stream:
        output = OutputFormatter(no_color=False, compact=True)
        output._console = Console(file=stream, width=40, height=12, force_terminal=True)
        keys = [ActionKey.from_name(name) for name in ["long-build-é-構築-" * 4, "test", "package"]]
        logger = ActionLoggerPure(keys, output, True)
        logger.mark_running(keys[0])
        logger.write_output(keys[0], "Type value: ", "stdout")
        assert logger.tasks[keys[0]].latest == "Type value: "
        logger.write_output(keys[0], "answer\n", "stdout")
        assert logger.tasks[keys[0]].latest == "Type value: answer"
        logger.mark_done(keys[0], 1.3)
        logger.mark_running(keys[1])
        logger.write_output(keys[1], "failure detail\n", "stderr")
        logger.mark_failed(keys[1], 2.4)
        logger.stop()
        output.console.print(logger._render_checklist())
        stream.flush()
        assert [state.status.value for state in logger.tasks.values()] == ["done", "failed", "skipped"]


def test_show_dirs_title_preserves_directory_suffix_on_ascii_terminal(tmp_path):
    with TextIOWrapper(BytesIO(), encoding="ascii") as stream:
        output = OutputFormatter(no_color=True, compact=True)
        output._console = Console(file=stream, width=80, height=24, force_terminal=True)
        key = ActionKey.from_name("work")
        directory = tmp_path / ("project-prefix-" * 10) / ".mdl/runs/example/selected-action-directory"
        logger = ActionLoggerPure([key], output, True, show_dirs=True)
        logger.mark_running(key, directory)
        output.console.print(logger._build_renderable())
        stream.flush()
        assert "selected-action-directory" in stream.buffer.getvalue().decode("ascii")


@pytest.mark.parametrize("use_short_ids", [True, False])
@pytest.mark.parametrize("width", [40, 120])
def test_checklist_keeps_action_name_before_context(width, use_short_ids):
    output = OutputFormatter(no_color=True, compact=True)
    output._console = Console(file=StringIO(), width=width, height=12, force_terminal=True)
    keys = [ActionKey(ActionId("echo"), ContextId((), (("message", value),))) for value in ["one", "two"]]
    logger = ActionLoggerPure(keys, output, use_short_ids)
    for key in keys:
        logger.mark_done(key, 1.3)
    stream = StringIO()
    Console(file=stream, width=width, height=12).print(logger._render_checklist())
    rows = stream.getvalue().splitlines()[:2]
    for key, row in zip(keys, rows):
        assert row.lstrip("> ").startswith("✓ echo")
        context = "@" + output.context.format_id(key.context_id, use_short_ids).plain
        assert (context if width == 120 else context[:4]) in row
        assert "1.3s" in row
    assert rows[0] != rows[1]


@pytest.mark.parametrize("width", [40, 80])
def test_checklist_action_time_and_log_columns_stay_aligned(width):
    from rich.cells import cell_len

    output = OutputFormatter(no_color=True, compact=True)
    output._console = Console(file=StringIO(), width=width, height=24, force_terminal=True)
    keys = [ActionKey(ActionId(name), ContextId.from_dict({"platform": platform}))
            for name, platform in [("a", "prod"), ("longer-action-name", "test"), ("構築", "jvm"), ("last", "js")]]
    logger = ActionLoggerPure(keys, output, True)
    logger.mark_running(keys[0])
    logger.mark_done(keys[1], 14.2)
    logger.mark_restored(keys[3], .3)
    for key in keys[:2]:
        logger.write_output(key, "LATEST", "stdout")

    def columns():
        stream = StringIO()
        Console(file=stream, width=width).print(logger._render_checklist())
        rows = stream.getvalue().splitlines()[:4]
        assert all(":" in row for row in rows)
        context_columns = [cell_len(row.split("@", 1)[0]) for row in rows]
        assert len(set(context_columns)) == 1
        return [cell_len(row.split(":", 1)[0]) for row in rows]

    initial = columns()
    assert len(set(initial)) == 1
    logger.selected_index = 2
    logger.mark_running(keys[2])
    logger.mark_done(keys[0], 110.2)
    assert columns() == initial


@pytest.mark.parametrize("encoding", ["ascii", "cp1252", "utf-8"])
def test_dependency_tree_shares_only_exact_action_contexts(encoding):
    context = ContextId((("platform", "linux"),))
    shared = ActionKey.from_name("shared")
    build = ActionKey(ActionId("build"), context)
    other_build = ActionKey(ActionId("build"), ContextId((("platform", "windows"),)))
    test = ActionKey.from_name("test")
    nodes = {key: ActionNode(key, ActionDefinition(key.id.name, [], {}, SourceLocation("test.md", 1, key.id.name)))
             for key in [shared, build, other_build, test]}
    nodes[build].dependencies.add(Dependency(shared))
    nodes[other_build].dependencies.add(Dependency(shared))
    nodes[test].dependencies.add(Dependency(build, weak=True))
    nodes[test].dependencies.add(Dependency(other_build))
    graph = ActionGraph(nodes, {build, other_build, test})
    with TextIOWrapper(BytesIO(), encoding=encoding) as stream:
        output = OutputFormatter(no_color=True, compact=True)
        output._console = Console(file=stream, width=90, force_terminal=False)
        CLI()._visualize_execution_plan(graph, [shared, build, other_build, test], ["build", "test"], output, False, "tree")
        stream.flush()
        result = stream.buffer.getvalue().decode(encoding)
    assert "build (@" + output.context.format_id(build.context_id, False).plain in result
    assert "build (@" + output.context.format_id(other_build.context_id, False).plain in result
    assert result.count("shared; shown above") == 1
    assert "weak" in result
    assert result.count("shared (@global") == 1
    assert result.count("test (@global") == 2
    assert "1. " not in result
    assert "prerequisites first" in result
    assert "goal" in result
    assert "┏" not in result
    assert ("└" if encoding == "utf-8" else "`-") in result


@pytest.mark.parametrize("option,encoding", [("--dry-run", "cp1252"), ("--list-actions", "utf-8")])
def test_pure_information_commands_use_compact_rows(tmp_path, option, encoding):
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (definitions / "actions.md").write_text("# action: shared\n\n```bash\ntrue\n```\n\n# action: build\n\nBuild the package.\n\n```bash\ndep action.shared\ntrue\n```\n")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    env["PYTHONIOENCODING"] = encoding
    result = subprocess.run([sys.executable, "-m", "mudyla", "--without-nix", "--no-color", option, ":build"],
                            cwd=tmp_path, env=env, capture_output=True, text=True, encoding=encoding, timeout=5)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "build" in result.stdout and "shared" in result.stdout
    assert not any(char in result.stdout for char in "┏┓┗┛┃━")
    if option == "--dry-run":
        assert "goal" in result.stdout and ("│" if encoding == "utf-8" else "|") in result.stdout
    else:
        assert "Build the package." in result.stdout


@pytest.mark.parametrize("encoding", ["utf-8", "ascii", "cp1252"])
def test_plan_distinguishes_strong_and_non_strong_incoming_edges(encoding):
    from mudyla.logging.formatters.plan import execution_tree, tree_section
    source, strong, weak, soft = [ActionKey.from_name(name) for name in ["source", "strong", "weak", "soft"]]
    keys = [source, strong, weak, soft]
    nodes = {key: ActionNode(key, ActionDefinition(key.id.name, [], {}, SourceLocation("test.md", 1, key.id.name))) for key in keys}
    nodes[strong].dependencies.add(Dependency(source))
    nodes[weak].dependencies.add(Dependency(source, weak=True))
    nodes[soft].dependencies.add(Dependency(source, soft=True, retainer_action=ActionKey.from_name("keep")))
    graph = ActionGraph(nodes, set(keys[1:]))
    with TextIOWrapper(BytesIO(), encoding=encoding) as stream:
        output = OutputFormatter(no_color=True, compact=True)
        output._console = Console(file=stream, width=100)
        output.print(tree_section(execution_tree(graph, keys, output.context, True, {}, lambda key: Text("+ ")),
                                  output.console.options.ascii_only))
        stream.flush()
        lines = stream.buffer.getvalue().decode(encoding).splitlines()
    unicode = encoding == "utf-8"
    assert next(line for line in lines if "+ strong" in line).startswith("├─" if unicode else "+-")
    assert next(line for line in lines if "+ weak" in line).startswith("├╌" if unicode else "+.")
    assert next(line for line in lines if "+ soft" in line).startswith("└╌" if unicode else "`.")
    assert ("─ strong / ╌ weak or soft" if unicode else "- strong / . weak or soft") in "\n".join(lines)
    assert lines[1].startswith("+ source")


@pytest.mark.parametrize("width", [12, 40, 100])
@pytest.mark.parametrize("encoding", ["ascii", "cp1252", "utf-8"])
def test_mixed_plan_edges_keep_all_nodes_and_stable_rows_during_status_changes(width, encoding):
    from mudyla.logging.formatters.plan import execution_tree
    keys = [ActionKey.from_name(f"node{index}") for index in range(8)]
    nodes = {key: ActionNode(key, ActionDefinition(key.id.name, [], {}, SourceLocation("test.md", 1, key.id.name))) for key in keys}
    for index in range(1, len(keys)):
        nodes[keys[index]].dependencies.add(Dependency(keys[index - 1], weak=index % 3 == 1, soft=index % 3 == 2))
    graph = ActionGraph(nodes, {keys[-1]})
    frames = []
    for glyph in ["+ ", "- "]:
        with TextIOWrapper(BytesIO(), encoding=encoding) as stream:
            output = OutputFormatter(no_color=True, compact=True)
            output._console = Console(file=stream, width=width)
            output.print(execution_tree(graph, keys, output.context, True, {}, lambda key: Text(glyph)))
            stream.flush()
            lines = stream.buffer.getvalue().decode(encoding).splitlines()
        assert all(Text(line).cell_len <= width for line in lines)
        compact = "".join(line.strip() for line in lines)
        assert all(key.id.name in compact for key in keys)
        frames.append(lines)
    assert len(frames[0]) == len(frames[1])
    assert [line.replace("+ ", "- ") for line in frames[0]] == frames[1]


def test_parallel_edges_have_stable_strength_order_and_shared_context_identity():
    from mudyla.logging.formatters.plan import execution_tree
    source = ActionKey.from_name("source")
    context = ContextId((("platform", "linux"),))
    goal = ActionKey.from_name("goal", context)
    child = ActionKey.from_name("child", context)
    keys = [source, goal, child]
    nodes = {key: ActionNode(key, ActionDefinition(key.id.name, [], {}, SourceLocation("test.md", 1, key.id.name))) for key in keys}
    nodes[goal].dependencies.update([Dependency(source, soft=True), Dependency(source), Dependency(source, weak=True)])
    nodes[child].dependencies.add(Dependency(goal, weak=True))
    graph = ActionGraph(nodes, {goal, child})
    output = OutputFormatter(no_color=False, compact=True)
    output._console = Console(file=StringIO(), width=120, force_terminal=True, color_system="standard")
    rows = output.console.render_lines(execution_tree(graph, keys, output.context, True, {}, lambda key: Text("+ ")), pad=False)
    text = [Text.assemble(*[(segment.text, segment.style or "") for segment in row]) for row in rows]
    goal_rows = [row for row in text if "+ goal" in row.plain]
    assert [row.plain[:2] for row in goal_rows] == ["├─", "├╌", "└╌"]
    assert all("@" in row.plain for row in goal_rows)
    assert all("shared; shown above" in row.plain for row in goal_rows[1:])
    assert next(row for row in text if "+ child" in row.plain).plain.startswith("│ └╌")
    assert all(row.get_style_at_offset(output.console, row.plain.index("goal")).bold for row in goal_rows)


@pytest.mark.parametrize("retain,strong,encoding", [(True, False, "utf-8"), (False, False, "utf-8"), (False, True, "cp1252")])
@pytest.mark.parametrize("plan_options", [["--plan-tree"], ["--plan-dag"]])
def test_actual_retainer_plan_uses_declared_strength_and_existing_pruning(tmp_path, retain, strong, encoding, plan_options):
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (definitions / "actions.md").write_text(
        '# action: cache\n\n```python\nprint("CACHE_DONE")\n```\n\n'
        '# action: keep\n\n```python\nfrom pathlib import Path\nPath("retainer-ran").write_text("yes")\n' +
        ('mdl.retain()' if retain else 'pass') + '\n```\n\n'
        '# action: build\n\n```python\nmdl.soft("action.cache", "action.keep")\n' +
        ('mdl.dep("action.cache")\n' if strong else '') + 'print("BUILD_DONE")\n```\n')
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), NO_COLOR="1", COLUMNS="160",
               PYTHONIOENCODING=encoding)
    result = subprocess.run([sys.executable, "-m", "mudyla", "--without-nix", "--simple-log", *plan_options, ":build"],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding=encoding, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    plan = result.stdout.split("Plan:", 1)[1].split("Actions:", 1)[0]
    assert ("cache (@global" in plan) == (retain or strong)
    unicode = encoding == "utf-8"
    if retain or strong:
        soft_edge = ("╎" if unicode else ":") if plan_options == ["--plan-dag"] else ("╌" if unicode else ".")
        assert soft_edge in plan.split("deps ready", 1)[0] and "soft" in plan
    if strong:
        strong_edge = ("│" if unicode else "|") if plan_options == ["--plan-dag"] else ("├─" if unicode else "+-")
        assert strong_edge in plan.split("deps ready", 1)[0]
    assert (tmp_path / "retainer-ran").exists() == (not strong)
