"""Explicit Plan presentation and connected dependency layout."""

import os
from io import BytesIO, StringIO, TextIOWrapper
from pathlib import Path
import re
import subprocess
import sys

import pytest
from rich.console import Console
from rich.text import Text

from mudyla.ast.models import ActionDefinition, SourceLocation
from mudyla.cli import CLI
from mudyla.dag.context import ContextId
from mudyla.dag.graph import ActionGraph, ActionKey, ActionNode, Dependency
from mudyla.logging.action_logger_pure import ActionLoggerPure
from mudyla.logging.action_logger_table import TaskStatus
from mudyla.logging.formatters import OutputFormatter
from mudyla.logging.formatters.dag import DagEdge, DagRow, execution_dag


@pytest.mark.parametrize("option", ["--plan-table", "--plan-tree", "--plan-dag"])
def test_plan_options_are_builtin_flags(option):
    _, unknown = CLI().parser.parse_known_args([option])
    assert unknown == []


@pytest.mark.parametrize("options", [["--plan-table", "--plan-tree"], ["--plan-table", "--plan-dag"], ["--plan-tree", "--plan-dag"]])
def test_plan_options_are_mutually_exclusive(capsys, options):
    with pytest.raises(SystemExit) as error:
        CLI().parser.parse_args(options)
    assert error.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["pure", "table", "simple", "verbose", "github", "teamcity"])
@pytest.mark.parametrize("dry", [False, True])
@pytest.mark.parametrize("selection", [[], ["--plan-dag"]])
def test_all_loggers_render_each_dag_action_once(tmp_path, mode, dry, selection):
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    actions = {"source": [], "left": ["source"], "right": ["source"], "goal": ["left", "right"]}
    (definitions / "actions.md").write_text("\n\n".join(
        f'# action: {name}\n\n```python\n' +
        "\n".join(f'mdl.dep("action.{dependency}")' for dependency in dependencies) +
        '\npass\n```' for name, dependencies in actions.items()), encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), NO_COLOR="1", COLUMNS="160")
    result = subprocess.run([sys.executable, "-m", "mudyla", "--without-nix", "--logger", mode,
        *(["--force-interactive", "--plan-dag"] if mode == "table" and not selection else ["--force-interactive"] if mode == "table" else []), *selection, *(["--dry-run"] if dry else []), ":goal"], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    if mode == "teamcity":
        from mudyla.logging.teamcity import parse_message
        messages = [parse_message(line) for line in result.stdout.splitlines()]
        assert all(message is not None for message in messages), result.stdout
        displayed = "".join(message.attributes.get("text", "") for message in messages if message is not None)
    else:
        displayed = result.stdout
    heading = "Actions:\n" if mode == "pure" and not dry else "Plan:\n"
    assert heading in displayed, displayed
    plan = displayed.split(heading, 1)[1].split("Result:", 1)[0].split("prerequisites first", 1)[0]
    for name in actions:
        assert sum(bool(re.search(rf"\s{re.escape(name)}(?:\s|$)", line)) for line in plan.splitlines()) == 1, plan


def crossing_graph():
    keys = [ActionKey.from_name(name) for name in ["source", "compile", "lint", "cache", "package"]]
    nodes = {key: ActionNode(key, ActionDefinition(key.id.name, [], {}, SourceLocation("fixture", 1, key.id.name))) for key in keys}
    for source, target, weak in [(0, 1, False), (0, 3, False), (1, 2, False), (1, 4, True), (2, 4, False), (3, 4, False)]:
        nodes[keys[target]].dependencies.add(Dependency(keys[source], weak=weak))
    return ActionGraph(nodes, {keys[-1]}), keys


@pytest.mark.parametrize("mode", ["pure", "table", "simple", "verbose", "github", "teamcity"])
@pytest.mark.parametrize("dry", [False, True])
def test_explicit_table_plan_is_rendered_by_cli_modes(tmp_path, mode, dry):
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (definitions / "actions.md").write_text(
        '# action: base\n```python\npass\n```\n\n'
        '# action: work\n```python\nmdl.dep("action.base")\npass\n```\n')
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), NO_COLOR="1", COLUMNS="100")
    result = subprocess.run([sys.executable, "-m", "mudyla", "--without-nix", "--logger", mode, "--plan-table",
                             *(["--force-interactive"] if mode == "table" else []),
                             *(["--dry-run"] if dry else []), ":work"], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    if mode == "teamcity":
        from mudyla.logging.teamcity import parse_message
        messages = [parse_message(line) for line in result.stdout.splitlines()]
        assert all(message is not None for message in messages)
        displayed = "".join(message.attributes.get("text", "") for message in messages if message is not None)
    else:
        displayed = result.stdout
    plan = displayed.split("Plan:", 1)[1].split("Deps: prerequisite row", 1)[0]
    for column in ["Context", "Action", "Goal", "Deps", "Shared"]:
        assert column in plan
    assert re.search(r"\b1\b.*base", plan) and re.search(r"\b2\b.*work.*\b1\b", plan)
    assert "deps ready" not in displayed


@pytest.mark.parametrize("completed", [False, True])
def test_pure_explicit_table_plan_remains_a_table_in_live_and_final_views(completed):
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=True, compact=True)
    output._console = Console(file=StringIO(), width=100, height=40)
    logger = ActionLoggerPure(keys, output, True, graph=graph, plan_style="table")
    if completed:
        logger.stop_flag = True
        output.console.print(logger._build_renderable())
        displayed = output.console.file.getvalue()
    else:
        displayed = "\n".join("".join(segment.text for segment in line) for line in logger._overview_prefix())
    for heading in ["Context", "Action", "Goal", "Deps", "Shared"]:
        assert heading in displayed
    assert "deps ready" not in displayed


