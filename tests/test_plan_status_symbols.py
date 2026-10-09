"""Static plan legends describe the same readiness markers as live action rows."""

from io import BytesIO, TextIOWrapper

import pytest
from rich.console import Console
from rich.text import Text

from mudyla.dag.display import build_display_edges
from mudyla.logging.formatters import OutputFormatter
from mudyla.logging.formatters.branches import BranchTheme
from mudyla.logging.formatters.dag import build_dag_layout, dag_section, execution_dag
from tests.test_plan_dag import crossing_graph


@pytest.mark.parametrize("ascii_only,ready,waiting", [(False, "○", "◌"), (True, "o", "*")])
@pytest.mark.parametrize("mode", ["auto", "grid-low"])
def test_default_dag_readiness_legend_matches_static_callbacks(ascii_only, ready, waiting, mode):
    graph, keys = crossing_graph()
    stream = TextIOWrapper(BytesIO(), encoding="ascii" if ascii_only else "utf-8")
    output = OutputFormatter(no_color=True, compact=True, console=Console(file=stream, width=180, force_terminal=True))
    display = build_display_edges(graph, tuple(keys), full=True)
    layout = build_dag_layout(graph, keys, display=display, mode=mode)
    dag = execution_dag(graph, keys, output.context, True, {}, lambda key: Text("o "),
                        lambda key: "dim", lambda: BranchTheme.DISABLED, layout=layout)
    with output.console.capture() as capture:
        output.console.print(dag_section(dag, output.symbols))
    plain = Text.from_ansi(capture.get()).plain
    assert f"{ready} deps ready / {waiting} waiting" in plain
