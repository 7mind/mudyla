"""Project one immutable layered dependency layout into terminal rows."""

from dataclasses import dataclass, field
from typing import Callable, Optional

from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.segment import Segment
from rich.style import Style
from rich.text import Span, Text

from ...dag.graph import ActionGraph, ActionKey, Dependency
from .context import ContextFormatter
from .details import context_label, literal_text
from . import layered
from .layered import Direction, LayeredLayout, LayoutCell
from .plan import MIN_LABEL_WIDTH
from .sections import section


UNICODE_LINES = (" ", "│", "│", "│", "─", "╯", "╮", "┤", "─", "╰", "╭", "├", "─", "┴", "┬", "┼")


@dataclass(frozen=True)
class DagEdge:
    source: ActionKey
    target: ActionKey
    dependency: Dependency

    @property
    def kind(self) -> str:
        return "soft" if self.dependency.soft else "weak" if self.dependency.weak else "strong"


@dataclass(frozen=True)
class DagLayout:
    keys: tuple[ActionKey, ...]
    edges: tuple[DagEdge, ...]
    geometry: LayeredLayout
    execution_order: tuple[ActionKey, ...]


TextAttributes = tuple[str, tuple[Span, ...], str | Style, Optional[str], Optional[str], Optional[bool], str, Optional[int]]
FrameAttributes = tuple[tuple[TextAttributes, TextAttributes], ...]


def text_attributes(text: Text) -> TextAttributes:
    return (text.plain, tuple(text.spans), text.style, text.justify, text.overflow, text.no_wrap, text.end, text.tab_size)


@dataclass
class RenderedDagRow:
    label: TextAttributes
    status: TextAttributes
    lines: list[list[Segment]]
    label_rows: int


