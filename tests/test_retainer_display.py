"""Planning owns retainer rows and hands the same display to real actions."""

from io import BytesIO, StringIO, TextIOWrapper
from pathlib import Path
import threading
import time

import pytest
from rich.console import Console
from rich.text import Text
from tests.logger_fixtures import prepared_logger

from mudyla.dag.graph import ActionKey
from mudyla.ast.models import ActionDefinition, SourceLocation
from mudyla.dag.graph import ActionGraph, ActionNode
from mudyla.executor.retainer_executor import RetainerCompletion, RetainerDecision, RetainerOutcome, RetainerRequest, RetainerResult
from mudyla.logging.terminal_logger import LoggerMode, create_terminal_logger
from mudyla.logging.terminal_logger_pure import PureTerminalLogger
from mudyla.logging.terminal_logger_table import TableTerminalLogger
from mudyla.logging.display_session import DisplaySession
from mudyla.logging.formatters.output import OutputFormatter
from mudyla.logging.retainer_display import PlanningFacts, RetainerDisplay, RetainerRow
from mudyla.logging.terminal_output import MAX_LOG_CHARS


@pytest.mark.parametrize("encoding", ["utf-8", "ascii"])
def test_retainer_and_action_tables_share_presentation_configuration(encoding):
    with TextIOWrapper(BytesIO(), encoding=encoding) as stream:
        output = OutputFormatter(no_color=True, compact=True,
            console=Console(file=stream, width=120, height=30))
        display = RetainerDisplay(output, LoggerMode.TABLE, True, session=None, fullscreen=False)
        request = RetainerRequest(ActionKey.from_name("keep"), (), time.perf_counter())
        display.begin_retainer(request)
        retainer_table = display._table_section().renderables[0].renderables[1]
        logger = prepared_logger(TableTerminalLogger, [ActionKey.from_name("work")], output)
        action_table = logger._build_table()
        assert [column.header for column in retainer_table.columns] == ["Retainer", "Status", "Elapsed", "Result"]
        for name in ("box", "header_style", "border_style", "safe_box", "padding", "highlight"):
            assert getattr(retainer_table, name) == getattr(action_table, name), name


def output_formatter(*, terminal: bool) -> OutputFormatter:
    output = OutputFormatter(no_color=False, compact=True, console=Console(
        file=StringIO(), width=160, height=30, force_terminal=terminal, color_system="truecolor"))
    output.start_recording(defer=True)
    from mudyla.logging.formatters.output import RunInfoField
    output.print_run_field(RunInfoField.PROJECT_ROOT, Text("example"), "example")
    output.flush_recording()
    return output


def completion(request: RetainerRequest, *, outcome: RetainerOutcome) -> RetainerCompletion:
    retained = request.targets[:1] if outcome == RetainerOutcome.SUCCEEDED else ()
    result = RetainerResult(request.key, list(retained), bool(retained), 137.0, "", "Timeout expired" if outcome == RetainerOutcome.TIMED_OUT else "")
    return RetainerCompletion(request, result, outcome,
                              tuple(RetainerDecision(key, key in retained) for key in request.targets))


@pytest.mark.parametrize("mode", list(LoggerMode))
@pytest.mark.parametrize("outcome", [RetainerOutcome.NONZERO, RetainerOutcome.TIMED_OUT, RetainerOutcome.ERROR])
def test_failed_retainer_emits_full_authoritative_buffers_once(mode, outcome):
    output = output_formatter(terminal=False)
    display = RetainerDisplay(output, mode, True, session=None, fullscreen=False)
    request = RetainerRequest(ActionKey.from_name("failed-check"), (), time.perf_counter())
    result = RetainerResult(request.key, [], False, 93, "OUT_A\n\nOUT_B\n", "ERR_A\nERR_B\n")
    completed = RetainerCompletion(request, result, outcome, ())
    display.begin_retainer(request)
    display.retainer_output(request.key, "discarded preview\n", "stdout")
    display.end_retainer(completed)
    text = Text.from_ansi(output.console.file.getvalue()).plain
    for line in ("OUT_A", "OUT_B", "ERR_A", "ERR_B"):
        assert text.count(f"    | {line}") == 1
    assert text.index("failed-check") < text.index("    | OUT_A") < text.index("    | ERR_A")
    assert "    | \n" in text
    history = display.finish()
    with output.console.capture() as capture:
        output.console.print(history)
    final = Text.from_ansi(capture.get()).plain
    assert all(final.count(f"    | {line}") == 1 for line in ("OUT_A", "OUT_B", "ERR_A", "ERR_B"))
    assert display.rows[request.key].completion.result is result


