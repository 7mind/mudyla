"""Public preparation paths must share the solver's domain timeout boundary."""

from io import StringIO
import threading

import pytest

from mudyla.ast.models import ActionDefinition, SourceLocation
from mudyla.dag.display import build_display_edges
from mudyla.dag.graph import ActionGraph, ActionNode, ActionKey, Dependency
from mudyla.dag.solver.base import OverallTimeout
from mudyla.executor.engine import ExecutionEngine
from mudyla.logging.action_logger import LoggerMode
from mudyla.logging.action_logger_pure import ActionLoggerPure
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


@pytest.mark.parametrize("entry", ["builder", "pure", "engine"])
def test_layout_preparation_uses_domain_timeout(entry, tmp_path):
    graph, keys, display = dense_graph()
    output = OutputFormatter(no_color=True, compact=True)
    output.console.file = StringIO()
    threads = tuple(threading.enumerate())
    engine = None
    try:
        with pytest.raises(OverallTimeout):
            if entry == "builder":
                build_dag_layout(graph, keys, display=display)
            elif entry == "pure":
                ActionLoggerPure(keys, output, True, graph=graph, plan_display=display)
            else:
                engine = ExecutionEngine(
                    graph, tmp_path, {}, {}, {}, [], run_directory=tmp_path / "run",
                    without_nix=True, parallel_execution=False, no_color=True,
                    logger_mode=LoggerMode.PURE, force_interactive=False,
                    output=output, plan_display=display,
                )
                engine.execute_all()
    finally:
        assert tuple(threading.enumerate()) == threads
        if engine is not None:
            assert not engine._running_processes
            assert engine._current_logger is None
            assert engine._timeout_timer is None
