"""Whole-graph Grid effort and committed displayed incumbents."""

import itertools

import pytest

from layout_graphs import fixture
from mudyla.dag.display import build_display_edges
from mudyla.dag.graph import ActionKey, Dependency
from mudyla.dag.solver.factory import create_solver
from mudyla.dag.solver.graph import build_solver_input
from mudyla.dag.solver.base import OverallTimeout, SolverFailure
from mudyla.dag.solver.budget import BudgetExpired, LayoutBudget, OVERALL_BUDGET_SECONDS
from mudyla.dag.solver.grid import GRID_EFFORT_LIMITS, GridSearch
from mudyla.dag.solver.model import LayoutScore, NodeSize
from mudyla.logging.formatters.native_dag import RowProjectionObjective, build_native_row_layout


def chain_input(count):
    keys = [ActionKey.from_name(f'node{index}') for index in range(count)]
    sample = fixture('grid-effort', keys, [(index, Dependency(keys[index - 1]))
                                          for index in range(1, count)], [count - 1], 180)
    return build_solver_input(sample.graph, keys, {key: NodeSize(1, 1) for key in keys},
                              display=build_display_edges(sample.graph, tuple(keys), full=True))


def test_auto_enumerates_one_thousand_independent_components_without_recursion():
    keys = [ActionKey.from_name(f"node{index}") for index in range(1000)]
    sample = fixture("independent", keys, [], list(range(1000)), 180)
    graph = build_solver_input(sample.graph, keys, {key: NodeSize(1, 1) for key in keys},
                               display=build_display_edges(sample.graph, tuple(keys), full=True))
    projection = RowProjectionObjective(graph)
    solver = create_solver("auto", graph, objective=projection, budget=LayoutBudget(OVERALL_BUDGET_SECONDS))
    result = solver.solve()
    assert result.progress.exhausted and result.progress.evaluated_layouts == 1
    assert solver.solve() is result
    assert len(result.components) == len(result.keys) == 1000
    assert projection.geometry(result) is projection.geometry(result)


def test_independent_component_input_preparation_scales_linearly(monkeypatch):
    original_hash = ActionKey.__hash__

    def count_hashes(count):
        keys = [ActionKey.from_name(f"node{index}") for index in range(count)]
        sample = fixture("independent-input", keys, [], list(range(count)), 180)
        sizes = {key: NodeSize(1, 1) for key in keys}
        display = build_display_edges(sample.graph, tuple(keys), full=True)
        calls = 0

        def counted_hash(key):
            nonlocal calls
            calls += 1
            return original_hash(key)

        with monkeypatch.context() as scoped:
            scoped.setattr(ActionKey, "__hash__", counted_hash)
            graph = build_solver_input(sample.graph, keys, sizes, display=display)
        assert graph.components == tuple((index,) for index in range(count))
        assert graph.execution_order == graph.presentation_order == tuple(keys)
        return calls

    assert count_hashes(200) <= 2 * count_hashes(100)


def test_connected_chain_enumeration_does_not_depend_on_python_recursion_depth():
    from mudyla.dag.solver.objective import NativeObjective

    graph = chain_input(1000)
    budget = LayoutBudget(5)
    budget.start()
    search = GridSearch(graph, NativeObjective(), budget)
    seed = (0,) * len(graph.execution_order)
    endpoints = tuple((index, index + 1) for index in range(len(seed) - 1))
    assignments = search._component_assignments(seed, endpoints)
    assert next(assignments) == seed
    following = next(assignments)
    assert len(following) == len(seed) and following != seed
    assert all(following[target] not in following[source + 1:target] for source, target in endpoints)