@pytest.mark.parametrize("mode", [LoggerMode.PURE, LoggerMode.TABLE])
def test_live_failed_retainer_keeps_full_logs_and_stream_styles(mode):
    output = output_formatter(terminal=True)
    session = DisplaySession(output.console)
    display = RetainerDisplay(output, mode, True, session=session, fullscreen=False)
    request = RetainerRequest(ActionKey.from_name("failed-check"), (), time.perf_counter())
    result = RetainerResult(request.key, [], False, 93, "OUT_A\nOUT_B\n", "ERR_A\nERR_B\n")
    try:
        display.begin_retainer(request)
        display.end_retainer(RetainerCompletion(request, result, RetainerOutcome.NONZERO, ()))
        for renderable in (display.render(), display.finish()):
            lines = [Text.assemble(*[(segment.text, segment.style or "") for segment in row])
                     for row in output.console.render_lines(renderable, pad=False)]
            for token, color in [("OUT_A", None), ("OUT_B", None), ("ERR_A", "red"), ("ERR_B", "red")]:
                row = next(line for line in lines if f"| {token}" in line.plain)
                actual = row.get_style_at_offset(output.console, row.plain.index(token)).color
                assert actual is None if color is None else actual.name == color
                assert row.get_style_at_offset(output.console, row.plain.index(token)).dim is not True
    finally:
        display.close()
    assert not any(thread.name == "retainer-display" for thread in threading.enumerate())


@pytest.mark.parametrize("width", [36, 40, 80, 160])
@pytest.mark.parametrize("encoding", ["utf-8", "ascii"])
@pytest.mark.parametrize("no_color", [False, True])
def test_retainer_table_preserves_folded_results_and_stream_styles(width, encoding, no_color):
    import re

    with TextIOWrapper(BytesIO(), encoding=encoding, errors="replace") as stream:
        console = Console(file=stream, width=width, height=30, force_terminal=True,
                          color_system="truecolor", no_color=no_color)
        output = OutputFormatter(no_color=no_color, compact=True, console=console)
        output.start_recording(defer=True)
        display = RetainerDisplay(output, LoggerMode.TABLE, True,
                                  session=DisplaySession(console), fullscreen=False)
        try:
            running = RetainerRequest(ActionKey.from_name("check-running"), (), time.perf_counter())
            display.begin_retainer(running)
            display.retainer_output(running.key, "RUN_PROGRESS", "stdout")
            for name in ("ignore", "keep", "fail"):
                target = ActionKey.from_name("target-" + name)
                request = RetainerRequest(ActionKey.from_name("check-" + name), (target,), time.perf_counter())
                failed = name == "fail"
                result = RetainerResult(request.key, [target] if name == "keep" else [], name == "keep", 20,
                    "FAIL_OUT_A\nFAIL_OUT_B" if failed else "",
                    "FAIL_ERR_A\nFAIL_ERR_B" if failed else "")
                display.begin_retainer(request)
                display.end_retainer(RetainerCompletion(request, result,
                    RetainerOutcome.NONZERO if failed else RetainerOutcome.SUCCEEDED,
                    (RetainerDecision(target, name == "keep"),)))
                assert display.rows[request.key].completion.result is result
            history = display.finish()
            for renderable in (display.render(), history):
                with console.capture() as capture:
                    console.print(renderable)
                rendered = Text.from_ansi(capture.get()).split("\n")
                result_text = Text()
                separator = "|" if console.options.ascii_only else "│"
                for line in rendered:
                    if not line.plain.startswith(separator):
                        continue
                    cells = line.plain.split(separator, 4)
                    if len(cells) != 5:
                        continue
                    start = sum(len(cell) + 1 for cell in cells[:4])
                    end = line.plain.rfind(separator)
                    cell = line.plain[start:end]
                    result_text.append_text(line[start + len(cell) - len(cell.lstrip()):end - len(cell) + len(cell.rstrip())])
                assert "Result" in result_text.plain
                assert "RUN_PROGRESS" in result_text.plain and "target-keep" in result_text.plain
                assert "<empty>" in result_text.plain
                for token in ("FAIL_OUT_A", "FAIL_OUT_B", "FAIL_ERR_A", "FAIL_ERR_B"):
                    matches = list(re.finditer(r"\|\s*" + re.escape(token), result_text.plain))
                    assert len(matches) == 1
                    assert result_text.get_style_at_offset(console, matches[0].start()).dim is True
                    start = matches[0].end() - len(token)
                    for offset in range(start, start + len(token)):
                        style = result_text.get_style_at_offset(console, offset)
                        if no_color or "OUT" in token:
                            assert style.color is None
                        else:
                            assert style.color.get_truecolor() == console.get_style("red").color.get_truecolor()
                        assert style.dim is not True
        finally:
            display.close()
    assert not any(thread.name == "retainer-display" for thread in threading.enumerate())