def test_pure_static_table_plan_reuses_prefix_until_terminal_width_changes(monkeypatch):
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=True, compact=True)
    output._console = Console(file=StringIO(), width=100, height=40)
    logger = ActionLoggerPure(keys, output, True, graph=graph, plan_style="table")
    calls = []
    original = logger._plan_section

    def plan():
        calls.append(output.console.width)
        return original()

    monkeypatch.setattr(logger, "_plan_section", plan)
    before = logger._overview_prefix()
    logger.mark_running(keys[0])
    assert logger._overview_prefix() == before
    assert calls == [100], "A static Plan must not rebuild when action status changes"
    output.console.width = 80
    logger._overview_prefix()
    assert calls == [100, 80]


@pytest.mark.parametrize("width", [100, 12, 8])
def test_unchanged_dag_rows_reuse_rendered_blocks(monkeypatch, width):
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=False, compact=True)
    console = Console(file=StringIO(), width=width, color_system="truecolor", no_color=False)
    dag = execution_dag(graph, keys, output.context, True, {}, lambda key: Text("o "), lambda key: "dim")
    first = dag.visual_lines(console, console.options, lambda key, width: dag._label(key))
    renders = []
    original = console.render_lines

    def render(*args, **kwargs):
        renders.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(console, "render_lines", render)
    second = dag.visual_lines(console, console.options, lambda key, width: dag._label(key))
    assert second == first
    assert len(renders) <= 1, "Only the narrow-view heading may render again"


def test_dag_cache_invalidation_includes_base_style_justification_and_incident_edges():
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=False, compact=True)
    console = Console(file=StringIO(), width=100, color_system="truecolor", no_color=False)
    styles = {key: "dim" for key in keys}
    dag = execution_dag(graph, keys, output.context, True, {}, lambda key: Text("o "), styles.__getitem__)
    label = Text("same", style="blue")
    baseline = dag.visual_lines(console, console.options, lambda key, width: label)
    label.style = "green on #f2f2f2"
    label.justify = "left"
    styles[keys[-1]] = "green not dim"
    updated = dag.visual_lines(console, console.options, lambda key, width: label)
    assert updated != baseline
    dag._rendered_rows.clear()
    assert updated == dag.visual_lines(console, console.options, lambda key, width: label)
    assert len(dag._rendered_rows) == len(keys)


