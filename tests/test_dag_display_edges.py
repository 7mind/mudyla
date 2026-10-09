"""Execution-order display reduction preserves authoritative typed dependencies."""

import pytest

from mudyla.cli import CLI
from mudyla.dag.solver.budget import LayoutBudget, OVERALL_BUDGET_SECONDS
from mudyla.dag.display import build_display_edges
from mudyla.dag.graph import ActionKey, Dependency
from mudyla.dag.solver.objective import NativeObjective
from mudyla.dag.solver.graph import build_solver_input
from mudyla.dag.solver.model import NodeSize
from mudyla.logging.formatters.native_dag import RowProjectionObjective
from mudyla.logging.formatters.dag import build_dag_layout
from layout_graphs import fixture


@pytest.mark.parametrize('value,minimize', [('true', True), ('false', False)])
def test_plan_display_minimization_is_explicit(value, minimize):
    assert CLI().parser.parse_args(['--plan-minimize', value]).plan_minimize is minimize


def test_plan_display_defaults_to_minimized():
    assert CLI().parser.parse_args([]).plan_minimize is True


@pytest.mark.parametrize('value', ['yes', '1', 'True', 'FALSE'])
def test_plan_minimize_rejects_non_boolean_tokens(value, capsys):
    with pytest.raises(SystemExit) as failure:
        CLI().parser.parse_args(['--plan-minimize', value])
    assert failure.value.code == 2
    assert 'expected true or false' in capsys.readouterr().err


@pytest.mark.parametrize('path_weak,shortcut_kind,retainer,visible_count', [
    (False, 'strong', False, 2),
    (True, 'strong', False, 2),
    (False, 'weak', False, 2),
    (False, 'soft', True, 2),
    (False, 'strong', True, 2),
])
def test_minimized_ordering_view_reduces_shortcuts_without_mutating_typed_dependencies(
        path_weak, shortcut_kind, retainer, visible_count):
    keys = [ActionKey.from_name(name) for name in ('source', 'middle', 'goal')]
    shortcut = Dependency(keys[0], weak=shortcut_kind == 'weak', soft=shortcut_kind == 'soft',
                          retainer_action=keys[1] if retainer else None)
    links = [(1, Dependency(keys[0], weak=path_weak)), (2, Dependency(keys[1])), (2, shortcut)]
    sample = fixture('display-reduction', keys, links, [2], 120)
    before = {key: frozenset(sample.graph.get_node(key).dependencies) for key in keys}
    solver_input = build_solver_input(sample.graph, keys, {key: NodeSize(1, 1) for key in keys}, display=build_display_edges(sample.graph, tuple(keys), full=False))
    layout = build_dag_layout(sample.graph, keys, display=build_display_edges(sample.graph, tuple(keys), full=False))
    assert len(layout.display.original) == 3
    assert layout.display == solver_input.display
    assert len(layout.edges) == len(solver_input.edges) == visible_count
    assert {key: frozenset(sample.graph.get_node(key).dependencies) for key in keys} == before
    assert len(layout.geometry.routes) == visible_count, 'Redundant ordering constraint still occupies a display rail'
    if visible_count == 2:
        assert {(route.source, route.target) for route in layout.geometry.routes} == {(0, 1), (1, 2)}


def reachable_pairs(edges):
    adjacency = {}
    for source, target in edges:
        adjacency.setdefault(source, set()).add(target)
    result = set()
    for source in adjacency:
        pending = list(adjacency[source])
        seen = set()
        while pending:
            target = pending.pop()
            if target not in seen:
                seen.add(target)
                pending.extend(adjacency.get(target, ()))
        result.update((source, target) for target in seen)
    return result


def test_reported_minimized_view_preserves_all_reachable_action_pairs():
    keys = [ActionKey.from_name(f'action{index}') for index in range(7)]
    endpoints = ((0, 2), (0, 3), (0, 5), (0, 6), (1, 3), (1, 5), (1, 6),
                 (2, 3), (2, 4), (2, 5), (2, 6), (3, 4), (3, 5), (3, 6), (4, 5), (4, 6), (5, 6))
    sample = fixture('reported-ordering', keys,
        [(target, Dependency(keys[source], weak=target == 3)) for source, target in endpoints], [6], 180)
    layout = build_dag_layout(sample.graph, keys, display=build_display_edges(sample.graph, tuple(keys), full=False))
    displayed = {(route.source, route.target) for route in layout.geometry.routes}
    assert len(layout.display.original) == 17
    assert displayed == {(0, 2), (1, 3), (2, 3), (3, 4), (4, 5), (5, 6)}
    assert reachable_pairs(displayed) == reachable_pairs(endpoints)
    assert len(reachable_pairs(displayed)) == 19