@pytest.mark.parametrize('mode', ['grid-low', 'grid-medium', 'grid-high'])
def test_grid_levels_evaluate_completed_assignments_with_the_required_objective(mode):
    calls = []

    class Objective:
        def score(self, candidate, budget):
            calls.append(candidate.components)
            return LayoutScore(0, 0, 0)

    result = create_solver(mode, chain_input(5), objective=Objective(), budget=LayoutBudget(OVERALL_BUDGET_SECONDS)).solve()
    effort = dict(GRID_EFFORT_LIMITS)[mode]
    assert len(calls) == effort, 'Grid did not evaluate its completed-layout effort limit'
    assert result.candidates[0].selection_score == LayoutScore(0, 0, 0)
    assert result.components is calls[0], 'An equal score replaced the greedy incumbent'


def test_auto_uses_only_one_continuing_grid_search(monkeypatch):
    from mudyla.dag.solver.dagre import DagreSolver
    from mudyla.dag.solver.elk import ElkSolver
    from mudyla.dag.solver.objective import NativeObjective

    def unexpected(self):
        pytest.fail('Auto invoked a manual backend instead of continuing Grid search')

    monkeypatch.setattr(DagreSolver, 'solve', unexpected)
    monkeypatch.setattr(ElkSolver, 'solve', unexpected)
    result = create_solver('auto', chain_input(3), objective=NativeObjective(), budget=LayoutBudget(OVERALL_BUDGET_SECONDS)).solve()
    assert result.selected == 'grid-opt'
    assert result.progress.exhausted
    assert result.progress.evaluated_layouts == 13
    assert not result.proven_optimal and result.candidates[-1].attempts[0].proven_optimal


def assignment(candidate):
    return tuple(tuple(sorted({node.center.x for node in component.nodes}).index(node.center.x)
                       for node in component.nodes) for component in candidate.components)


@pytest.mark.parametrize('count,endpoints', [
    (1, ()), (3, ((0, 1), (1, 2))), (3, ((0, 1), (0, 2), (1, 2))),
    (4, ((0, 1), (0, 2), (1, 3), (2, 3))),
])
def test_canonical_search_exhausts_every_feasible_ordered_partition(count, endpoints):
    from mudyla.dag.solver.objective import NativeObjective
    keys = [ActionKey.from_name(f'node{index}') for index in range(count)]
    sample = fixture('grid-domain', keys, [(target, Dependency(keys[source])) for source, target in endpoints],
                     [count - 1], 180)
    graph = build_solver_input(sample.graph, keys, {key: NodeSize(1, 1) for key in keys},
                               display=build_display_edges(sample.graph, tuple(keys), full=True))
    expected = {(lanes,) for lanes in itertools.product(range(count), repeat=count)
                if set(lanes) == set(range(max(lanes) + 1))
                and all(lanes[target] not in lanes[source + 1:target] for source, target in endpoints)}
    search = GridSearch(graph, NativeObjective(), LayoutBudget(5))
    result = search.advance(None)
    assert set(search.evaluated_assignments) == expected
    assert result.progress.evaluated_layouts == len(expected)
    assert result.progress.exhausted and result.attempts[0].proven_optimal


@pytest.mark.parametrize('desired', [(2, 0, 1), (1, 0, 2)])
def test_injected_objective_can_select_first_node_rightmost_or_interior(desired):
    calls = []

    class Objective:
        def score(self, candidate, budget):
            lanes, = assignment(candidate)
            calls.append(lanes)
            return LayoutScore(0, 0, 0 if lanes == desired else 1)

    search = GridSearch(chain_input(3), Objective(), LayoutBudget(5))
    result = search.advance(None)
    assert assignment(result) == (desired,)
    assert result.progress.best_try == calls.index(desired) + 1
    assert result.selection_score.normalized_area == 0