def test_dag_routes_each_declared_edge_without_joining_unrelated_crossing():
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=True, compact=True)
    dag = execution_dag(graph, keys, output.context, True, {}, lambda key: Text("o "), lambda key: "dim")
    assert [row.key for row in dag.rows] == keys
    assert {(edge.target, edge.dependency) for edge in dag.edges} == {
        (key, dependency) for key in keys for dependency in graph.get_node(key).dependencies}
    for edge in dag.edges:
        start, end = keys.index(edge.source), keys.index(edge.target)
        assert edge in dag.rows[start].after and edge in dag.rows[end].before
        assert all(edge in row.entry and edge in row.before and edge in row.after for row in dag.rows[start + 1:end])
    for row in dag.rows:
        assert [edge for edge in row.entry if edge is not None] == [edge for edge in row.before if edge is not None]
        assert {edge for edge in row.after if edge is not None} == {
            edge for edge in row.before if edge is not None and edge.target != row.key
        } | {edge for edge in dag.edges if edge.source == row.key}
    stream = StringIO()
    Console(file=stream, width=100).print(dag)
    assert stream.getvalue().splitlines() == [
        "o─╮   source (@global)", "│ │  ", "│ ╰─╮", "o╌╮ │ compile (@global)", "│ ╎ │",
        "o ╎ │ lint (@global)", "│ ╎ │", "│ ╎ o cache (@global)", "│ ╎ │",
        "o─┴─╯ package (@global; goal)"]


def test_dag_preserves_contexts_parallel_strengths_retainers_and_pruned_endpoints():
    source = ActionKey.from_name("source")
    variants = [ActionKey.from_name("build", ContextId.from_dict({"platform": value})) for value in ["linux", "windows"]]
    keys = [source, *variants]
    nodes = {key: ActionNode(key, ActionDefinition(key.id.name, [], {}, SourceLocation("fixture", 1, key.id.name))) for key in keys}
    retainers = [ActionKey.from_name("keep", ContextId.from_dict({"mode": value})) for value in ["a", "b"]]
    nodes[variants[0]].dependencies.update([Dependency(source), Dependency(source, weak=True),
        *(Dependency(source, soft=True, retainer_action=key) for key in retainers)])
    nodes[variants[1]].dependencies.update([Dependency(source), Dependency(ActionKey.from_name("pruned"), weak=True)])
    output = OutputFormatter(no_color=True, compact=True)
    dag = execution_dag(ActionGraph(nodes, set(variants)), keys, output.context, False, {}, lambda key: Text("o "), lambda key: "dim")
    assert [row.key for row in dag.rows] == keys and len(dag.edges) == 5
    assert [edge.kind for edge in dag.edges] == ["strong", "weak", "soft", "soft", "strong"]
    assert [edge.dependency.retainer_action for edge in dag.edges if edge.kind == "soft"] == retainers
    stream = StringIO()
    Console(file=stream, width=160).print(dag)
    assert stream.getvalue().count("build (@") == 2
    assert all(output.context.format_id(key.context_id, False).plain in stream.getvalue() for key in variants)
    assert "pruned" not in stream.getvalue()
    narrow = StringIO()
    Console(file=narrow, width=9).print(dag)
    compact = "".join(narrow.getvalue().split())
    assert compact.count("retainer:keep") == 2
    assert "retainedby" not in compact


@pytest.mark.parametrize("encoding", ["utf-8", "ascii", "cp1252"])
@pytest.mark.parametrize("width", [5, 12, 40, 100])
def test_dag_width_encoding_and_status_updates_preserve_geometry_and_references(width, encoding):
    graph, keys = crossing_graph()
    frames = []
    for glyph, style in [("o ", "dim"), ("+ ", "cyan not dim")]:
        with TextIOWrapper(BytesIO(), encoding=encoding) as stream:
            output = OutputFormatter(no_color=True, compact=True)
            output._console = Console(file=stream, width=width)
            dag = execution_dag(graph, keys, output.context, True, {}, lambda key: Text(glyph), lambda key: style)
            output.print(dag)
            stream.flush()
            lines = stream.buffer.getvalue().decode(encoding).splitlines()
        assert all(Text(line).cell_len <= width for line in lines)
        joined = "".join(line.strip() for line in lines)
        assert all(key.id.name in joined for key in keys)
        if width == 5:
            assert "toonarrow" in joined
            assert joined.count("needs") == len(dag.edges)
        frames.append(lines)
    assert len(frames[0]) == len(frames[1])
    assert [line.replace("o", "+", 1) if re.match(r"^[│|╎: ]*o", line) else line for line in frames[0]] == frames[1]