def test_parallel_dependency_instances_do_not_count_as_an_alternate_path():
    keys = [ActionKey.from_name(name) for name in ('source', 'goal')]
    links = [(1, Dependency(keys[0])), (1, Dependency(keys[0], weak=True)),
             (1, Dependency(keys[0], soft=True, retainer_action=keys[1]))]
    sample = fixture('parallel-ordering', keys, links, [1], 120)
    layout = build_dag_layout(sample.graph, keys, display=build_display_edges(sample.graph, tuple(keys), full=False))
    assert len(layout.edges) == len(layout.geometry.routes) == 3
    assert {edge.dependency for edge in layout.edges} == {dependency for _, dependency in links}


def test_native_and_default_share_frozen_selection_and_original_ids():
    from mudyla.dag.solver.factory import create_solver
    from mudyla.logging.formatters.native_dag import build_native_row_layout
    keys = [ActionKey.from_name(name) for name in ('source', 'middle', 'goal')]
    sample = fixture('display-sharing', keys, [(1, Dependency(keys[0])), (2, Dependency(keys[0], weak=True)),
                                              (2, Dependency(keys[1]))], [2], 120)
    display = build_display_edges(sample.graph, tuple(keys), full=False)
    full = build_display_edges(sample.graph, tuple(keys), full=True)
    assert len(display.visible) == 2 and len(full.visible) == 3
    assert tuple(display.original[index] for index in display.visible_ids) == display.visible
    model = build_solver_input(sample.graph, keys, {key: NodeSize(1, 1) for key in keys}, display=display)
    native_result = create_solver('dagre', model, objective=NativeObjective(), budget=LayoutBudget(OVERALL_BUDGET_SECONDS)).solve()
    projection = RowProjectionObjective(model)
    budget = LayoutBudget(5)
    budget.start()
    projection.score(native_result.candidates[0], budget)
    native = build_native_row_layout(native_result, projection=projection, preparation_ms=(native_result).solve_ms)
    default = build_dag_layout(sample.graph, keys, display=display)
    assert native.display is default.display is model.display is display
    assert native.native_result.graph.edges is display.visible
    assert {edge.dependency for edge in display.original} == {dependency for key in keys for dependency in sample.graph.get_node(key).dependencies}


def test_narrow_minimized_references_keep_hidden_dependencies_and_readiness_uses_original_graph():
    from io import StringIO
    from rich.console import Console
    from mudyla.logging.action_logger_pure import ActionLoggerPure
    from mudyla.logging.action_logger_table import TaskStatus
    from mudyla.logging.formatters import OutputFormatter
    keys = [ActionKey.from_name(name) for name in ('source', 'middle', 'goal')]
    sample = fixture('hidden-prerequisite', keys, [(1, Dependency(keys[0])), (2, Dependency(keys[0], soft=True, retainer_action=keys[1])),
                                                (2, Dependency(keys[1]))], [2], 120)
    output = OutputFormatter(no_color=True, compact=True, console=Console(file=StringIO(), width=8, no_color=True))
    logger = ActionLoggerPure(keys, output, False, graph=sample.graph)
    logger.tasks[keys[1]].status = TaskStatus.RESTORED
    assert len(logger._dag.edges) == 2
    assert logger._tree_status(keys[2]).plain.strip() == '◌'
    assert logger._plan_edge_style(keys[2]) == 'dim'
    output.console.print(logger._dag)
    compact = ''.join(output.console.file.getvalue().split())
    assert compact.count('needs') == 3 and 'retainer:middle' in compact
    logger.tasks[keys[0]].status = TaskStatus.DONE
    assert logger._tree_status(keys[2]).plain.strip() == '○'
    assert logger._plan_edge_style(keys[2]) == 'not dim'


def test_display_flags_are_available_in_help_and_flag_completion(capsys):
    cli = CLI()
    assert '--plan-minimize' in cli.parser.format_help()
    assert cli.run(['--autocomplete', 'flags']) == 0
    flags = capsys.readouterr().out.splitlines()
    assert '--plan-minimize' in flags and '--plan-dag-solver' in flags
    assert '--dag-full' not in flags and '--dag-minimized' not in flags


