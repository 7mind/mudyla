"""Automatic plan presentation follows the final execution plan size."""

from unittest.mock import patch
import time

import pytest
from rich.console import Group

from mudyla.cli import CLI
from mudyla.dag.solver.base import OverallTimeout
from mudyla.dag.solver.budget import BudgetExpired
from mudyla.logging.formatters.native_dag import RowProjectionObjective


@pytest.fixture
def plan_project(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (definitions / "actions.md").write_text("\n\n".join(
        f'# action: step-{index}\n```python\npass\n```' for index in range(219)))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize("selection", [[], ["--plan", "auto"], ["--plan-dag-solver", "dagre"]])
@pytest.mark.parametrize("count,expected", [(29, "dag"), (30, "table"), (219, "table")])
@pytest.mark.parametrize("mode", ["pure", "table", "simple", "verbose", "github", "teamcity"])
def test_auto_plan_uses_action_count(plan_project, monkeypatch, capsys, selection, count, expected, mode):
    cli = CLI()
    args = cli.parser.parse_args(["--logger", mode, "--force-interactive", "--no-color", *selection])
    cli._apply_platform_defaults(args, True)
    logger = cli._create_logger(args)
    monkeypatch.setattr(cli, "_create_logger", lambda args: logger)
    if expected == "table":
        monkeypatch.setattr("mudyla.logging.terminal_logger.build_dag_layout",
                            lambda *args, **kwargs: pytest.fail("Table plan invoked the DAG solver"))
    assert cli.run(["--without-nix", "--dry-run", "--logger", mode, "--force-interactive", "--no-color",
                    *selection, *(f":step-{index}" for index in range(count))]) == 0
    assert logger.plan_style == expected
    assert logger.execution_order is not None and len(logger.execution_order) == count
    assert (logger.layout is not None) == (expected == "dag")
    assert "Plan:" in capsys.readouterr().out


@pytest.mark.parametrize("selection", ["tree", "dag", "table"])
@pytest.mark.parametrize("count", [29, 30, 219])
def test_explicit_plan_overrides_action_count(plan_project, monkeypatch, selection, count):
    cli = CLI()
    args = cli.parser.parse_args(["--plan", selection, "--no-color"])
    cli._apply_platform_defaults(args, True)
    logger = cli._create_logger(args)
    monkeypatch.setattr(cli, "_create_logger", lambda args: logger)
    with patch("mudyla.logging.terminal_logger.build_dag_layout") as layout, \
            patch.object(logger, "_plan", return_value=Group()):
        assert cli.run(["--without-nix", "--dry-run", "--plan", selection, "--no-color",
                        *(f":step-{index}" for index in range(count))]) == 0
    assert logger.plan_style == selection
    assert layout.call_count == (1 if selection == "dag" else 0)


@pytest.mark.parametrize("selection,expected_exit", [([], 0), (["--plan", "auto"], 0), (["--plan", "dag"], 1)])
@pytest.mark.parametrize("error,fallback", [
    (OverallTimeout("row_spacing", ()), True),
    (OverallTimeout("display_edges", ()), True),
    (ValueError("Invalid layout"), False),
])
@pytest.mark.parametrize("mode", ["pure", "table", "simple", "verbose", "github", "teamcity"])
def test_only_auto_plan_falls_back_when_layout_budget_expires(plan_project, capsys, selection, expected_exit, error, fallback, mode):
    if not fallback:
        expected_exit = 1
    with patch("mudyla.logging.terminal_logger.build_dag_layout", side_effect=error) as layout:
        assert CLI().run(["--without-nix", "--no-color", "--dry-run", "--logger", mode,
                          "--force-interactive", *selection, ":step-0"]) == expected_exit
    assert layout.call_count == 1
    displayed = capsys.readouterr().out
    assert ("Deps: prerequisite row" in displayed) == (expected_exit == 0)


def test_expired_dag_budget_does_not_prevent_execution(plan_project, capsys):
    saved = plan_project / "result.json"
    with patch("mudyla.dag.solver.budget.LayoutBudget.check", side_effect=BudgetExpired("display_edges")):
        assert CLI().run(["--without-nix", "--no-color", "--out", str(saved), ":step-0"]) == 0
    assert saved.is_file()
    assert "Deps: prerequisite row" in capsys.readouterr().out


@pytest.fixture
def timed_plan_project(plan_project, monkeypatch):
    definitions = plan_project / ".mdl" / "defs" / "actions.md"
    definitions.write_text("\n\n".join(
        f'# action: step-{index}\n```python\n' +
        (f'mdl.dep("action.step-{index - 1}")\n' if index else "") +
        'pass\n```' for index in range(3)))
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    return clock


@pytest.mark.parametrize("plan,solver,projection_times,expected", [
    ("dag", "grid-auto", [2.0], "dag"),
    ("auto", "grid-opt", [.1, 1.0], "table"),
    ("auto", "grid-auto", [.1, .2], "dag"),
    ("auto", "grid-opt", [.05], "dag"),
] + [("auto", solver, [2.0], "table") for solver in
     ["grid-auto", "grid-low", "grid-medium", "grid-high", "grid-opt", "dagre", "elk", "sugiyama"]])
def test_plan_owns_hard_deadline(timed_plan_project, monkeypatch, capsys, plan, solver, projection_times, expected):
    clock = timed_plan_project
    cli = CLI()
    selection = ["--plan", plan, "--plan-dag-solver", solver, "--no-color"]
    args = cli.parser.parse_args(selection)
    cli._apply_platform_defaults(args, True)
    logger = cli._create_logger(args)
    monkeypatch.setattr(cli, "_create_logger", lambda args: logger)
    original = RowProjectionObjective.score
    calls = []
    completed = []

    def delayed_score(self, candidate, budget):
        clock[0] = projection_times[min(len(calls), len(projection_times) - 1)]
        calls.append(candidate)
        score = original(self, candidate, budget)
        completed.append(candidate)
        return score

    monkeypatch.setattr(RowProjectionObjective, "score", delayed_score)
    assert cli.run(["--without-nix", "--dry-run", *selection, ":step-2"]) == 0
    assert logger.plan_style == expected
    assert (logger.layout is not None) == (expected == "dag")
    assert ("Deps: prerequisite row" in capsys.readouterr().out) == (expected == "table")
    if solver == "grid-opt" and len(projection_times) == 2:
        assert len(completed) == 1
    if solver == "grid-opt" and expected == "dag":
        assert logger.layout.native_result.proven_optimal


@pytest.mark.parametrize("phase", ["build_display_edges", "build_solver_input", "build_native_row_layout"])
def test_auto_plan_budget_includes_complete_preparation(timed_plan_project, monkeypatch, capsys, phase):
    clock = timed_plan_project
    from mudyla.logging.formatters import dag, native_dag
    module = native_dag if phase == "build_native_row_layout" else dag
    original = getattr(module, phase)

    def delayed(*args, **kwargs):
        value = original(*args, **kwargs)
        clock[0] = 1
        return value

    monkeypatch.setattr(module, phase, delayed)
    assert CLI().run(["--without-nix", "--dry-run", "--no-color", ":step-2"]) == 0
    assert "Deps: prerequisite row" in capsys.readouterr().out


@pytest.mark.parametrize("error", [KeyboardInterrupt(), ValueError("Invalid projection")])
def test_auto_plan_propagates_cancellation_and_projection_errors(timed_plan_project, monkeypatch, capsys, error):
    clock = timed_plan_project

    def fail(self, candidate, budget):
        clock[0] = 2
        raise error

    monkeypatch.setattr(RowProjectionObjective, "score", fail)
    if isinstance(error, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt) as caught:
            CLI().run(["--without-nix", "--dry-run", "--no-color", ":step-2"])
        assert caught.value is error
    else:
        assert CLI().run(["--without-nix", "--dry-run", "--no-color", ":step-2"]) == 1
    assert "Deps: prerequisite row" not in capsys.readouterr().out
