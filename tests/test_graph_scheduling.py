"""Typed parallel dependencies must not release an action prematurely."""

from mudyla.cli_args import ActionInvocation, ParsedCLIInputs
from mudyla.dag.compiler import DAGCompiler
from mudyla.dag.display import build_display_edges
from mudyla.dag.solver.graph import build_solver_input
from mudyla.dag.solver.model import NodeSize
from mudyla.parser.markdown_parser import MarkdownParser


def test_parallel_strengths_wait_for_every_prerequisite(tmp_path):
    path=tmp_path/'actions.md'
    path.write_text('''# action: a

```bash
echo A
```

# action: b

```bash
echo B
```

# action: aa-goal

```bash
dep action.a
weak action.a
dep action.b
echo GOAL
```
''',encoding='utf-8')
    document=MarkdownParser().parse_files([path])
    inputs=ParsedCLIInputs({}, {}, {}, [ActionInvocation('aa-goal', {}, {}, {})], [])
    graph=DAGCompiler(document,inputs).compile().prune_to_goals()
    order=graph.get_execution_order()
    assert [key.id.name for key in order]==['a','b','aa-goal']
    model=build_solver_input(graph,order,{key:NodeSize(180,42) for key in order}, display=build_display_edges(graph, tuple(order), full=True))
    assert len(model.edges)==3
    assert sorted(edge.kind for edge in model.edges if edge.source.id.name=='a')==['strong','weak']
