"""Explicit Plan presentation and connected dependency layout."""

import os
from io import BytesIO, StringIO, TextIOWrapper
from itertools import combinations
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
from mudyla.logging.formatters.dag import DagLayout, build_dag_layout, execution_dag
from mudyla.logging.formatters import layered


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


def assert_independent_routes(layout: DagLayout) -> None:
    walks = []
    occupancy = []
    for edge, route in zip(layout.edges, layout.geometry.routes):
        assert (layout.keys[route.source], layout.keys[route.target]) == (edge.source, edge.target)
        points = [route.points[0]]
        directions = {}
        for target in route.points[1:]:
            start = points[-1]
            assert start[0] == target[0] or start[1] == target[1]
            dx = (target[0] > start[0]) - (target[0] < start[0])
            dy = (target[1] > start[1]) - (target[1] < start[1])
            forward, backward = {(1, 0): ("R", "L"), (-1, 0): ("L", "R"), (0, 1): ("D", "U")}[dx, dy]
            while points[-1] != target:
                previous = points[-1]
                following = previous[0] + dx, previous[1] + dy
                directions.setdefault(previous, set()).add(forward)
                directions.setdefault(following, set()).add(backward)
                points.append(following)
        assert len(points) == len(set(points)) and points[0][1] < points[-1][1]
        walks.append(points)
        occupancy.append(directions)
    assert len(walks) == len(layout.edges)
    for first, second in combinations(range(len(walks)), 2):
        a, b = layout.geometry.routes[first], layout.geometry.routes[second]
        allowed = set()
        for endpoint in {a.source, a.target} & {b.source, b.target}:
            first_branch = walks[first] if endpoint == a.source else walks[first][::-1]
            second_branch = walks[second] if endpoint == b.source else walks[second][::-1]
            for start, end in zip(first_branch, second_branch):
                if start != end:
                    break
                allowed.add(start)
        if (a.source, a.target) == (b.source, b.target):
            assert walks[first] != walks[second]
        for point in occupancy[first].keys() & occupancy[second].keys() - allowed:
            assert {frozenset(occupancy[first][point]), frozenset(occupancy[second][point])} == {
                frozenset("UD"), frozenset("LR")}, (point, first, second)


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
    initial_width = output.console.width
    before = logger._overview_prefix()
    logger.mark_running(keys[0])
    assert logger._overview_prefix() == before
    assert calls == [initial_width], "A static Plan must not rebuild when action status changes"
    output.console.width = 80
    resized_width = output.console.width
    logger._overview_prefix()
    assert calls == [initial_width, resized_width]


@pytest.mark.parametrize("width", [100, 12, 8])
def test_unchanged_dag_rows_reuse_rendered_blocks(monkeypatch, width):
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=False, compact=True)
    console = Console(file=StringIO(), width=width, color_system="truecolor", no_color=False)
    dag = execution_dag(graph, keys, output.context, True, {}, lambda key: Text("o "), lambda key: "dim", layout=build_dag_layout(graph, keys))
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
    dag = execution_dag(graph, keys, output.context, True, {}, lambda key: Text("o "), styles.__getitem__, layout=build_dag_layout(graph, keys))
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
    dag = execution_dag(graph, keys, output.context, True, {}, lambda key: Text("o "), lambda key: "dim", layout=build_dag_layout(graph, keys))
    assert list(dag.layout.keys) == keys
    assert {(edge.target, edge.dependency) for edge in dag.edges} == {
        (key, dependency) for key in keys for dependency in graph.get_node(key).dependencies}
    assert_independent_routes(dag.layout)
    for edge, route in zip(dag.edges, dag.layout.geometry.routes):
        assert (keys[route.source], keys[route.target]) == (edge.source, edge.target)
        assert route.points[0][1] < route.points[-1][1]
    stream = StringIO()
    Console(file=stream, width=100).print(dag)
    assert all(stream.getvalue().count(key.id.name + " (@") == 1 for key in keys)
    assert "╎" in stream.getvalue()


def test_layered_routing_keeps_unrelated_bends_separate_and_parallel_strength_visible():
    fixtures = [
        (10, [(0, 2), (1, 3), (0, 4), (1, 4), (3, 4), (2, 5), (1, 6), (2, 6),
              (4, 6), (1, 7), (3, 7), (5, 7), (1, 8), (3, 9), (4, 9), (6, 9)]),
        (2, [(0, 1)]),
    ]
    for count, links in fixtures:
        keys = [ActionKey.from_name(f"node{index}") for index in range(count)]
        nodes = {key: ActionNode(key, ActionDefinition(key.id.name, [], {}, SourceLocation("fixture", 1, key.id.name)))
                 for key in keys}
        for source, target in links:
            nodes[keys[target]].dependencies.add(Dependency(keys[source]))
        if count == 2:
            nodes[keys[1]].dependencies.update([Dependency(keys[0], weak=True), Dependency(keys[0], soft=True)])
        graph = ActionGraph(nodes, {keys[-1]})
        layout = build_dag_layout(graph, keys)
        assert_independent_routes(layout)
        output = OutputFormatter(no_color=True, compact=True)
        dag = execution_dag(graph, keys, output.context, True, {}, lambda key: Text("o "), lambda key: "dim", layout=layout)
        stream = StringIO()
        Console(file=stream, width=160).print(dag)
        if count == 2:
            assert "╎" in stream.getvalue()
            assert [edge.kind for edge in layout.edges] == ["strong", "weak", "soft"]
        narrow = StringIO()
        Console(file=narrow, width=8).print(dag)
        assert all(Text(line).cell_len <= 8 for line in narrow.getvalue().splitlines())
        assert "".join(narrow.getvalue().split()).count("needs") == len(layout.edges)


