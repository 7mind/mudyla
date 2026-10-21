"""Public preparation paths must share the solver's domain timeout boundary."""

import threading
import time

import pytest

from mudyla.ast.models import ActionDefinition, SourceLocation
from mudyla.dag.display import build_display_edges
from mudyla.dag.graph import ActionGraph, ActionNode, ActionKey, Dependency
from mudyla.dag.solver.base import OverallTimeout
from mudyla.dag.solver.budget import LayoutBudget
from mudyla.logging.formatters import dag
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


@pytest.mark.parametrize("phase", ["build_display_edges", "build_solver_input"])
def test_layout_preparation_uses_caller_owned_domain_timeout(phase, monkeypatch):
    graph, keys, _ = dense_graph()
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    original = getattr(dag, phase)

    def delayed(*args, **kwargs):
        clock[0] = 1
        return original(*args, **kwargs)

    monkeypatch.setattr(dag, phase, delayed)
    threads = tuple(threading.enumerate())
    try:
        with pytest.raises(OverallTimeout):
            build_dag_layout(graph, keys, budget=LayoutBudget(.2))
    finally:
        assert tuple(threading.enumerate()) == threads
