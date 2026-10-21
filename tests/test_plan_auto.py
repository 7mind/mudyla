"""Automatic plan presentation follows the final execution plan size."""

from unittest.mock import patch

import pytest
from rich.console import Group

from mudyla.cli import CLI
from mudyla.dag.solver.base import OverallTimeout
from mudyla.dag.solver.budget import BudgetExpired


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
