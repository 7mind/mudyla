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
from mudyla.dag.solver.model import LayoutScore, NodeSize
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
    assert budgets[0][0].seconds == (OVERALL_BUDGET_SECONDS if mode == "auto" else None)
    assert layout.preparation_ms >= sum(intervals)
    assert layout.native_result.solve_ms >= intervals[-1]
    assert layout.native_result.graph.display is layout.display
    assert layout.native_result.candidates[0].selection_score is not None


@pytest.mark.parametrize("mode", ["grid-low", "dagre", "elk", "sugiyama"])
def test_caller_budget_rejects_first_projection_completed_after_deadline(mode, monkeypatch):
    key = ActionKey.from_name("source")
    graph = ActionGraph({key: ActionNode(key, ActionDefinition(
        key.id.name, [], {}, SourceLocation("publication", 1, key.id.name)))}, {key})
    display = build_display_edges(graph, (key,), full=True)
    model = build_solver_input(graph, [key], {key: NodeSize(1, 1)}, display=display)
    objective = RowProjectionObjective(model)
    solver = create_solver(mode, model, objective=objective, budget=LayoutBudget(OVERALL_BUDGET_SECONDS))
    original = layered.LayeredLayout
    completed = []

    def delayed_payload(*args, **kwargs):
        time.sleep(.22)
        payload = original(*args, **kwargs)
        completed.append(time.monotonic())
        return payload

    monkeypatch.setattr(layered, "LayeredLayout", delayed_payload)
    with pytest.raises(OverallTimeout) as timeout:
        solver.solve()
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