def test_successful_retainer_remains_compact_in_verbose_mode():
    output = output_formatter(terminal=False)
    display = RetainerDisplay(output, LoggerMode.VERBOSE, True, session=None, fullscreen=False)
    request = RetainerRequest(ActionKey.from_name("keep"), (), time.perf_counter())
    result = RetainerResult(request.key, [], False, 1, "PREVIOUS_LINE\nLATEST_LINE\n", "")
    display.begin_retainer(request)
    display.retainer_output(request.key, result.stdout, "stdout")
    display.end_retainer(RetainerCompletion(request, result, RetainerOutcome.SUCCEEDED, ()))
    text = Text.from_ansi(output.console.file.getvalue()).plain
    assert "LATEST_LINE" in text and "PREVIOUS_LINE" not in text
    assert "| " not in text


def test_append_only_completion_arrives_before_second_retainer_and_is_not_replayed():
    output = output_formatter(terminal=False)
    display = RetainerDisplay(output, LoggerMode.PURE, True, session=None, fullscreen=False)
    first = RetainerRequest(ActionKey.from_name("keep-first"),
                            (ActionKey.from_name("retained"), ActionKey.from_name("ignored")), time.perf_counter())
    second = RetainerRequest(ActionKey.from_name("keep-second"), (ActionKey.from_name("other"),), time.perf_counter())
    display.begin_retainer(first)
    display.retainer_output(first.key, "progress\rfinal\n", "stdout")
    display.end_retainer(completion(first, outcome=RetainerOutcome.SUCCEEDED))
    text = output.console.file.getvalue()
    assert "keep-first" in text and "137ms" in text and "final" in text
    assert "retained" in text and "ignored" in text and "<empty>" not in text
    assert "keep-second" not in text
    display.begin_retainer(second)
    assert output.console.file.getvalue() == text
    display.end_retainer(completion(second, outcome=RetainerOutcome.SUCCEEDED))
    history = display.finish()
    output.remember(history)
    output.stop_recording(emit=False)
    assert output.console.file.getvalue().count("keep-first") == 1
    assert output.console.file.getvalue().count("keep-second") == 1
    assert output.console.file.getvalue().count("Retainers:") == 1
    assert output.console.file.getvalue().count("Run info:") == 1
    assert not any(thread.name == "retainer-display" for thread in threading.enumerate())


