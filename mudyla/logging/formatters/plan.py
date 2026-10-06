"""Dependency-tree presentation shared by the transcript and live overview."""

from dataclasses import dataclass
from typing import Callable, Optional

from rich.console import Console, ConsoleOptions, Group, RenderResult, RenderableType
from rich.text import Text

from ...dag.context import ContextId
from ...dag.graph import ActionGraph, ActionKey
from .context import ContextFormatter
from .details import context_label, literal_text
from .sections import section

MIN_LABEL_WIDTH = 8


@dataclass(frozen=True)
class TreeRow:
    label: Text
    last_siblings: tuple[bool, ...]
    dashed: bool


@dataclass
class DependencyTree:
    rows: list[TreeRow]

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        rail = "| " if options.ascii_only else "│ "
        for row in self.rows:
            ancestors = "".join("  " if last else rail for last in row.last_siblings[:-1])
            guide = continuation = ""
            if row.last_siblings:
                last = row.last_siblings[-1]
                branch = ("`" if last else "+") if options.ascii_only else ("└" if last else "├")
                edge = ("." if options.ascii_only else "╌") if row.dashed else ("-" if options.ascii_only else "─")
                guide = ancestors + branch + edge
                continuation = ancestors + ("  " if last else rail)
            label_width = max(MIN_LABEL_WIDTH, options.max_width - len(guide))
            for index, segments in enumerate(console.render_lines(row.label, options.update(width=label_width), pad=False)):
                line = Text.assemble((guide if index == 0 else continuation, "dim"),
                                     *[(segment.text, segment.style or "") for segment in segments])
                yield from line.wrap(console, max(1, options.max_width), overflow="fold")


def sharing_counts(graph: ActionGraph, execution_order: list[ActionKey], goals: list[str]) -> dict[ActionKey, int]:
    """Count the unique goal contexts reached through each action's dependents."""
    def collect(action_key: ActionKey, visited: set[ActionKey]) -> set[str]:
        if action_key in visited:
            return set()
        visited.add(action_key)
        contexts = {str(action_key.context_id)} if action_key.id.name in goals else set()
        for dependent in graph.get_node(action_key).dependents:
            contexts.update(collect(dependent.action, visited))
        return contexts

    return {key: len(collect(key, set())) for key in execution_order}


def execution_tree(graph: ActionGraph, execution_order: list[ActionKey], formatter: ContextFormatter,
                   use_short_ids: bool, shared: dict[ActionKey, int],
                   status: Callable[[ActionKey], Text]) -> Group:
    """Render prerequisite roots and dependent branches from the existing graph."""
    positions = {key: index for index, key in enumerate(execution_order)}
    seen: set[ActionKey] = set()
    dependents: dict[ActionKey, list[tuple[ActionKey, str]]] = {key: [] for key in execution_order}
    roots = []
    for key in execution_order:
        dependencies = [dep for dep in graph.get_node(key).dependencies if dep.action in positions]
        if not dependencies:
            roots.append(key)
        for dep in dependencies:
            dependents[dep.action].append((key, "soft" if dep.soft else "weak" if dep.weak else ""))

    def add_action(rows: list[TreeRow], key: ActionKey, qualifier: str, parent_context: Optional[ContextId],
                   last_siblings: tuple[bool, ...]) -> None:
        label = Text()
        label.append_text(status(key))
        label.append_text(literal_text(key.id.name, "bold" if key in graph.goals else "not bold"))
        annotations: list[Text] = []
        if key.context_id != parent_context or key in seen:
            identity = context_label(key.context_id, formatter, use_short_ids)
            identity.stylize("dim not bold")
            annotations.append(identity)
        if key in graph.goals:
            annotations.append(Text("goal", style="dim"))
        if qualifier:
            annotations.append(Text(qualifier, style="dim"))
        if key in seen:
            annotations.append(Text("shared; shown above", style="dim"))
        elif shared.get(key, 1) > 1:
            annotations.append(Text(f"shared by {shared[key]} contexts", style="dim"))
        if annotations:
            label.append(" (", style="dim")
            label.append_text(Text("; ", style="dim").join(annotations))
            label.append(")", style="dim")
        rows.append(TreeRow(label, last_siblings, qualifier in {"weak", "soft"}))
        if key in seen:
            return
        seen.add(key)
        edges = sorted(dependents[key], key=lambda edge: (positions[edge[0]], {"": 0, "weak": 1, "soft": 2}[edge[1]]))
        for index, (dependent, edge_kind) in enumerate(edges):
            add_action(rows, dependent, edge_kind, key.context_id, last_siblings + (index == len(edges) - 1,))

    forest = []
    for key in roots:
        rows: list[TreeRow] = []
        add_action(rows, key, "", None, ())
        forest.append(DependencyTree(rows))
    return Group(*forest)


def tree_section(tree: RenderableType, ascii_only: bool) -> Group:
    ready, waiting = (">", "o") if ascii_only else ("◇", "○")
    solid, dashed = ("-", ".") if ascii_only else ("─", "╌")
    return section("Plan:", tree, None,
                   Text(f"{ready} deps ready / {waiting} waiting; {solid} strong / {dashed} weak or soft; "
                        "prerequisites first, branches may overlap", style="dim"))