@pytest.mark.parametrize('selection,expected', [([], 2), (['--plan-minimize', 'true'], 2), (['--plan-minimize', 'false'], 3)])
@pytest.mark.parametrize('native', [[], ['--plan-dag-solver', 'dagre']])
def test_cli_solves_the_selected_display_once_after_pruning(tmp_path, monkeypatch, selection, expected, native):
    import mudyla.cli as cli_module
    (tmp_path / '.git').mkdir()
    definitions = tmp_path / '.mdl' / 'defs'
    definitions.mkdir(parents=True)
    (definitions / 'actions.md').write_text('''# action: source

```python
pass
```

# action: middle

```python
mdl.dep("action.source")
pass
```

# action: goal

```python
mdl.dep("action.source")
mdl.dep("action.middle")
pass
```
''', encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    selected = []
    layouts = []
    from mudyla.logging.formatters import dag as dag_module, native_dag
    original_selection = dag_module.build_display_edges
    original_layout = native_dag.build_native_row_layout

    def display(*args, **kwargs):
        value = original_selection(*args, **kwargs)
        selected.append(value)
        return value

    def layout(*args, **kwargs):
        value = original_layout(*args, **kwargs)
        layouts.append(value)
        return value

    monkeypatch.setattr(dag_module, 'build_display_edges', display)
    monkeypatch.setattr(native_dag, 'build_native_row_layout', layout)
    assert CLI().run(['--without-nix', '--dry-run', *selection, *native, ':goal']) == 0
    assert len(selected) == len(layouts) == 1
    assert layouts[0].display is selected[0]
    assert len(selected[0].original) == 3
    assert len(selected[0].visible) == len(layouts[0].geometry.routes) == expected


def test_auto_minimized_choice_minimizes_the_displayed_gutter_objective():
    from dataclasses import replace
    from mudyla.dag.solver.factory import create_solver
    from mudyla.logging.formatters.native_dag import build_native_row_layout
    keys = [ActionKey.from_name(f'action{index}') for index in range(7)]
    endpoints = ((0, 2), (0, 3), (0, 5), (0, 6), (1, 3), (1, 5), (1, 6),
                 (2, 3), (2, 4), (2, 5), (2, 6), (3, 4), (3, 5), (3, 6), (4, 5), (4, 6), (5, 6))
    sample = fixture('auto-displayed-score', keys,
        [(target, Dependency(keys[source], weak=target == 3)) for source, target in endpoints], [6], 180)
    display = build_display_edges(sample.graph, tuple(keys), full=False)
    model = build_solver_input(sample.graph, keys, {key: NodeSize(1, 1) for key in keys}, display=display)
    projection = RowProjectionObjective(model)
    result = create_solver('auto', model, objective=projection, budget=LayoutBudget(OVERALL_BUDGET_SECONDS)).solve()

    def objective(components, order):
        geometry = build_native_row_layout(replace(result, components=components), projection=projection, preparation_ms=(replace(result, components=components)).solve_ms).geometry
        rows = len(keys) + sum(map(len, geometry.connector_rows))
        return (0, geometry.crossings, geometry.width * rows)

    eligible = [(candidate, index) for index, candidate in enumerate(result.candidates) if candidate.selection_score is not None]
    winner, order = min(eligible, key=lambda item: objective(item[0].components, item[1]))
    selected_order = next(index for candidate, index in eligible if candidate.mode == result.selected)
    assert objective(result.components, selected_order) == objective(winner.components, order), (
        f'Auto chose {result.selected}, but {winner.mode} has a smaller displayed gutter objective')


def test_direct_cli_projection_timeout_reports_completed_solver_attempts(tmp_path, monkeypatch, capsys):
    import mudyla.cli as cli_module
    from mudyla.dag.solver import budget as budget_module
    from mudyla.dag.solver.base import OverallTimeout
    clock = [0.0]
    monkeypatch.setattr(budget_module.time, 'monotonic', lambda: clock[0])
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
    monkeypatch.chdir(tmp_path)
    attempts = []
    failures = []

    def timeout(phase, history):
        value = OverallTimeout(phase, history)
        failures.append(value)
        return value

    def expire(self, candidate, budget):
        attempts.extend(candidate.attempts)
        clock[0] = 6.0
        budget.check('row_columns')

    monkeypatch.setattr(RowProjectionObjective, 'score', expire)
    from mudyla.dag.solver import base as base_module
    monkeypatch.setattr(base_module, 'OverallTimeout', timeout)
    assert CLI().run(['--without-nix', '--dry-run', '--plan-dag-solver', 'dagre', ':goal']) == 1
    captured = capsys.readouterr()
    assert attempts and all(attempt.complete for attempt in attempts)
    assert 'row_columns' in captured.out
    assert 'Traceback' not in captured.err, 'Direct projection timeout escaped the domain failure boundary'
    assert len(failures) == 1 and failures[0].attempts == tuple(attempts)
    assert isinstance(failures[0].__cause__, budget_module.BudgetExpired)


def test_default_cli_projection_timeout_reports_domain_failure(tmp_path, monkeypatch, capsys):
    import mudyla.cli as cli_module
    from mudyla.dag.solver import budget as budget_module
    from mudyla.dag.solver.base import OverallTimeout
    from mudyla.logging.formatters import layered
    clock = [0.0]
    monkeypatch.setattr(budget_module.time, 'monotonic', lambda: clock[0])
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
    monkeypatch.chdir(tmp_path)
    failures = []

    def expire(columns, edges, budget):
        clock[0] = budget.seconds + 1.0
        budget.check('row_tracks')

    def timeout(phase, history):
        value = OverallTimeout(phase, history)
        failures.append(value)
        return value

    monkeypatch.setattr(layered, '_allocate_routing', expire)
    import mudyla.dag.solver.grid as grid_module
    monkeypatch.setattr(grid_module, 'OverallTimeout', timeout)
    assert CLI().run(['--without-nix', '--dry-run', ':goal']) == 1
    captured = capsys.readouterr()
    assert 'row_tracks' in captured.out
    assert 'Traceback' not in captured.err, 'Default projection timeout escaped the domain failure boundary'
    assert len(failures) == 1 and failures[0].attempts
    assert failures[0].attempts[-1].timed_out
    assert failures[0].attempts[-1].phase == 'row_tracks'


@pytest.mark.parametrize('expiry', ['later_native_candidate', 'later_selection_candidate'])
def test_auto_retains_complete_row_geometry_when_later_work_expires(monkeypatch, expiry):
    from mudyla.dag.solver import budget as budget_module, grid
    from mudyla.dag.solver.budget import BudgetExpired
    from mudyla.dag.solver.factory import create_solver
    from mudyla.logging.formatters.native_dag import build_native_row_layout
    clock = [0.0]
    monkeypatch.setattr(budget_module.time, 'monotonic', lambda: clock[0])
    keys = [ActionKey.from_name(name) for name in ('source', 'goal')]
    sample = fixture('row-selection-expiry', keys, [(1, Dependency(keys[0]))], [1], 120)
    model = build_solver_input(sample.graph, keys, {key: NodeSize(1, 1) for key in keys},
                               display=build_display_edges(sample.graph, tuple(keys), full=False))
    projection = RowProjectionObjective(model)
    solver = create_solver('auto', model, objective=projection, budget=LayoutBudget(OVERALL_BUDGET_SECONDS))
    if expiry == 'later_native_candidate':
        original_native = grid.score_native_layout
        completed_native = []
        def expire(*args):
            if completed_native:
                clock[0] = 6.0
                raise BudgetExpired(expiry)
            value = original_native(*args)
            completed_native.append(value)
            return value
        monkeypatch.setattr(grid, 'score_native_layout', expire)
    else:
        completed = []
        original = projection.score

        def score(candidate, budget):
            if completed:
                clock[0] = 6.0
                raise BudgetExpired(expiry)
            value = original(candidate, budget)
            completed.append(candidate.mode)
            return value
        monkeypatch.setattr(projection, 'score', score)
    result = solver.solve()
    assert result.selected == 'grid-opt'
    assert any(attempt.timed_out for attempt in result.attempts)
    layout = build_native_row_layout(result, projection=projection, preparation_ms=(result).solve_ms)
    assert layout.geometry is build_native_row_layout(result, projection=projection, preparation_ms=(result).solve_ms).geometry
    assert solver.solve() is result




def test_auto_projection_cache_routes_each_distinct_column_arrangement_once(monkeypatch):
    from mudyla.dag.solver.factory import create_solver
    from mudyla.logging.formatters import layered
    from mudyla.logging.formatters.native_dag import build_native_row_layout
    keys = [ActionKey.from_name(name) for name in ('source', 'left', 'right', 'goal')]
    sample = fixture('projection-cache', keys, [(1, Dependency(keys[0])), (2, Dependency(keys[0])),
                                              (3, Dependency(keys[1])), (3, Dependency(keys[2]))], [3], 120)
    model = build_solver_input(sample.graph, keys, {key: NodeSize(1, 1) for key in keys},
                               display=build_display_edges(sample.graph, tuple(keys), full=False))
    counts = {}
    for name in ('_allocate_routing', '_rasterize_routes'):
        original = getattr(layered, name)
        def counted(*args, name=name, function=original, **kwargs):
            counts[name] = counts.get(name, 0) + 1
            return function(*args, **kwargs)
        monkeypatch.setattr(layered, name, counted)
    projection = RowProjectionObjective(model)
    solver = create_solver('auto', model, objective=projection, budget=LayoutBudget(OVERALL_BUDGET_SECONDS))
    result = solver.solve()
    eligible = [candidate for candidate in result.candidates if candidate.selection_score is not None]
    for candidate in eligible:
        from dataclasses import replace
        layout = build_native_row_layout(replace(result, components=candidate.components), projection=projection, preparation_ms=(replace(result, components=candidate.components)).solve_ms)
        assert candidate.selection_score.crossings == layout.geometry.crossings
        rows = len(keys) + sum(map(len, layout.geometry.connector_rows))
        assert candidate.selection_score.normalized_area == layout.geometry.width * rows / len(keys)
    assert counts == {name: len(projection._layouts) for name in counts}
    before = dict(counts)
    geometry = build_native_row_layout(result, projection=projection, preparation_ms=(result).solve_ms).geometry
    assert build_native_row_layout(result, projection=projection, preparation_ms=(result).solve_ms).geometry is geometry
    assert solver.solve() is result and counts == before


def test_auto_objective_failure_preserves_completed_attempts_without_launching_later_solver(monkeypatch):
    from mudyla.dag.solver.base import SolverFailure
    from mudyla.dag.solver.factory import create_solver
    keys = [ActionKey.from_name(name) for name in ('source', 'goal')]
    sample = fixture('objective-failure', keys, [(1, Dependency(keys[0]))], [1], 120)
    model = build_solver_input(sample.graph, keys, {key: NodeSize(1, 1) for key in keys},
                               display=build_display_edges(sample.graph, tuple(keys), full=False))
    class FaultObjective:
        def score(self, candidate, budget):
            raise ValueError('objective failure')
    solver = create_solver('auto', model, objective=FaultObjective(), budget=LayoutBudget(OVERALL_BUDGET_SECONDS))
    with pytest.raises(SolverFailure, match='objective failure') as failure:
        solver.solve()
    assert isinstance(failure.value.__cause__, ValueError)
    assert failure.value.attempts and failure.value.attempts[0].mode == 'grid-opt'
    assert not failure.value.attempts[0].complete
    assert failure.value.attempts[0].phase == 'evaluation'
    assert solver.search.evaluated_assignments == ()


@pytest.mark.parametrize('plan', ['tree', 'table'])
def test_tree_minimization_and_full_table_bypass_dag_solver(tmp_path, monkeypatch, capsys, plan):
    import mudyla.cli as cli_module
    (tmp_path / '.git').mkdir()
    definitions = tmp_path / '.mdl' / 'defs'
    definitions.mkdir(parents=True)
    (definitions / 'actions.md').write_text('''# action: source

```python
pass
```

# action: middle

```python
mdl.dep("action.source")
pass
```

# action: goal

```python
mdl.dep("action.source")
mdl.dep("action.middle")
pass
```
''', encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    from mudyla.logging.formatters import dag as dag_module
    monkeypatch.setattr(dag_module, 'create_solver', lambda *args, **kwargs: pytest.fail('Non-DAG plan invoked a solver'))
    selections = []
    original = cli_module.build_display_edges

    def select(*args, **kwargs):
        value = original(*args, **kwargs)
        selections.append(value)
        return value

    monkeypatch.setattr(cli_module, 'build_display_edges', select)
    plans = []
    for minimize in ('true', 'false'):
        assert CLI().run(['--without-nix', '--no-color', '--dry-run', '--plan', plan,
                          '--plan-minimize', minimize, ':goal']) == 0
        plans.append(capsys.readouterr().out.split('Plan:\n', 1)[1])
    if plan == 'tree':
        assert [len(display.visible) for display in selections] == [2, 3]
        assert [text.count('goal (') for text in plans] == [1, 2]
        assert all(len(display.original) == 3 for display in selections)
    else:
        assert selections == []
        assert plans[0] == plans[1]
        assert 'Deps' in plans[0] and '1,2' in ''.join(plans[0].split())