@pytest.mark.parametrize("mode", [LoggerMode.PURE, LoggerMode.TABLE])
def test_planning_live_owner_has_no_action_input_and_survives_handoff(mode, monkeypatch):
    output = output_formatter(terminal=True)
    logger = create_terminal_logger(mode, no_color=False, force_interactive=False, interactive=False,
        fullscreen=False, show_dirs=False, use_short_ids=True, plan_style="table", plan_minimize=True,
        dag_solver=None, console=output.console)
    session = logger.session
    assert session is not None
    request = RetainerRequest(ActionKey.from_name("keep"), (ActionKey.from_name("target"),), time.perf_counter())
    action = ActionKey.from_name("action")
    nodes = {key: ActionNode(key, ActionDefinition(key.id.name, [], {}, SourceLocation("test.md", 1, key.id.name)))
             for key in (*request.targets, action)}
    monkeypatch.setattr(PureTerminalLogger, "_setup_terminal", lambda self: pytest.fail("Planning acquired action input"))
    try:
        logger.start_run(Path.cwd().name, None)
        logger.report_project(Path.cwd(), True)
        logger.report_compilation(ActionGraph(nodes, {action}), 1)
        logger.start_retainers()
        logger.begin_retainer(request)
        live = session.live
        assert live is not None and "<empty>" in output.console.file.getvalue()
        logger.retainer_output(request.key, "before-exit", "stderr")
        assert "before-exit" in output.console.file.getvalue()
        assert "\x1b]11;" not in output.console.file.getvalue()
        assert "\x1b[?1000h" not in output.console.file.getvalue()
        logger.end_retainer(completion(request, outcome=RetainerOutcome.SUCCEEDED))
        logger.finish_retainers()
        assert session.live is live
        logger.report_plan(ActionGraph({action: nodes[action]}, {action}), [action], set(request.targets))
        monkeypatch.setattr(type(logger), "start", lambda self: None)
        logger.start_actions(Path.cwd(), {}, kill_callback=lambda: None, input_callback=lambda key, text: None)
        assert logger.console is session.console and logger.live is live
        assert logger.tasks.keys() == {action}
        assert not any(thread.name == "retainer-display" for thread in threading.enumerate())
    finally:
        logger.finish_run()
    assert session.live is None


def test_completed_duration_and_failure_message_come_from_executor_facts():
    output = output_formatter(terminal=False)
    display = RetainerDisplay(output, LoggerMode.PURE, True, session=None, fullscreen=False)
    request = RetainerRequest(ActionKey.from_name("keep"), (ActionKey.from_name("target"),), time.perf_counter() - 3)
    display.begin_retainer(request)
    display.retainer_output(request.key, "partial before timeout", "stdout")
    display.end_retainer(completion(request, outcome=RetainerOutcome.TIMED_OUT))
    text = output.console.file.getvalue()
    assert "137ms" in text and "3000ms" not in text
    assert "Timeout expired" in text and "partial before timeout" not in text
    assert "⊗" in text


def test_plan_facts_count_unique_retained_keys_and_omit_unknown_actions():
    target = ActionKey.from_name("target")
    facts = PlanningFacts(12.8, frozenset([target, target]), None)
    assert facts.summary().plain == "1 retained, planning took 13ms"
    assert str(facts.summary().style) == "dim"
    assert PlanningFacts(12.8, facts.retained_keys, 4).summary().plain == "4 actions with 1 retained, planned in 13ms"


def test_retainer_blank_completed_line_preserves_latest_visible_message():
    request = RetainerRequest(ActionKey.from_name("keep"), (), 0)
    row = RetainerRow(request, None, "", "stdout")
    row.receive("visible\n", "stdout")
    row.receive("\n", "stdout")
    assert row.latest == "visible", "A blank completed line erased the last visible retainer message"


def test_retainer_preview_keeps_only_bounded_partial_lines_and_split_controls():
    request = RetainerRequest(ActionKey.from_name("keep"), (), 0)
    row = RetainerRow(request, None, "", "stdout")
    for _ in range(72):
        row.receive("x" * MAX_LOG_CHARS + "\n", "stdout")
    assert row.stdout.partial == ""
    row.receive("\x1b[3", "stderr")
    row.receive("1mprogress\r\x1b[0mreplacement\n", "stderr")
    assert row.latest == "replacement" and row.stream == "stderr"
    row.receive("a" * (MAX_LOG_CHARS + 17), "stdout")
    assert len(row.stdout.partial) == MAX_LOG_CHARS and len(row.latest) == MAX_LOG_CHARS
    assert row.stderr.partial == ""


