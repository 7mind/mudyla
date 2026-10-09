"""Completed native scoring belongs to the reported solve duration."""

import importlib
import time

import pytest

from mudyla.ast.models import ActionDefinition, SourceLocation
from mudyla.dag.display import build_display_edges
from mudyla.dag.graph import ActionGraph, ActionNode, ActionKey, Dependency
from mudyla.dag.solver.base import OverallTimeout
from mudyla.dag.solver.budget import BudgetExpired
from mudyla.dag.solver.factory import create_solver
from mudyla.dag.solver.budget import LayoutBudget, OVERALL_BUDGET_SECONDS
from mudyla.dag.solver.graph import build_solver_input
from mudyla.dag.solver.model import NodeSize
from mudyla.dag.solver.objective import NativeObjective
from mudyla.logging.formatters import dag, layered
from mudyla.logging.formatters.native_dag import RowProjectionObjective


@pytest.mark.parametrize("mode", ["dagre", "elk", "sugiyama"])
def test_reported_solve_time_includes_native_scoring(mode, monkeypatch):
    key = ActionKey.from_name("source")
    graph = ActionGraph({key: ActionNode(key, ActionDefinition(
        key.id.name, [], {}, SourceLocation("timing", 1, key.id.name)))}, {key})
    display = build_display_edges(graph, (key,), full=True)
    model = build_solver_input(graph, [key], {key: NodeSize(1, 1)}, display=display)
    module = importlib.import_module(f"mudyla.dag.solver.{mode}")
    original = module.score_native_layout
    elapsed = []

    def measured_scoring(*args):
        start = time.monotonic()
        score = original(*args)
        time.sleep(0.025)
        elapsed.append((time.monotonic() - start) * 1000)
        return score

    monkeypatch.setattr(module, "score_native_layout", measured_scoring)
    solver = create_solver(mode, model, objective=NativeObjective(), budget=LayoutBudget(OVERALL_BUDGET_SECONDS))
    result = solver.solve()
    assert solver.solve() is result
    assert result.solve_ms >= elapsed[0], (mode, result.solve_ms, elapsed[0])


@pytest.mark.parametrize("mode", ["auto", "grid-low", "grid-medium", "grid-high", "grid-opt", "dagre", "elk", "sugiyama"])
def test_public_preparation_uses_one_budget_and_reports_complete_interval(mode, monkeypatch):
    key = ActionKey.from_name("source")
    graph = ActionGraph({key: ActionNode(key, ActionDefinition(
        key.id.name, [], {}, SourceLocation("timing", 1, key.id.name)))}, {key})
    budgets = []
    intervals = []

    def measured(function):
        def call(*args, **kwargs):
            budget = kwargs["budget"]
            budgets.append((budget, budget._deadline))
            started = time.monotonic()
            value = function(*args, **kwargs)
            time.sleep(.005)
            intervals.append((time.monotonic() - started) * 1000)
            return value
        return call

    monkeypatch.setattr(dag, "build_display_edges", measured(dag.build_display_edges))
    monkeypatch.setattr(dag, "build_solver_input", measured(dag.build_solver_input))
    scoring = RowProjectionObjective.score

    def projected(self, candidate, budget):
        budgets.append((budget, budget._deadline))
        started = time.monotonic()
        score = scoring(self, candidate, budget)
        time.sleep(.005)
        intervals.append((time.monotonic() - started) * 1000)
        return score

    monkeypatch.setattr(RowProjectionObjective, "score", projected)
    layout = dag.build_dag_layout(graph, [key], mode=mode)
    assert len(budgets) == 3
    assert all(budget is budgets[0][0] and deadline == budgets[0][1] for budget, deadline in budgets)
    assert budgets[0][0].seconds == OVERALL_BUDGET_SECONDS
    assert layout.preparation_ms >= sum(intervals)
    assert layout.native_result.solve_ms >= intervals[-1]
    assert layout.native_result.graph.display is layout.display
    assert layout.native_result.candidates[0].selection_score is not None


@pytest.mark.parametrize("mode", ["grid-low", "dagre", "elk", "sugiyama"])
def test_first_projection_completed_after_deadline_is_not_published(mode, monkeypatch):
    key = ActionKey.from_name("source")
    graph = ActionGraph({key: ActionNode(key, ActionDefinition(
        key.id.name, [], {}, SourceLocation("publication", 1, key.id.name)))}, {key})
    original = layered.LayeredLayout
    completed = []

    def delayed_payload(*args, **kwargs):
        time.sleep(.22)
        payload = original(*args, **kwargs)
        completed.append(time.monotonic())
        return payload

    monkeypatch.setattr(layered, "LayeredLayout", delayed_payload)
    with pytest.raises(OverallTimeout) as timeout:
        dag.build_dag_layout(graph, [key], mode=mode)
    assert len(completed) == 1
    assert timeout.value.phase == ("grid_commit" if mode == "grid-low" else "solver_commit")
    assert timeout.value.attempts
    if mode == "grid-low":
        assert timeout.value.attempts[0].timed_out
    else:
        assert isinstance(timeout.value.__cause__, BudgetExpired)
        assert all(attempt.mode == mode for attempt in timeout.value.attempts)


def test_late_second_projection_preserves_grid_committed_seed(monkeypatch):
    keys = [ActionKey.from_name(f"node{index}") for index in range(3)]
    nodes = {key: ActionNode(key, ActionDefinition(
        key.id.name, [], {}, SourceLocation("publication", 1, key.id.name))) for key in keys}
    for source, target in zip(keys, keys[1:]):
        nodes[target].dependencies.add(Dependency(source))
        nodes[source].dependents.add(Dependency(target))
    graph = ActionGraph(nodes, {keys[-1]})
    display = build_display_edges(graph, tuple(keys), full=True)
    model = build_solver_input(graph, keys, {key: NodeSize(1, 1) for key in keys}, display=display)
    objective = RowProjectionObjective(model)
    budget = LayoutBudget(OVERALL_BUDGET_SECONDS)
    solver = create_solver("grid-opt", model, objective=objective, budget=budget)
    original = layered.LayeredLayout
    completed = []

    def delayed_second_payload(*args, **kwargs):
        if completed:
            time.sleep(.22)
        payload = original(*args, **kwargs)
        completed.append(payload)
        return payload

    monkeypatch.setattr(layered, "LayeredLayout", delayed_second_payload)
    result = solver.solve()
    assert len(completed) == 2
    assert result.progress.evaluated_layouts == result.progress.best_try == 1
    assert result.progress.stop_reason == "deadline"
    assert not result.proven_optimal and result.attempts[0].timed_out
    assert objective.geometry(result) is completed[0]
    assert budget.remaining() == 0

    def unexpected_check(phase):
        pytest.fail(f"Cached incumbent checked expired budget during {phase}")

    monkeypatch.setattr(budget, "check", unexpected_check)
    assert solver.solve() is result
    assert objective.geometry(result) is completed[0]