@dataclass
class DependencyDag:
    graph: ActionGraph
    layout: DagLayout
    formatter: ContextFormatter
    use_short_ids: bool
    shared: dict[ActionKey, int]
    status: Callable[[ActionKey], Text]
    edge_style: Callable[[ActionKey], str]
    _rendered_rows: dict[ActionKey, RenderedDagRow] = field(default_factory=dict, init=False, repr=False)
    _render_options: Optional[tuple[ConsoleOptions, Optional[str], bool]] = field(default=None, init=False, repr=False)
    _frame_attributes: Optional[tuple[FrameAttributes, tuple[str, ...]]] = field(default=None, init=False, repr=False)
    _frame: Optional[tuple[list[list[Segment]], dict[ActionKey, range]]] = field(default=None, init=False, repr=False)

    @property
    def edges(self) -> tuple[DagEdge, ...]:
        return self.layout.edges

    @property
    def crossings(self) -> bool:
        return self.layout.geometry.crossings > 0

    def label_parts(self, key: ActionKey) -> tuple[Text, Text]:
        label = literal_text(key.id.name, "bold" if key in self.graph.goals else "not bold")
        identity = context_label(key.context_id, self.formatter, self.use_short_ids)
        identity.stylize("dim not bold")
        annotations = [identity]
        if key in self.graph.goals:
            annotations.append(Text("goal", style="dim not bold"))
        if self.shared.get(key, 1) > 1:
            annotations.append(Text(f"shared by {self.shared[key]} contexts", style="dim not bold"))
        annotation = Text("(", style="dim not bold") + Text("; ", style="dim not bold").join(annotations) + Text(")", style="dim not bold")
        return label, annotation

    def _label(self, key: ActionKey) -> Text:
        name, annotation = self.label_parts(key)
        return name + Text(" ") + annotation

    def _gutter(self, cells: tuple[LayoutCell, ...], styles: tuple[str, ...], ascii_only: bool,
                node: Optional[tuple[int, Text]]) -> Text:
        gutter = Text(end="")
        by_column = {cell.column: cell for cell in cells}
        node_column, status = node if node is not None else (-1, Text())
        for column in range(self.layout.geometry.width):
            if column == node_column:
                glyph = status.copy()
                glyph.rstrip()
                assert glyph.cell_len == 1, "Dependency status must occupy one cell"
                gutter.append_text(glyph)
                continue
            cell = by_column.get(column)
            if cell is None:
                gutter.append(" ")
                continue
            edge_ids = tuple(connection.edge for connection in cell.connections)
            active = next((edge for edge in edge_ids if "not dim" in styles[edge]), edge_ids[0])
            weak = all(self.edges[edge].kind != "strong" for edge in edge_ids)
            vertical = cell.directions == Direction.UP | Direction.DOWN
            horizontal = cell.directions == Direction.LEFT | Direction.RIGHT
            if cell.crossing:
                glyph_text = "x" if ascii_only else "╪"
            elif vertical:
                glyph_text = (":" if ascii_only else "╎") if weak else ("|" if ascii_only else "│")
            elif horizontal:
                glyph_text = ("." if ascii_only else "╌") if weak else ("-" if ascii_only else "─")
            else:
                glyph_text = "+" if ascii_only else UNICODE_LINES[cell.directions]
            gutter.append(glyph_text, style=styles[active])
        return gutter

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        lines, _ = self.visual_lines(console, options, lambda key, width: self._label(key))
        for line in lines:
            yield from line
            yield Segment.line()

    def visual_lines(self, console: Console, options: ConsoleOptions,
                     node_label: Callable[[ActionKey, int], Text]) -> tuple[list[list[Segment]], dict[ActionKey, range]]:
        configuration = options, console.color_system, console.no_color
        if configuration != self._render_options:
            self._rendered_rows.clear()
            self._render_options = options.update(), console.color_system, console.no_color
            self._frame_attributes = None
        geometry = self.layout.geometry
        narrow = geometry.width + 1 + MIN_LABEL_WIDTH > options.max_width
        statuses = {key: self.status(key) for key in self.layout.keys}
        if narrow:
            for status in statuses.values():
                status.truncate(max(0, options.max_width - 1), overflow="crop")
        labels = {key: node_label(key, max(1, options.max_width -
                                         (statuses[key].cell_len if narrow else geometry.width + 1)))
                  for key in self.layout.keys}
        edge_styles = tuple(self.edge_style(edge.target) for edge in self.edges)
        attributes = (tuple((text_attributes(labels[key]), text_attributes(statuses[key])) for key in self.layout.keys),
                      edge_styles)
        if attributes == self._frame_attributes and len(self._rendered_rows) == len(self.layout.keys):
            assert self._frame is not None
            return self._frame
        lines: list[list[Segment]] = []
        node_rows: dict[ActionKey, range] = {}
        incoming: dict[ActionKey, list[DagEdge]] = {key: [] for key in self.layout.keys}
        if narrow:
            lines.extend(console.render_lines(Text("Plan too narrow for connected lanes; prerequisites listed below.", style="dim"),
                                              options, pad=False))
            for edge in self.edges:
                incoming[edge.target].append(edge)
        for rank, key in enumerate(self.layout.keys):
            label_attributes, status_attributes = attributes[0][rank]
            rendered = self._rendered_rows.get(key)
            if rendered is None or rendered.label != label_attributes or rendered.status != status_attributes:
                label_width = max(1, options.max_width - (statuses[key].cell_len if narrow else geometry.width + 1))
                block = console.render_lines(labels[key], options.update(width=label_width), pad=False)
                if narrow:
                    wrapped: list[list[Segment]] = []
                    for index, segments in enumerate(block):
                        line = statuses[key].copy() if index == 0 else Text(" " * statuses[key].cell_len)
                        line.append_text(Text.assemble(*[(segment.text, segment.style or "") for segment in segments]))
                        wrapped.extend(console.render_lines(line, options, pad=False))
                    block = wrapped
                label_rows = len(block)
                for edge in incoming[key]:
                    reference = Text("  needs ", style="dim") + self._label(edge.source)
                    reference.append(f" ({edge.kind})", style="dim")
                    if edge.dependency.retainer_action is not None:
                        retainer = edge.dependency.retainer_action
                        reference.append(" retainer: ", style="dim")
                        reference.append_text(literal_text(retainer.id.name))
                        reference.append(" ")
                        reference.append_text(context_label(retainer.context_id, self.formatter, self.use_short_ids))
                    block.extend(console.render_lines(reference, options, pad=False))
                rendered = RenderedDagRow(label_attributes, status_attributes, block, label_rows)
                self._rendered_rows[key] = rendered
            start = len(lines)
            if narrow:
                lines.extend(rendered.lines)
                if rank < len(geometry.connector_rows) and geometry.connector_rows[rank] == ((),):
                    lines.append([])
            else:
                for index, segments in enumerate(rendered.lines):
                    cells = geometry.action_rows[rank] if index == 0 else geometry.continuation_rows[rank]
                    node = (geometry.action_columns[rank], statuses[key]) if index == 0 else None
                    gutter = self._gutter(cells, edge_styles, options.ascii_only, node)
                    gutter.append(" ")
                    lines.append([*console.render(gutter, options), *segments])
                if rank < len(geometry.connector_rows):
                    lines.extend(list(console.render(self._gutter(cells, edge_styles, options.ascii_only, None), options))
                                 for cells in geometry.connector_rows[rank])
            node_rows[key] = range(start, start + rendered.label_rows)
        self._frame_attributes = attributes
        self._frame = lines, node_rows
        return self._frame