def test_complex_execution_plan_bounds_connector_rows_and_keeps_each_action_once():
    names = ["setup-env", "setup-jvm-options", "setup-scala", "build", "setup-scala-publish",
             "scala-publish-local", "scala-local"]
    keys = [ActionKey.from_name(name) for name in names]
    nodes = {key: ActionNode(key, ActionDefinition(key.id.name, [], {}, SourceLocation("fixture", 1, key.id.name)))
             for key in keys}
    links = [(0, 2), (0, 3), (1, 3), (2, 3), (2, 4), (3, 4), (0, 5), (1, 5), (2, 5),
             (3, 5), (4, 5), (0, 6), (1, 6), (2, 6), (3, 6), (4, 6), (5, 6)]
    for source, target in links:
        nodes[keys[target]].dependencies.add(Dependency(keys[source], weak=(source, target) == (2, 3)))
    graph = ActionGraph(nodes, {keys[-1]})
    layout = build_dag_layout(graph, keys)
    assert_independent_routes(layout)
    assert sum(band.height for band in layout.geometry.bands) <= 20
    assert max(band.height for band in layout.geometry.bands) <= 4
    output = OutputFormatter(no_color=True, compact=True)
    dag = execution_dag(graph, keys, output.context, True, {}, lambda key: Text("o "), lambda key: "dim", layout=layout)
    stream = StringIO()
    Console(file=stream, width=120).print(dag)
    assert len(stream.getvalue().splitlines()) <= 27
    assert all(stream.getvalue().count(name + " (@") == 1 for name in names)


def test_cli_shares_one_solved_layout_across_preparation_execution_and_resizing(tmp_path, monkeypatch):
    import mudyla.cli as cli_module
    import mudyla.logging.action_logger_pure as pure_module

    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (definitions / "actions.md").write_text('# action: work\n\n```python\npass\n```\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    calls = {}
    for name in ["solve_layered_layout", "_coordinates", "_allocate_routing", "_rasterize"]:
        original = getattr(layered, name)

        def counted(*args, stage=name, function=original, **kwargs):
            calls[stage] = calls.get(stage, 0) + 1
            return function(*args, **kwargs)

        monkeypatch.setattr(layered, name, counted)
    layouts = []
    original_render = execution_dag

    def render(*args, **kwargs):
        layouts.append(kwargs["layout"])
        return original_render(*args, **kwargs)

    monkeypatch.setattr(cli_module, "execution_dag", render)
    monkeypatch.setattr(pure_module, "execution_dag", render)
    original_start = ActionLoggerPure.start

    def start(logger):
        original_start(logger)
        for width, status in [(100, TaskStatus.TBD), (25, TaskStatus.RUNNING), (100, TaskStatus.DONE)]:
            logger.console.size = width, 24
            logger.tasks[logger.action_keys[0]].status = status
            logger._action_lines()

    monkeypatch.setattr(ActionLoggerPure, "start", start)
    assert CLI().run(["--without-nix", ":work"]) == 0
    assert len(layouts) == 2 and layouts[0] is layouts[1]
    assert calls == {name: 1 for name in ["solve_layered_layout", "_coordinates", "_allocate_routing", "_rasterize"]}


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
    dag = execution_dag(ActionGraph(nodes, set(variants)), keys, output.context, False, {}, lambda key: Text("o "), lambda key: "dim", layout=build_dag_layout(ActionGraph(nodes, set(variants)), keys))
    assert list(dag.layout.keys) == keys and len(dag.edges) == 5
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
            dag = execution_dag(graph, keys, output.context, True, {}, lambda key: Text(glyph), lambda key: style, layout=build_dag_layout(graph, keys))
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
    assert before.layout is after.layout
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
        CLI()._visualize_execution_plan(graph, keys, ["package"], output, True, build_dag_layout(graph, keys),
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
    dag = execution_dag(graph, keys, output.context, True, {}, lambda key: Text("o "), lambda key: "dim", layout=build_dag_layout(graph, keys))
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


def test_repeated_forks_keep_compact_routes_and_preserve_edges():
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
    dag = execution_dag(ActionGraph(nodes, {keys[-1]}), keys, output.context, True, {}, lambda key: Text("o "), lambda key: "dim", layout=build_dag_layout(ActionGraph(nodes, {keys[-1]}), keys))
    assert not dag.crossings
    assert max(band.height for band in dag.layout.geometry.bands) <= 4
    assert {(edge.target, edge.dependency) for edge in dag.edges} == set(links)


def test_nonplanar_merges_keep_explicit_nonjoining_crossings():
    keys = [ActionKey.from_name(name) for name in ["a", "b", "c", "x", "y", "z"]]
    nodes = {key: ActionNode(key, ActionDefinition(key.id.name, [], {}, SourceLocation("fixture", 1, key.id.name))) for key in keys}
    for source in keys[:3]:
        for target in keys[3:]:
            nodes[target].dependencies.add(Dependency(source))
    output = OutputFormatter(no_color=True, compact=True)
    dag = execution_dag(ActionGraph(nodes, set(keys[3:])), keys, output.context, True, {}, lambda key: Text("o "), lambda key: "dim", layout=build_dag_layout(ActionGraph(nodes, set(keys[3:])), keys))
    stream = StringIO()
    Console(file=stream, width=100).print(dag)
    assert dag.crossings and "╪" in stream.getvalue()
    assert len(dag.edges) == 9 and len(dag.layout.keys) == 6