def test_completion_error_preview_uses_bounded_latest_line_without_changing_result():
    output = output_formatter(terminal=False)
    display = RetainerDisplay(output, LoggerMode.PURE, True, session=None, fullscreen=False)
    request = RetainerRequest(ActionKey.from_name("keep"), (), time.perf_counter())
    display.begin_retainer(request)
    display.retainer_output(request.key, "earlier fragment", "stderr")
    error = "error\n" + "x" * (MAX_LOG_CHARS * 16)
    result = RetainerResult(request.key, [], False, 137, "", error)
    display.end_retainer(RetainerCompletion(request, result, RetainerOutcome.NONZERO, ()))
    assert display.rows[request.key].latest == "x" * MAX_LOG_CHARS
    assert len(display.rows[request.key].stderr.partial) <= MAX_LOG_CHARS
    assert result.stderr == error


def test_retainer_parent_duration_separates_empty_latest_message():
    output = output_formatter(terminal=False)
    display = RetainerDisplay(output, LoggerMode.PURE, True, session=None, fullscreen=False)
    request = RetainerRequest(ActionKey.from_name("keep"), (), time.perf_counter())
    display.begin_retainer(request)
    display.end_retainer(completion(request, outcome=RetainerOutcome.SUCCEEDED))
    assert "137ms: <empty>" in Text.from_ansi(output.console.file.getvalue()).plain


def test_live_compiler_and_unique_retained_facts_precede_final_action_count():
    output = output_formatter(terminal=True)
    session = DisplaySession(output.console)
    display = RetainerDisplay(output, LoggerMode.PURE, True, session=session, fullscreen=False)
    target = ActionKey.from_name("target")
    first = RetainerRequest(ActionKey.from_name("first"), (target, target), time.perf_counter())
    second = RetainerRequest(ActionKey.from_name("second"), (target,), time.perf_counter())
    try:
        display.set_planning_facts(PlanningFacts(12.8, frozenset(), None))
        display.begin_retainer(first)
        assert "0 retained, planning took 13ms" in output.console.file.getvalue()
        display.end_retainer(completion(first, outcome=RetainerOutcome.SUCCEEDED))
        display.begin_retainer(second)
        output.console.print(display.render())
        assert "1 retained, planning took 13ms" in output.console.file.getvalue()
        assert "actions with" not in output.console.file.getvalue()
        display.end_retainer(completion(second, outcome=RetainerOutcome.SUCCEEDED))
        facts = display.complete_plan(4)
        assert facts.summary().plain == "4 actions with 1 retained, planned in 13ms"
        assert facts is display.facts
    finally:
        display.close()


def test_target_decisions_appear_only_on_completion_with_explicit_labels():
    output = output_formatter(terminal=False)
    display = RetainerDisplay(output, LoggerMode.PURE, True, session=None, fullscreen=False)
    request = RetainerRequest(ActionKey.from_name("keep"),
                             (ActionKey.from_name("alpha"), ActionKey.from_name("beta")), time.perf_counter())
    display.begin_retainer(request)
    before = StringIO()
    Console(file=before, width=160).print(display.render())
    assert "alpha" not in before.getvalue() and "beta" not in before.getvalue()
    display.end_retainer(completion(request, outcome=RetainerOutcome.SUCCEEDED))
    rendered = Text.from_ansi(output.console.file.getvalue()).plain
    assert "retained alpha @global" in rendered and "ignored beta @global" in rendered
    child = next(line for line in rendered.splitlines() if "ignored beta" in line)
    assert child == "  ○ ignored beta @global"


def test_retainer_status_color_does_not_spread_to_identity_duration_or_stdout():
    output = output_formatter(terminal=False)
    display = RetainerDisplay(output, LoggerMode.PURE, True, session=None, fullscreen=False)
    request = RetainerRequest(ActionKey.from_name("keep"), (ActionKey.from_name("alpha"),), time.perf_counter())
    display.begin_retainer(request)
    display.retainer_output(request.key, "neutral output", "stdout")
    display.end_retainer(completion(request, outcome=RetainerOutcome.SUCCEEDED))
    segments = list(output.console.render(display.render()))
    for text in ("keep", "alpha", "137ms", "neutral output"):
        matching = [segment for segment in segments if text in segment.text]
        assert matching, text
        assert all(segment.style is None or segment.style.color is None for segment in matching), text
    assert any(segment.text == "● " and segment.style.color.name == "green" for segment in segments)
