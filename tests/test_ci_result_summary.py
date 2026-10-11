"""Final CI summaries, literal reasons and unchanged terminal presentation."""

from io import StringIO
from pathlib import Path
import sys
from unittest.mock import patch

import pytest
from rich.console import Console

from mudyla.dag.context import ContextId
from mudyla.dag.graph import ActionKey
from mudyla.executor.engine import ActionResult, ExecutionResult
from mudyla.logging.terminal_logger_github import GitHubTerminalLogger
from mudyla.logging.terminal_logger_teamcity import TeamCityTerminalLogger
from mudyla.logging.terminal_logger_simple import SimpleTerminalLogger
from mudyla.logging.terminal_logger_verbose import VerboseTerminalLogger
from mudyla.logging.terminal_logger_pure import PureTerminalLogger
from mudyla.logging.terminal_logger_table import TableTerminalLogger
from tests.logger_fixtures import prepared_logger
from tests.test_stream_loggers import ci_action_output, run_project


def action_result(key, directory, *, success, restored, error_message, exit_code):
    return ActionResult(key.id.name, success, {}, directory / "stdout.log", directory / "stderr.log",
                        directory / "script.py", "start", "end", .1, exit_code=exit_code,
                        error_message=error_message, restored=restored)


def ci_logger(monkeypatch, mode, keys):
    capture = StringIO()
    monkeypatch.setattr(sys, "stdout", capture)
    monkeypatch.setenv("COLUMNS", "300")
    logger_type = GitHubTerminalLogger if mode == "github" else TeamCityTerminalLogger
    logger = prepared_logger(logger_type, keys, no_color=True,
                             console=Console(file=capture, width=300, no_color=True) if mode == "github" else None)
    return logger, capture


def runner_commands(text):
    """Apply the runner's first command header, legacy search and stop/resume rules."""
    commands = []
    stopped = None
    for line in text.splitlines():
        modern = line.lstrip()
        if modern.startswith("::") and (end := modern.find("::", 2)) >= 0:
            name, data = modern[2:end].split(" ", 1)[0].lower(), modern[end + 2:]
            mappings = [("%0D", "\r"), ("%0A", "\n"), ("%25", "%")]
        elif (start := line.find("##[")) >= 0 and (end := line.find("]", start)) >= 0:
            name, data = line[start + 3:end].split(" ", 1)[0].lower(), line[end + 1:]
            mappings = [("%3B", ";"), ("%0D", "\r"), ("%0A", "\n"), ("%5D", "]"), ("%25", "%")]
        else:
            continue
        for escaped, literal in mappings:
            data = data.replace(escaped, literal)
        if stopped is not None:
            if name == stopped:
                stopped = None
            continue
        if name == "stop-commands":
            stopped = data.lower()
        elif name in {"group", "endgroup"}:
            commands.append((name, data))
    assert stopped is None, text
    return commands


def test_runner_command_control_recognizes_legacy_search_and_stop_resume():
    assert runner_commands("prefix ##[group]literal\n##[endgroup]\n") == [("group", "literal"), ("endgroup", "")]
    assert runner_commands("::stop-commands::TOKEN\nprefix ##[group]literal\n::TOKEN::\n::group::later\n::endgroup::\n") == [
        ("group", "later"), ("endgroup", "")]


@pytest.mark.parametrize("mode", ["github", "teamcity"])
def test_ci_result_counts_lists_and_reasons_follow_final_plan(monkeypatch, tmp_path, mode):
    keys = [ActionKey.from_name("seed")]
    keys.extend(ActionKey.from_name(name, ContextId(axis_values=(("platform", platform),)))
                for name in ["test", "build"] for platform in ["linux-x86", "macos-arm64"])
    logger, capture = ci_logger(monkeypatch, mode, keys)
    values = [action_result(key, tmp_path, success=index not in {1, 2}, restored=index in {3, 4},
                            error_message=" \n\t\nfirst problem\nhidden detail" if index == 1 else " \n\t\n",
                            exit_code=3 if index == 2 else 0) for index, key in enumerate(keys)]
    result = ExecutionResult(False, dict(reversed(list(zip(keys, values)))), tmp_path, 1.2)
    logger.report_outcome(result, True)
    messages, _, _ = ci_action_output(capture.getvalue(), mode)
    assert "5 actions — 1 completed, 2 failed, 2 restored" in messages
    assert "test for linux-x86 — first problem" in messages
    assert "test for macos-arm64 — exit code 3" in messages
    assert "hidden detail" not in messages
    assert messages.index("test for linux-x86") < messages.index("test for macos-arm64")
    assert messages.index("build for linux-x86") < messages.index("build for macos-arm64")
    assert messages.index("Outcome:") < messages.index("Total wall time:") < messages.index("Actions:")
    assert messages.index("Actions:") < messages.index("Failed:") < messages.index("Restored:") < messages.index("Logs:")