@pytest.fixture
def controlled_layout_time(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    keys = [ActionKey.from_name(f"node{index}") for index in range(3)]
    nodes = {key: ActionNode(key, ActionDefinition(
        key.id.name, [], {}, SourceLocation("deadline", 1, key.id.name))) for key in keys}
    for source, target in zip(keys, keys[1:]):
        nodes[target].dependencies.add(Dependency(source))
        nodes[source].dependents.add(Dependency(target))
    return clock, ActionGraph(nodes, {keys[-1]}), keys


@pytest.mark.parametrize("mode", ["grid-low", "grid-medium", "grid-high", "grid-opt", "dagre", "elk", "sugiyama"])
@pytest.mark.parametrize("phase", ["preparation", "native_scoring", "projection"])
def test_manual_public_solvers_have_no_automatic_deadline(controlled_layout_time, monkeypatch, mode, phase):
    clock, graph, keys = controlled_layout_time
    if phase == "preparation":
        module, attribute = dag, "build_display_edges"
    elif phase == "native_scoring":
        solver_module = "grid" if mode.startswith("grid-") else mode
        module = importlib.import_module(f"mudyla.dag.solver.{solver_module}")
        attribute = "score_native_layout"
    else:
        module, attribute = RowProjectionObjective, "score"
    original = getattr(module, attribute)

    def delayed(*args, **kwargs):
        clock[0] += 2
        return original(*args, **kwargs)

    monkeypatch.setattr(module, attribute, delayed)
    layout = dag.build_dag_layout(graph, keys, mode=mode)
    result = layout.native_result
    assert layout.preparation_ms >= 2000
    assert result.requested == mode
    assert result.attempts
    assert all(attempt.budget_seconds is None and not attempt.timed_out for attempt in result.attempts)
    assert result.candidates[0].selection_score is not None
    if result.progress is not None:
        assert result.progress.stop_reason in {"effort_limit", "exhausted"}
        if mode == "grid-opt":
            assert result.proven_optimal and result.progress.exhausted


@pytest.mark.parametrize("mode", ["grid-low", "grid-medium", "grid-high", "grid-opt", "dagre", "elk", "sugiyama"])
def test_manual_solvers_preserve_caller_owned_finite_budget(controlled_layout_time, monkeypatch, mode):
    clock, graph, keys = controlled_layout_time
    display = build_display_edges(graph, tuple(keys), full=True)
    model = build_solver_input(graph, keys, {key: NodeSize(1, 1) for key in keys}, display=display)
    solver_module = "grid" if mode.startswith("grid-") else mode
    module = importlib.import_module(f"mudyla.dag.solver.{solver_module}")
    original = module.score_native_layout

    def late_score(*args):
        clock[0] = 2
        return original(*args)

    monkeypatch.setattr(module, "score_native_layout", late_score)
    budget = LayoutBudget(OVERALL_BUDGET_SECONDS)
    solver = create_solver(mode, model, objective=NativeObjective(), budget=budget)
    assert solver.budget is budget
    with pytest.raises(OverallTimeout) as timeout:
        solver.solve()
    assert timeout.value.phase == "score_native_layout"
    assert timeout.value.attempts[0].budget_seconds == OVERALL_BUDGET_SECONDS
    assert timeout.value.attempts[0].timed_out


@pytest.mark.parametrize("mode", ["grid-low", "grid-medium", "grid-high", "grid-opt", "dagre", "elk", "sugiyama"])
def test_manual_public_solvers_propagate_cancellation(controlled_layout_time, monkeypatch, mode):
    clock, graph, keys = controlled_layout_time
    cancellation = KeyboardInterrupt()

    def cancel(self, candidate, budget):
        clock[0] = 2
        raise cancellation

    monkeypatch.setattr(RowProjectionObjective, "score", cancel)
    with pytest.raises(KeyboardInterrupt) as caught:
        dag.build_dag_layout(graph, keys, mode=mode)
    assert caught.value is cancellation


@pytest.mark.parametrize("seconds", [None, 1])
def test_budget_requires_start_before_checks(controlled_layout_time, seconds):
    budget = LayoutBudget(seconds)
    with pytest.raises(AssertionError, match="has not started"):
        budget.remaining()
    with pytest.raises(AssertionError, match="has not started"):
        budget.elapsed()
    with pytest.raises(AssertionError, match="has not started"):
        budget.deadline
    with pytest.raises(AssertionError, match="has not started"):
        budget.check("unstarted")
    with pytest.raises(AssertionError, match="has not started"):
        budget.limit_to(.2)
    with pytest.raises(AssertionError, match="has not started"):
        list(budget.iterate([1], "unstarted"))


@pytest.mark.parametrize("seconds", [None, 1])
def test_budget_start_is_idempotent(controlled_layout_time, seconds):
    clock, _, _ = controlled_layout_time
    budget = LayoutBudget(seconds)
    budget.start()
    clock[0] = .1
    budget.start()
    budget.limit_to(.2)
    assert budget.remaining() == pytest.approx(.1)
    assert budget.elapsed() == pytest.approx(.1)


def test_unlimited_budget_checks_and_iteration_do_not_expire(controlled_layout_time):
    clock, _, _ = controlled_layout_time
    budget = LayoutBudget(None)
    budget.start()
    values = budget.iterate(range(3), "unlimited")
    for expected in range(3):
        clock[0] += 1_000_000
        assert budget.remaining() is None
        budget.check("unlimited")
        assert next(values) == expected
    assert list(values) == []


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf"), float("-inf")])
def test_budget_rejects_invalid_durations(controlled_layout_time, seconds):
    with pytest.raises(AssertionError):
        LayoutBudget(seconds)
    budget = LayoutBudget(None)
    budget.start()
    with pytest.raises(AssertionError):
        budget.limit_to(seconds)


def test_unlimited_budget_can_be_shortened_from_original_start(controlled_layout_time):
    clock, _, _ = controlled_layout_time
    budget = LayoutBudget(None)
    budget.start()
    clock[0] = .5
    budget.limit_to(.2)
    assert budget.seconds == .2
    assert budget.remaining() == 0
    with pytest.raises(BudgetExpired, match="shortened"):
        budget.check("shortened")
    budget.limit_to(1)
    assert budget.remaining() == 0


@pytest.mark.parametrize("first_seconds", [.2, .6, .999, 1.0, 2.0])
def test_auto_without_hard_budget_waits_for_first_complete_layout(controlled_layout_time, monkeypatch, first_seconds):
    clock, graph, keys = controlled_layout_time
    original = RowProjectionObjective.score
    completed = []

    def first_solution(self, candidate, budget):
        clock[0] = first_seconds
        score = original(self, candidate, budget)
        completed.append(candidate.components)
        return score

    monkeypatch.setattr(RowProjectionObjective, "score", first_solution)
    layout = dag.build_dag_layout(graph, keys, mode="auto")
    assert completed == [layout.native_result.components]
    assert layout.native_result.progress.evaluated_layouts == 1
    assert layout.native_result.progress.stop_reason == "deadline"
    assert layout.preparation_ms == pytest.approx(first_seconds * 1000)


@pytest.mark.parametrize("first_seconds", [1.0, 1.01])
@pytest.mark.parametrize("phase", ["row_columns", "grid_commit"])
def test_caller_budget_rejects_first_auto_layout_at_one_second(controlled_layout_time, monkeypatch, first_seconds, phase):
    clock, graph, keys = controlled_layout_time
    original = RowProjectionObjective.score

    def late_solution(self, candidate, budget):
        if phase == "row_columns":
            clock[0] = first_seconds
        score = original(self, candidate, budget)
        clock[0] = first_seconds
        return score

    monkeypatch.setattr(RowProjectionObjective, "score", late_solution)
    with pytest.raises(OverallTimeout) as timeout:
        dag.build_dag_layout(graph, keys, mode="auto", budget=LayoutBudget(1))
    assert timeout.value.attempts[0].budget_seconds == 1.0
    assert timeout.value.attempts[0].timed_out
    assert timeout.value.phase == phase


@pytest.mark.parametrize("seconds", [None, 1])
def test_auto_stops_improving_at_two_hundred_milliseconds(controlled_layout_time, monkeypatch, seconds):
    clock, graph, keys = controlled_layout_time
    original = RowProjectionObjective.score
    times = iter([.05, .15, .2])
    completed = []

    def improving(self, candidate, budget):
        clock[0] = next(times)
        original(self, candidate, budget)
        completed.append(candidate.components)
        return LayoutScore(0, 0, 3 - len(completed))

    monkeypatch.setattr(RowProjectionObjective, "score", improving)
    layout = dag.build_dag_layout(graph, keys, mode="auto", budget=LayoutBudget(seconds))
    assert len(completed) == 2
    assert layout.native_result.components == completed[1]
    assert layout.native_result.progress.evaluated_layouts == 2
    assert layout.native_result.progress.best_try == 2
    assert layout.native_result.progress.stop_reason == "deadline"
    assert layout.preparation_ms == pytest.approx(200)


def test_budget_limit_keeps_original_start_and_cannot_extend_deadline(controlled_layout_time):
    clock, _, _ = controlled_layout_time
    budget = LayoutBudget(1)
    budget.start()
    clock[0] = .1
    budget.limit_to(.2)
    assert budget.remaining() == pytest.approx(.1)
    budget.limit_to(1)
    assert budget.remaining() == pytest.approx(.1)
    clock[0] = .2
    with pytest.raises(BudgetExpired):
        budget.check("deadline")


def test_public_hard_budget_rejects_grid_incumbent_at_deadline(controlled_layout_time, monkeypatch):
    clock, graph, keys = controlled_layout_time
    original = RowProjectionObjective.score
    completed = []

    def late_improvement(self, candidate, budget):
        clock[0] = .1 if not completed else 1
        score = original(self, candidate, budget)
        completed.append(candidate)
        return score

    monkeypatch.setattr(RowProjectionObjective, "score", late_improvement)
    with pytest.raises(OverallTimeout) as timeout:
        dag.build_dag_layout(graph, keys, mode="grid-opt", budget=LayoutBudget(1))
    assert len(completed) == 1
    assert timeout.value.phase == "layout_commit"
    assert timeout.value.attempts[0].timed_out
    assert timeout.value.attempts[0].budget_seconds == 1


def test_public_builder_retains_caller_budget_start(controlled_layout_time, monkeypatch):
    clock, graph, keys = controlled_layout_time
    budget = LayoutBudget(1)
    budget.start()
    clock[0] = .6
    original = RowProjectionObjective.score

    def delayed_score(self, candidate, supplied_budget):
        assert supplied_budget is budget
        clock[0] += .4
        return original(self, candidate, supplied_budget)

    monkeypatch.setattr(RowProjectionObjective, "score", delayed_score)
    with pytest.raises(OverallTimeout) as timeout:
        dag.build_dag_layout(graph, keys, mode="auto", budget=budget)
    assert timeout.value.phase == "row_columns"
    assert budget.elapsed() == 1


@pytest.mark.parametrize("mode", ["auto", "grid-low", "dagre"])
@pytest.mark.parametrize("elapsed", [.999, 1.0])
def test_public_hard_budget_checked_after_layout_construction(controlled_layout_time, monkeypatch, mode, elapsed):
    clock, graph, keys = controlled_layout_time
    from mudyla.logging.formatters import native_dag
    original = native_dag.build_native_row_layout
    completed = []

    def delayed_layout(*args, **kwargs):
        value = original(*args, **kwargs)
        clock[0] = elapsed
        completed.append(value)
        return value

    monkeypatch.setattr(native_dag, "build_native_row_layout", delayed_layout)
    if elapsed < 1:
        layout = dag.build_dag_layout(graph, keys, mode=mode, budget=LayoutBudget(1))
        assert completed == [layout]
    else:
        with pytest.raises(OverallTimeout) as timeout:
            dag.build_dag_layout(graph, keys, mode=mode, budget=LayoutBudget(1))
        assert timeout.value.phase == "layout_commit"
        assert timeout.value.attempts == completed[0].native_result.attempts


@pytest.mark.parametrize("mode", ["auto", "grid-low", "dagre"])
def test_public_hard_budget_expires_at_absolute_deadline(controlled_layout_time, monkeypatch, mode):
    clock, graph, keys = controlled_layout_time
    from mudyla.logging.formatters import native_dag
    original = native_dag.build_native_row_layout
    clock[0] = 1.0
    budget = LayoutBudget(.2)
    budget.start()
    completed = []

    def finish_at_deadline(*args, **kwargs):
        layout = original(*args, **kwargs)
        completed.append(layout)
        clock[0] = 1.2
        return layout

    monkeypatch.setattr(native_dag, "build_native_row_layout", finish_at_deadline)
    with pytest.raises(OverallTimeout) as timeout:
        dag.build_dag_layout(graph, keys, mode=mode, budget=budget)
    assert timeout.value.phase == "layout_commit"
    assert timeout.value.attempts == completed[0].native_result.attempts
    assert budget.remaining() == 0


@pytest.mark.parametrize("projection_seconds,succeeds", [(.3, True), (.4, False)])
def test_caller_first_solution_budget_includes_preparation(controlled_layout_time, monkeypatch, projection_seconds, succeeds):
    clock, graph, keys = controlled_layout_time
    original_input = dag.build_solver_input
    original_score = RowProjectionObjective.score

    def prepare(*args, **kwargs):
        result = original_input(*args, **kwargs)
        clock[0] = .6
        return result

    def project(self, candidate, budget):
        clock[0] += projection_seconds
        return original_score(self, candidate, budget)

    monkeypatch.setattr(dag, "build_solver_input", prepare)
    monkeypatch.setattr(RowProjectionObjective, "score", project)
    if succeeds:
        layout = dag.build_dag_layout(graph, keys, mode="auto", budget=LayoutBudget(1))
        assert layout.preparation_ms == pytest.approx(900)
        assert layout.native_result.progress.evaluated_layouts == 1
    else:
        with pytest.raises(OverallTimeout):
            dag.build_dag_layout(graph, keys, mode="auto", budget=LayoutBudget(1))
