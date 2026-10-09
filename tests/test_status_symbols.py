"""Completion marks use the same circle symbols as action nodes."""

from tests.logger_fixtures import prepared_logger, static_tree

from io import BytesIO, StringIO, TextIOWrapper

import pytest
from rich.console import Console
from rich.text import Text

from mudyla.cli import CLI
from mudyla.executor.retainer_executor import RetainerCompletion, RetainerDecision, RetainerOutcome, RetainerRequest, RetainerResult
from mudyla.logging.formatters import OutputFormatter
from mudyla.logging.terminal_logger_pure import PureTerminalLogger
from mudyla.logging.terminal_logger_table import TaskStatus
from mudyla.logging.formatters.symbols import StatusSymbol, SymbolsFormatter
from mudyla.logging.terminal_logger import LoggerMode
from mudyla.logging.retainer_display import RetainerDisplay
from tests.test_plan_dag import crossing_graph


@pytest.mark.parametrize("unicode_supported,expected", [(True, ("●", "⊗")), (False, ("+", "x"))])
def test_completion_symbols_match_node_outcomes(unicode_supported, expected):
    stream = TextIOWrapper(BytesIO(), encoding="utf-8" if unicode_supported else "ascii")
    symbols = SymbolsFormatter(Console(file=stream), decorative_ascii=True)
    assert (symbols.Check, symbols.Cross) == expected


def test_completion_circles_are_independent_of_decorative_ascii_policy():
    output = OutputFormatter(no_color=True, compact=True, console=Console(file=StringIO(), no_color=True))
    assert (output.symbols.Check, output.symbols.Cross) == ("●", "⊗")
    assert output.symbols.Globe == "*"


def test_retainer_result_marks_share_completion_symbols():
    _, keys = crossing_graph()
    output = OutputFormatter(no_color=False, compact=True, console=Console(file=StringIO(), force_terminal=True, width=180, color_system="truecolor"))
    display = RetainerDisplay(output, LoggerMode.PURE, True, session=None, fullscreen=False)
    requests = [RetainerRequest(keys[0], (keys[1],), 0), RetainerRequest(keys[1], (keys[2],), 0)]
    results = [RetainerResult(keys[0], [keys[1]], True, 2), RetainerResult(keys[1], [], False, 3)]
    with output.console.capture() as capture:
        for request, result, outcome in zip(requests, results, (RetainerOutcome.SUCCEEDED, RetainerOutcome.NONZERO)):
            display.begin_retainer(request)
            display.end_retainer(RetainerCompletion(request, result, outcome,
                                  tuple(RetainerDecision(key, result.retained) for key in request.targets)))
    rows = Text.from_ansi(capture.get()).plain.splitlines()
    assert any(row.startswith("● ") and keys[0].id.name in row for row in rows), rows
    assert any(row.startswith("⊗ ") and keys[1].id.name in row for row in rows), rows


def test_live_and_static_nodes_share_existing_symbol_formatter(monkeypatch):
    from mudyla.dag.display import build_display_edges
    from mudyla.logging.formatters.plan import tree_section

    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=False, compact=True, console=Console(file=StringIO(), force_terminal=True, width=180, color_system="truecolor"))
    original = SymbolsFormatter.status
    calls = []

    def status(self, symbol, *, now):
        calls.append((symbol, now))
        return original(self, symbol, now=now)

    monkeypatch.setattr(SymbolsFormatter, "status", status)
    logger = prepared_logger(PureTerminalLogger, keys, output, True, graph=graph)
    logger.tasks[keys[0]].status = TaskStatus.RESTORED
    assert logger._status_marker(keys[0], .25).plain == "◉ "
    assert calls[-1][0:2] == (StatusSymbol.RESTORED, .25)
    calls.clear()
    from mudyla.logging.formatters.plan import execution_tree
    tree = execution_tree(graph, keys, logger.output.context, True, {}, logger._initial_status,
                          display=build_display_edges(graph, tuple(keys), full=True))
    with output.console.capture() as capture:
        output.console.print(tree_section(tree, output.symbols))
    assert "○ deps ready / ◌ waiting" in Text.from_ansi(capture.get()).plain
    assert {call[0] for call in calls} == {StatusSymbol.READY, StatusSymbol.WAITING}