@pytest.mark.parametrize("status", list(TaskStatus))
def test_live_dag_keeps_geometry_and_styles_edges_by_dependent_status(status):
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=False, compact=True)
    output._console = Console(file=StringIO(), width=100, force_terminal=True, color_system="standard")
    logger = ActionLoggerPure(keys, output, True, graph=graph, plan_style="dag")
    before = logger._plan_section().renderables[1]
    logger.tasks[keys[1]].status = status
    logger.tasks[keys[0]].status = TaskStatus.DONE
    after = logger._plan_section().renderables[1]
    assert before.rows == after.rows
    assert logger._plan_edge_style(keys[3]) == "dim"
    assert ("not dim" in logger._plan_edge_style(keys[1])) == (status not in {TaskStatus.TBD, TaskStatus.SKIPPED})
    rows = output.console.render_lines(after, pad=False)
    for key in keys:
        line = next(Text.assemble(*[(segment.text, segment.style or "") for segment in row])
                    for row in rows if f" {key.id.name} " in "".join(segment.text for segment in row))
        assert bool(line.get_style_at_offset(output.console, line.plain.index(key.id.name)).bold) == (key in graph.goals)


@pytest.mark.parametrize("compact", [False, True])
def test_unspecified_plan_matches_explicit_dag_and_retains_tree_alternative(compact):
    graph, keys = crossing_graph()
    frames = []
    for selection in [None, "dag", "tree"]:
        output = OutputFormatter(no_color=True, compact=compact)
        output._console = Console(file=StringIO(), width=100)
        CLI()._visualize_execution_plan(graph, keys, ["package"], output, True,
                                        **({"plan_style": selection} if selection else {}))
        frames.append(output.console.file.getvalue())
    cli = CLI()
    args = cli.parser.parse_args([])
    cli._apply_platform_defaults(args, True)
    assert args.plan_style == "dag"
    assert frames[0] == frames[1]
    assert "Plan:" in frames[2] and "shared; shown above" in frames[2]


@pytest.mark.parametrize("width", [9, 12, 100])
def test_goal_annotations_keep_normal_weight_when_labels_wrap(width):
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=False, compact=True)
    output._console = Console(file=StringIO(), width=width, force_terminal=True, color_system="standard")
    dag = execution_dag(graph, keys, output.context, True, {}, lambda key: Text("o "), lambda key: "dim")
    label = dag._label(next(iter(graph.goals)))
    assert label.get_style_at_offset(output.console, 0).bold
    in_annotation = False
    for segments in output.console.render_lines(label, pad=False):
        for segment in segments:
            for char in segment.text:
                in_annotation = in_annotation or char == "("
                if in_annotation and not char.isspace():
                    assert segment.style.bold is not True
                    assert segment.style.dim is True