def test_search_counts_whole_graph_assignments_and_reuses_reached_checkpoints():
    keys = [ActionKey.from_name(name) for name in ('a-source', 'c-source', 'b-goal', 'd-goal')]
    sample = fixture('grid-components', keys, [(2, Dependency(keys[0])), (3, Dependency(keys[1]))], [2, 3], 180)
    graph = build_solver_input(sample.graph, keys, {key: NodeSize(1, 1) for key in keys},
                               display=build_display_edges(sample.graph, tuple(keys), full=True))
    calls = []

    class Objective:
        def score(self, candidate, budget):
            calls.append(assignment(candidate))
            return LayoutScore(0, 0, 0)

    search = GridSearch(graph, Objective(), LayoutBudget(5))
    first = search.advance(1)
    fourth = search.advance(4)
    assert first.progress.evaluated_layouts == 1 and fourth.progress.evaluated_layouts == 4
    assert first.components is fourth.components, 'A mode-order tie replaced the earlier incumbent'
    assert search.advance(1) is first, 'An earlier checkpoint lost its original progress snapshot'
    assert search.advance(4).components is first.components and len(calls) == 4
    final = search.advance(None)
    assert final.progress.evaluated_layouts == 9 and final.progress.exhausted
    assert len(calls) == len(set(calls)) == 9


@pytest.mark.parametrize('phase', ['grid_expand', 'native_score', 'row_columns'])
def test_expiry_retains_only_the_complete_projected_incumbent(monkeypatch, phase):
    from mudyla.dag.solver import grid
    graph = chain_input(3)
    projection = RowProjectionObjective(graph)
    budget = LayoutBudget(5)
    search = GridSearch(graph, projection, budget)
    seed = search.advance(1)
    if phase == 'grid_expand':
        original_check = budget.check
        def check(stage):
            if stage == phase:
                raise BudgetExpired(phase)
            original_check(stage)
        monkeypatch.setattr(budget, 'check', check)
    else:
        def expire(*args):
            raise BudgetExpired(phase)
        monkeypatch.setattr(grid if phase == 'native_score' else projection,
                            'score_native_layout' if phase == 'native_score' else 'score', expire)
    final = search.advance(None)
    assert final.progress.evaluated_layouts == 1 and final.progress.best_try == 1
    assert final.progress.stop_reason == 'deadline' and not final.progress.exhausted
    assert final.components is seed.components and not final.attempts[0].proven_optimal
    from mudyla.dag.solver.model import SolverResult
    result = SolverResult(graph, final.components, 'auto', 'grid-opt', final.attempts,
                          final.solve_ms, (final,), final.progress, False)
    geometry = build_native_row_layout(result, projection=projection, preparation_ms=(result).solve_ms).geometry
    assert build_native_row_layout(result, projection=projection, preparation_ms=(result).solve_ms).geometry is geometry
    assert search.advance(None).components is final.components


def test_incomplete_seed_and_fatal_objective_report_explicit_attempts(monkeypatch):
    from mudyla.dag.solver import grid
    from mudyla.dag.solver.objective import NativeObjective

    def expire(*args):
        raise BudgetExpired('native_score')

    monkeypatch.setattr(grid, 'score_native_layout', expire)
    with pytest.raises(OverallTimeout, match='native_score') as timeout:
        GridSearch(chain_input(3), NativeObjective(), LayoutBudget(5)).advance(None)
    assert timeout.value.attempts[0].timed_out and not timeout.value.attempts[0].complete
    monkeypatch.undo()

    class Objective:
        def score(self, candidate, budget):
            raise ValueError('controlled objective failure')

    with pytest.raises(SolverFailure, match='controlled objective failure') as failure:
        GridSearch(chain_input(3), Objective(), LayoutBudget(5)).advance(None)
    assert isinstance(failure.value.__cause__, ValueError)
    assert failure.value.attempts[0].phase == 'evaluation'


def test_factory_shares_one_two_hundred_millisecond_budget_with_the_search():
    from mudyla.dag.solver.objective import NativeObjective
    for mode in ('grid-low', 'grid-medium', 'grid-high', 'grid-opt', 'auto'):
        solver = create_solver(mode, chain_input(3), objective=NativeObjective(), budget=LayoutBudget(OVERALL_BUDGET_SECONDS))
        assert solver.budget.seconds == 0.2 and solver.search.budget is solver.budget
        result = solver.solve()
        assert solver.solve() is result