@pytest.mark.parametrize("mode", ["github", "teamcity"])
@pytest.mark.parametrize("planned,returned,success,restored,expected", [
    (1, 1, True, False, "1 action — 1 completed, 0 failed, 0 restored"),
    (1, 1, True, True, "1 action — 0 completed, 0 failed, 1 restored"),
    (2, 1, False, True, "1 of 2 actions has a result — 0 completed, 1 failed, 0 restored"),
    (2, 0, False, False, "0 of 2 actions have results — 0 completed, 0 failed, 0 restored"),
    (0, 0, True, False, "0 actions — 0 completed, 0 failed, 0 restored"),
])
def test_ci_result_partial_empty_and_restored_failure(monkeypatch, tmp_path, mode, planned, returned,
                                                      success, restored, expected):
    keys = [ActionKey.from_name(f"work{index}") for index in range(planned)]
    logger, capture = ci_logger(monkeypatch, mode, keys)
    results = {key: action_result(key, tmp_path, success=success, restored=restored,
                                 error_message=None, exit_code=1 if not success else 0) for key in keys[:returned]}
    logger.report_outcome(ExecutionResult(success, results, tmp_path), False)
    messages, _, _ = ci_action_output(capture.getvalue(), mode)
    assert expected in messages
    assert ("Failed:" in messages) == (returned > 0 and not success)
    assert ("Restored:" in messages) == (returned > 0 and success and restored)
    assert "Total wall time:" not in messages


@pytest.mark.parametrize("mode", ["github", "teamcity"])
def test_ci_result_rejects_results_outside_plan(monkeypatch, tmp_path, mode):
    key = ActionKey.from_name("unplanned")
    logger, _ = ci_logger(monkeypatch, mode, [])
    value = action_result(key, tmp_path, success=True, restored=False, error_message=None, exit_code=0)
    with pytest.raises(AssertionError, match="final plan"):
        logger.report_outcome(ExecutionResult(True, {key: value}, tmp_path), True)


@pytest.mark.parametrize("reason", ["[red]literal[/red]\x1b[2J", "##[group]INJECTED", "##[endgroup]", "##[stop-commands]INJECTED"])
def test_github_result_reasons_are_literal_and_later_commands_resume(monkeypatch, tmp_path, reason):
    key = ActionKey.from_name("test")
    logger, capture = ci_logger(monkeypatch, "github", [key])
    value = action_result(key, tmp_path, success=False, restored=False, error_message=reason, exit_code=1)
    logger.report_outcome(ExecutionResult(False, {key: value}, tmp_path), True)
    logger.output.print_raw("::group::AFTER\n::endgroup::")
    text = capture.getvalue()
    assert "test — " + reason.replace("\x1b", "\\u001b") in text
    assert "\x1b" not in text
    assert runner_commands(text) == [("group", "AFTER"), ("endgroup", "")]


@pytest.mark.parametrize("directory", ["##[group]INJECTED", "##[endgroup]", "##[stop-commands]INJECTED"])
def test_github_existing_result_logs_remain_literal_to_native_parser(monkeypatch, directory):
    logger, capture = ci_logger(monkeypatch, "github", [])
    logger.report_outcome(ExecutionResult(False, {}, Path(directory)), True)
    logger.output.print_raw("::group::AFTER\n::endgroup::")
    assert runner_commands(capture.getvalue()) == [("group", "AFTER"), ("endgroup", "")]


def test_github_result_restores_commands_after_render_failure_with_fresh_tokens(monkeypatch, tmp_path):
    logger, capture = ci_logger(monkeypatch, "github", [])
    result = ExecutionResult(True, {}, tmp_path)
    with patch.object(logger, "_action_result_fields", side_effect=RuntimeError("render failed")):
        with pytest.raises(RuntimeError, match="render failed"):
            logger.report_outcome(result, True)
    logger.report_outcome(result, True)
    logger.output.print_raw("::group::AFTER\n::endgroup::")
    text = capture.getvalue()
    tokens = [line.removeprefix("::stop-commands::") for line in text.splitlines() if line.startswith("::stop-commands::")]
    assert len(tokens) == len(set(tokens)) == 2
    assert all("::" + token + "::\n" in text for token in tokens)
    assert runner_commands(text) == [("group", "AFTER"), ("endgroup", "")]