def _presentation_order(keys: tuple[ActionKey, ...], edges: tuple[DagEdge, ...]) -> tuple[ActionKey, ...]:
    neighbors: dict[ActionKey, set[ActionKey]] = {key: set() for key in keys}
    for edge in edges:
        neighbors[edge.source].add(edge.target)
        neighbors[edge.target].add(edge.source)
    unseen = set(keys)
    components: list[tuple[ActionKey, ...]] = []
    for first in keys:
        if first not in unseen:
            continue
        pending = [first]
        connected: set[ActionKey] = set()
        while pending:
            key = pending.pop()
            if key in connected:
                continue
            connected.add(key)
            pending.extend(neighbors[key] - connected)
        unseen.difference_update(connected)
        components.append(tuple(key for key in keys if key in connected))
    return tuple(key for component in components for key in component)


def build_dag_layout(graph: ActionGraph, execution_order: list[ActionKey]) -> DagLayout:
    positions = {key: index for index, key in enumerate(execution_order)}
    assert len(positions) == len(execution_order), "Plan action keys must be unique"
    edges = [DagEdge(dependency.action, key, dependency) for key in execution_order
             for dependency in graph.get_node(key).dependencies if dependency.action in positions]
    edges.sort(key=lambda edge: (positions[edge.source], positions[edge.target],
                                {"strong": 0, "weak": 1, "soft": 2}[edge.kind],
                                str(edge.dependency.retainer_action)))
    keys = _presentation_order(tuple(execution_order), tuple(edges))
    presentation = {key: index for index, key in enumerate(keys)}
    endpoints = tuple((presentation[edge.source], presentation[edge.target]) for edge in edges)
    geometry = layered.solve_layered_layout(len(keys), endpoints)
    return DagLayout(keys, tuple(edges), geometry, tuple(execution_order))


def execution_dag(graph: ActionGraph, execution_order: list[ActionKey], formatter: ContextFormatter,
                  use_short_ids: bool, shared: dict[ActionKey, int], status: Callable[[ActionKey], Text],
                  edge_style: Callable[[ActionKey], str], *, layout: DagLayout) -> DependencyDag:
    assert layout.execution_order == tuple(execution_order), "Dependency layout must match scheduler order"
    return DependencyDag(graph, layout, formatter, use_short_ids, shared, status, edge_style)


def dag_section(dag: DependencyDag, ascii_only: bool) -> Group:
    ready, waiting = (">", "o") if ascii_only else ("◇", "○")
    solid, dashed, crossing = ("|", ":", "x") if ascii_only else ("│", "╎", "╪")
    crossings = f"{crossing} crossing without a join; " if dag.crossings else ""
    return section("Plan:", dag, None,
                   Text(f"{ready} deps ready / {waiting} waiting; {solid} strong / {dashed} weak or soft; "
                        f"{crossings}prerequisites first", style="dim"))
