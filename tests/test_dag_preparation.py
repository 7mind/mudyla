"""Public preparation paths must share the solver's domain timeout boundary."""

from tests.logger_fixtures import prepared_logger

from io import StringIO
import threading

import pytest

from mudyla.ast.models import ActionDefinition, SourceLocation
from mudyla.dag.display import build_display_edges
from mudyla.dag.graph import ActionGraph, ActionNode, ActionKey, Dependency
from mudyla.dag.solver.base import OverallTimeout
from mudyla.logging.terminal_logger import LoggerMode
from mudyla.logging.terminal_logger_pure import PureTerminalLogger
from mudyla.logging.formatters import OutputFormatter
from mudyla.logging.formatters.dag import build_dag_layout


def dense_graph():
    keys = [ActionKey.from_name(f"node{index:02}") for index in range(30)]
    nodes = {
        key: ActionNode(key, ActionDefinition(key.id.name, [], {},
                                             SourceLocation("architecture", 1, key.id.name)))
        for key in keys
    }
    for source in range(len(keys)):
        for target in range(source + 1, len(keys)):
            nodes[keys[target]].dependencies.add(Dependency(keys[source]))
            nodes[keys[source]].dependents.add(Dependency(keys[target]))
    graph = ActionGraph(nodes, {keys[-1]})
    return graph, keys, build_display_edges(graph, tuple(keys), full=True)


@pytest.mark.parametrize("entry", ["builder", "logger"])
def test_layout_preparation_uses_domain_timeout(entry, tmp_path):
    graph, keys, display = dense_graph()
    output = OutputFormatter(no_color=True, compact=True)
    output.console.file = StringIO()
    threads = tuple(threading.enumerate())
    try:
        with pytest.raises(OverallTimeout):
            if entry == "builder":
                build_dag_layout(graph, keys, display=display)
            else:
                prepared_logger(PureTerminalLogger, keys, output, True, graph=graph, plan_display=display)
    finally:
        assert tuple(threading.enumerate()) == threads