@pytest.mark.parametrize("reason", ["[red]literal[/red]\x1b[2J", "##teamcity[blockOpened name='INJECTED']", "pipes | and brackets []"])
def test_teamcity_result_reasons_are_literal_service_message_text(monkeypatch, tmp_path, reason):
    key = ActionKey.from_name("test")
    logger, capture = ci_logger(monkeypatch, "teamcity", [key])
    value = action_result(key, tmp_path, success=False, restored=False, error_message=reason, exit_code=1)
    logger.report_outcome(ExecutionResult(False, {key: value}, tmp_path), True)
    messages, opened, closed = ci_action_output(capture.getvalue(), "teamcity")
    assert "test — " + reason.replace("\x1b", "\\u001b") in messages
    assert opened == closed == []
    assert "\x1b" not in messages


@pytest.mark.parametrize("logger_type", [SimpleTerminalLogger, VerboseTerminalLogger, PureTerminalLogger, TableTerminalLogger])
def test_non_ci_result_keeps_existing_restored_labels_and_order(monkeypatch, tmp_path, logger_type):
    from mudyla.logging.formatters.details import action_label

    capture = StringIO()
    monkeypatch.setattr(sys, "stdout", capture)
    keys = [ActionKey.from_name("work", ContextId(axis_values=(("platform", value),))) for value in ["linux-x86", "macos-arm64"]]
    logger = prepared_logger(logger_type, keys, no_color=True, console=Console(file=capture, width=300, no_color=True))
    values = {key: action_result(key, tmp_path, success=index == 1, restored=True, error_message=None, exit_code=index)
              for index, key in reversed(list(enumerate(keys)))}
    with patch.object(logger, "finish_actions"):
        logger.report_outcome(ExecutionResult(False, values, tmp_path, 1.2), True)
    text = capture.getvalue()
    assert "Actions:" not in text and "Failed:" not in text
    if logger.output.compact:
        expected = ", ".join(action_label(key, logger.output.context, logger.use_short_ids, True).plain for key in values)
        assert "Restored:" in text and expected in text
        assert "Outcome:" in text and "Total wall time:" in text and "Logs:" in text
        assert "work for linux-x86" not in text
    else:
        assert "Execution failed!" in text and "Result:" not in text and "Restored:" not in text


def test_github_result_closes_active_unfinished_child_before_literal_render(monkeypatch, tmp_path):
    key = ActionKey.from_name("test")
    logger, capture = ci_logger(monkeypatch, "github", [key])
    logger.begin_action(key, ["command"])
    logger.write_output(key, "::group::child\nBODY\n", "stdout")
    value = action_result(key, tmp_path, success=False, restored=False, error_message="##[group]INJECTED", exit_code=1)
    logger.report_outcome(ExecutionResult(False, {key: value}, tmp_path), True)
    text = capture.getvalue()
    assert runner_commands(text) == [("group", "test"), ("endgroup", ""), ("group", "test / child"), ("endgroup", "")]
    assert text.rindex("::endgroup::") < text.index("::stop-commands::") < text.index("Result:")
    assert logger._open_groups == {} and logger._physical_group is None


@pytest.mark.parametrize("mode", ["github", "teamcity"])
def test_ci_result_real_execution_and_continuation_preserve_outputs_and_metadata(tmp_path, mode):
    first = run_project(tmp_path, ["--logger", mode, "--keep-run-dir"], 'mdl.ret("ok", True, "bool")')
    assert first.returncode == 0, first.stdout + first.stderr
    messages, _, _ = ci_action_output(first.stdout, mode)
    assert "2 actions — 2 completed, 0 failed, 0 restored" in messages
    assert "Failed:" not in messages and "Restored:" not in messages
    assert messages.index("Result:") < messages.index("Outputs:")
    before = {path.relative_to(tmp_path): path.read_bytes() for path in (tmp_path / ".mdl" / "runs").rglob("meta.json")}
    second = run_project(tmp_path, ["--logger", mode, "--keep-run-dir", "--continue"], 'mdl.ret("ok", True, "bool")')
    assert second.returncode == 0, second.stdout + second.stderr
    messages, opened, closed = ci_action_output(second.stdout, mode)
    assert "2 actions — 0 completed, 0 failed, 2 restored" in messages
    assert "seed\n" in messages and "work\n" in messages
    assert opened == closed == []
    assert all((tmp_path / path).read_bytes() == content for path, content in before.items())