@pytest.mark.parametrize("direction", [-1, 1])
def test_uniform_lane_shifts_use_two_connected_rows(direction):
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=False, compact=True)
    styles = dict(zip(keys, ["dim", "cyan not dim", "green not dim", "red not dim", "blue not dim"]))
    dag = execution_dag(graph, keys, output.context, True, {}, lambda key: Text("o "), styles.__getitem__)
    dag.lane_count = 5
    edges = [DagEdge(keys[0], key, Dependency(keys[0], weak=index == 1, soft=index == 2))
             for index, key in enumerate(keys[1:])]
    entry = (*edges[:3], None, edges[3])
    before = (None, *edges[:3], edges[3])
    if direction < 0:
        entry, before = before, entry
    row = DagRow(keys[0], 0, entry, before, before)
    lines = dag._transitions(row, 2, False)
    assert len(lines) == 2
    destinations = {edge: lane * 2 for lane, edge in enumerate(before) if edge is not None}
    current = {lane * 2: edge for lane, edge in enumerate(entry) if edge is not None}
    for line in lines:
        following = {}
        occupied = set()
        for column, edge in current.items():
            target = column if column == destinations[edge] else column + direction
            cells = {column, target}
            assert not occupied.intersection(cells)
            occupied.update(cells)
            if target == column:
                assert line.plain[column] == "│"
            else:
                assert line.plain[column] == ("╰" if direction > 0 else "╯")
                assert line.plain[target] == ("╮" if direction > 0 else "╭")
            for cell in cells:
                assert line.get_style_at_offset(output.console, cell) == output.console.get_style(styles[edge.target])
            following[target] = edge
        current = following
    assert current == {lane * 2: edge for lane, edge in enumerate(before) if edge is not None}
    assert len(dag._transitions(row, 1, False)) == 3
    assert len(dag._transitions(row, 2, True)) == 3


def test_four_parallel_branches_do_not_cross_unrelated_sibling_lanes(tmp_path):
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    actions = {"fetch": [], "gen-a": ["fetch"], "gen-z": ["fetch"],
               "compile-a1": ["gen-a"], "compile-a2": ["gen-a"],
               "compile-z1": ["gen-z"], "compile-z2": ["gen-z"]}
    (definitions / "actions.md").write_text("\n\n".join(
        f'# action: {name}\n\n```python\n' +
        "\n".join(f'mdl.dep("action.{dependency}")' for dependency in dependencies) +
        '\npass\n```' for name, dependencies in actions.items()), encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), NO_COLOR="1", COLUMNS="160")
    result = subprocess.run([sys.executable, "-m", "mudyla", "--without-nix", "--simple-log", "--plan-dag", "--dry-run",
        *(':' + name for name in actions if name.startswith("compile"))], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    plan = result.stdout.split("Plan:\n", 1)[1].split("deps ready", 1)[0]
    assert "╪" not in plan, plan


def test_repeated_forks_keep_two_lanes_and_shifts_preserve_edges():
    keys = [ActionKey.from_name("root")]
    links = []
    previous = keys[0]
    for index in range(30):
        leaf, continuation = [ActionKey.from_name(f"{name}{index}") for name in ["leaf", "chain"]]
        keys.extend([leaf, continuation])
        links.extend([(leaf, Dependency(previous)), (continuation, Dependency(previous, weak=True))])
        previous = continuation
    nodes = {key: ActionNode(key, ActionDefinition(key.id.name, [], {}, SourceLocation("fixture", 1, key.id.name))) for key in keys}
    for target, dependency in links:
        nodes[target].dependencies.add(dependency)
    output = OutputFormatter(no_color=True, compact=True)
    dag = execution_dag(ActionGraph(nodes, {keys[-1]}), keys, output.context, True, {}, lambda key: Text("o "), lambda key: "dim")
    assert dag.lane_count == 2
    assert not dag.crossings
    for row in dag.rows:
        dag._transitions(row, 2, False)
        dag._transitions(row, 1, True)
    assert {(edge.target, edge.dependency) for edge in dag.edges} == set(links)


def test_nonplanar_merges_keep_explicit_nonjoining_crossings():
    keys = [ActionKey.from_name(name) for name in ["a", "b", "c", "x", "y", "z"]]
    nodes = {key: ActionNode(key, ActionDefinition(key.id.name, [], {}, SourceLocation("fixture", 1, key.id.name))) for key in keys}
    for source in keys[:3]:
        for target in keys[3:]:
            nodes[target].dependencies.add(Dependency(source))
    output = OutputFormatter(no_color=True, compact=True)
    dag = execution_dag(ActionGraph(nodes, set(keys[3:])), keys, output.context, True, {}, lambda key: Text("o "), lambda key: "dim")
    stream = StringIO()
    Console(file=stream, width=100).print(dag)
    assert dag.crossings and "╪" in stream.getvalue()
    assert len(dag.edges) == 9 and len(dag.rows) == 6
