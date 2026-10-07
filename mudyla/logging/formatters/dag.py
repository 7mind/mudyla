"""Connected dependency lanes in the scheduler's existing action order."""

from dataclasses import dataclass, field
from typing import Callable, Optional

from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.segment import Segment
from rich.style import Style
from rich.text import Span, Text

from ...dag.graph import ActionGraph, ActionKey, Dependency
from .context import ContextFormatter
from .details import context_label, literal_text
from .plan import MIN_LABEL_WIDTH
from .sections import section


@dataclass(frozen=True)
class DagEdge:
    source: ActionKey
    target: ActionKey
    dependency: Dependency

    @property
    def kind(self) -> str:
        return "soft" if self.dependency.soft else "weak" if self.dependency.weak else "strong"


@dataclass(frozen=True)
class DagRow:
    key: ActionKey
    lane: int
    entry: tuple[Optional[DagEdge], ...]
    before: tuple[Optional[DagEdge], ...]
    after: tuple[Optional[DagEdge], ...]


TextAttributes = tuple[str, tuple[Span, ...], str | Style, Optional[str], Optional[str], Optional[bool], str, Optional[int]]


def text_attributes(text: Text) -> TextAttributes:
    return (text.plain, tuple(text.spans), text.style, text.justify, text.overflow, text.no_wrap, text.end, text.tab_size)


@dataclass
class RenderedDagRow:
    label: TextAttributes
    status: TextAttributes
    edge_styles: tuple[str, ...]
    lines: list[list[Segment]]
    anchor: int
    label_rows: int


@dataclass
class DependencyDag:
    graph: ActionGraph
    rows: list[DagRow]
    edges: list[DagEdge]
    lane_count: int
    crossings: bool
    formatter: ContextFormatter
    use_short_ids: bool
    shared: dict[ActionKey, int]
    status: Callable[[ActionKey], Text]
    edge_style: Callable[[ActionKey], str]
    _rendered_rows: dict[ActionKey, RenderedDagRow] = field(default_factory=dict, init=False, repr=False)
    _render_options: Optional[tuple[ConsoleOptions, Optional[str], bool]] = field(default=None, init=False, repr=False)

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

    def _rail(self, edge: DagEdge, ascii_only: bool) -> Text:
        glyph = ("|" if ascii_only else "│") if edge.kind == "strong" else (":" if ascii_only else "╎")
        return Text(glyph, style=self.edge_style(edge.target))

    def _continuation(self, row: DagRow, spacing: int, ascii_only: bool) -> Text:
        return Text(" " * (spacing - 1)).join([
            self._rail(edge, ascii_only) if edge is not None else Text(" ") for edge in row.after
        ]).append(" " * max(0, (self.lane_count - len(row.after)) * spacing))

    def _transitions(self, row: DagRow, spacing: int, ascii_only: bool) -> list[Text]:
        destinations = {edge: lane for lane, edge in enumerate(row.before) if edge is not None}
        current = [*row.entry, *([None] * (self.lane_count - len(row.entry)))]
        moves = [(lane, destinations[edge], edge) for lane, edge in enumerate(row.entry) if edge is not None]
        assert [edge for edge in row.entry if edge is not None] == [edge for edge in row.before if edge is not None]
        displacements = [destination - source for source, destination, _ in moves if destination != source]
        if spacing == 2 and not ascii_only and len(displacements) > 2 and set(displacements) in ({-1}, {1}):
            direction = displacements[0]
            positions = {2 * lane: edge for lane, edge in enumerate(row.entry) if edge is not None}
            lines = []
            for _ in range(2):
                cells: list[Optional[Text]] = [None] * ((self.lane_count - 1) * 2 + 1)
                following: dict[int, DagEdge] = {}
                for column, edge in positions.items():
                    target = column if column == 2 * destinations[edge] else column + direction
                    assert cells[column] is None and cells[target] is None
                    if column == target:
                        cells[column] = self._rail(edge, False)
                    else:
                        style = self.edge_style(edge.target)
                        cells[column] = Text("╰" if direction > 0 else "╯", style=style)
                        cells[target] = Text("╮" if direction > 0 else "╭", style=style)
                    following[target] = edge
                lines.append(Text().join(cell if cell is not None else Text(" ") for cell in cells))
                positions = following
            assert positions == {2 * lane: edge for lane, edge in enumerate(row.before) if edge is not None}
            return lines
        ordered = sorted((move for move in moves if move[1] < move[0]))
        ordered.extend(sorted((move for move in moves if move[1] > move[0]), reverse=True))
        lines = []
        for source, destination, edge in ordered:
            left, right = sorted((source, destination))
            assert all(current[lane] is None for lane in range(left, right + 1) if lane != source), "Lane shift crossed an open edge"
            line = Text()
            for column in range((self.lane_count - 1) * spacing + 1):
                lane, between = divmod(column, spacing)
                if left * spacing <= column <= right * spacing:
                    if column == source * spacing:
                        glyph = ("\\" if source < destination else "/") if ascii_only else ("╰" if source < destination else "╯")
                    elif column == destination * spacing:
                        glyph = ("\\" if source < destination else "/") if ascii_only else ("╮" if source < destination else "╭")
                    else:
                        glyph = ("-" if ascii_only else "─") if edge.kind == "strong" else ("." if ascii_only else "╌")
                    line.append(glyph, style=self.edge_style(edge.target))
                elif not between:
                    occupant = current[lane]
                    line.append_text(self._rail(occupant, ascii_only) if occupant is not None else Text(" "))
                else:
                    line.append(" ")
            lines.append(line)
            current[source], current[destination] = None, edge
        assert current[:len(row.before)] == list(row.before)
        return lines

    def _gutter(self, row: DagRow, spacing: int, ascii_only: bool) -> Text:
        incident = {lane: edge for lane, edge in enumerate(row.before) if edge is not None and edge.target == row.key}
        incident.update({lane: edge for lane, edge in enumerate(row.after) if edge is not None and edge.source == row.key})
        left, right = min([row.lane, *incident]), max([row.lane, *incident])
        gutter = Text()
        for column in range((self.lane_count - 1) * spacing + 1):
            lane, between = divmod(column, spacing)
            if not between and lane == row.lane:
                node = self.status(row.key).copy()
                node.rstrip()
                gutter.append_text(node)
                continue
            crossing = [edge for endpoint, edge in incident.items()
                        if min(endpoint, row.lane) * spacing <= column <= max(endpoint, row.lane) * spacing]
            if crossing:
                active = next((edge for edge in crossing if "not dim" in self.edge_style(edge.target)), crossing[0])
                style = self.edge_style(active.target)
                if between or lane not in incident:
                    existing = row.before[lane] if lane < len(row.before) else None
                    if not between and existing is not None:
                        glyph = "x" if ascii_only else "╪"
                    else:
                        glyph = ("-" if ascii_only else "─") if any(edge.kind == "strong" for edge in crossing) else ("." if ascii_only else "╌")
                else:
                    up = lane < len(row.before) and row.before[lane] is not None
                    down = lane < len(row.after) and row.after[lane] is not None
                    if ascii_only:
                        glyph = "+"
                    elif left < lane < right:
                        glyph = "┼" if up and down else "┴" if up else "┬"
                    elif lane < row.lane:
                        glyph = "├" if up and down else "╰" if up else "╭"
                    else:
                        glyph = "┤" if up and down else "╯" if up else "╮"
                gutter.append(glyph, style=style)
            elif not between:
                edge = row.before[lane] if lane < len(row.before) else None
                gutter.append_text(self._rail(edge, ascii_only) if edge is not None else Text(" "))
            else:
                gutter.append(" ")
        return gutter

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        lines, _ = self.visual_lines(console, options, lambda key, width: self._label(key))
        for line in lines:
            yield from line
            yield Segment.line()

    def visual_lines(self, console: Console, options: ConsoleOptions,
                     node_label: Callable[[ActionKey, int], Text]) -> tuple[list[list[Segment]], dict[ActionKey, range]]:
        configuration = (options, console.color_system, console.no_color)
        if configuration != self._render_options:
            self._rendered_rows.clear()
            self._render_options = (options.update(), console.color_system, console.no_color)
        lines: list[list[Segment]] = []
        node_rows: dict[ActionKey, range] = {}
        spacing = 2 if self.lane_count * 2 + MIN_LABEL_WIDTH <= options.max_width else 1
        gutter_width = (self.lane_count - 1) * spacing + 1
        narrow = gutter_width + 1 + MIN_LABEL_WIDTH > options.max_width
        incoming: dict[ActionKey, list[DagEdge]] = {}
        if narrow:
            lines.extend(console.render_lines(Text("Plan too narrow for connected lanes; prerequisites listed below.", style="dim"), options, pad=False))
            incoming = {row.key: [] for row in self.rows}
            for edge in self.edges:
                incoming[edge.target].append(edge)
        styles = {row.key: self.edge_style(row.key) for row in self.rows}
        for index, row in enumerate(self.rows):
            status = self.status(row.key)
            if narrow:
                status.truncate(max(0, options.max_width - 1), overflow="crop")
            label_width = max(1, options.max_width - (status.cell_len if narrow else gutter_width + 1))
            label = node_label(row.key, label_width)
            label_attributes, status_attributes = text_attributes(label), text_attributes(status)
            edge_styles = tuple(styles[edge.target] for lanes in (row.entry, row.before, row.after)
                                for edge in lanes if edge is not None)
            rendered = self._rendered_rows.get(row.key)
            if (rendered is None or rendered.label != label_attributes or rendered.status != status_attributes
                    or rendered.edge_styles != edge_styles):
                block: list[list[Segment]] = []
                if narrow:
                    anchor = 0
                    for line_index, segments in enumerate(console.render_lines(label, options.update(width=label_width), pad=False)):
                        line = Text()
                        line.append_text(status if line_index == 0 else Text(" " * status.cell_len))
                        line.append_text(Text.assemble(*[(segment.text, segment.style or "") for segment in segments]))
                        block.extend(console.render_lines(line, options, pad=False))
                    label_rows = len(block)
                    for edge in incoming[row.key]:
                        reference = Text("  needs ", style="dim") + self._label(edge.source)
                        reference.append(f" ({edge.kind})", style="dim")
                        if edge.dependency.retainer_action is not None:
                            retainer = edge.dependency.retainer_action
                            reference.append(" retainer: ", style="dim")
                            reference.append_text(literal_text(retainer.id.name))
                            reference.append(" ")
                            reference.append_text(context_label(retainer.context_id, self.formatter, self.use_short_ids))
                        block.extend(console.render_lines(reference, options, pad=False))
                else:
                    for transition in self._transitions(row, spacing, options.ascii_only):
                        block.extend(console.render_lines(transition, options, pad=False))
                    continuation = self._continuation(row, spacing, options.ascii_only)
                    anchor = len(block)
                    for line_index, segments in enumerate(console.render_lines(label, options.update(width=label_width), pad=False)):
                        line = self._gutter(row, spacing, options.ascii_only) if line_index == 0 else continuation.copy()
                        line.append(" ")
                        line.append_text(Text.assemble(*[(segment.text, segment.style or "") for segment in segments]))
                        block.extend(console.render_lines(line, options, pad=False))
                    label_rows = len(block) - anchor
                    if index < len(self.rows) - 1 and any(row.after):
                        block.extend(console.render_lines(continuation, options, pad=False))
                rendered = RenderedDagRow(label_attributes, status_attributes, edge_styles, block, anchor, label_rows)
                self._rendered_rows[row.key] = rendered
            start = len(lines) + rendered.anchor
            node_rows[row.key] = range(start, start + rendered.label_rows)
            lines.extend(rendered.lines)
        return lines, node_rows


def execution_dag(graph: ActionGraph, execution_order: list[ActionKey], formatter: ContextFormatter,
                  use_short_ids: bool, shared: dict[ActionKey, int], status: Callable[[ActionKey], Text],
                  edge_style: Callable[[ActionKey], str]) -> DependencyDag:
    positions = {key: index for index, key in enumerate(execution_order)}
    assert len(positions) == len(execution_order), "Plan action keys must be unique"
    edges = [DagEdge(dependency.action, key, dependency) for key in execution_order
             for dependency in graph.get_node(key).dependencies if dependency.action in positions]
    edges.sort(key=lambda edge: (positions[edge.source], positions[edge.target],
                                {"strong": 0, "weak": 1, "soft": 2}[edge.kind],
                                str(edge.dependency.retainer_action)))
    assert all(positions[edge.source] < positions[edge.target] for edge in edges), "Plan order must put prerequisites first"
    outgoing_by_source: dict[ActionKey, list[DagEdge]] = {key: [] for key in execution_order}
    for dependency_edge in edges:
        outgoing_by_source[dependency_edge.source].append(dependency_edge)
    lanes: list[Optional[DagEdge]] = []
    rows: list[DagRow] = []
    lane_count = 1
    for key in execution_order:
        entry = tuple(lanes)
        outgoing = outgoing_by_source[key]
        if len(outgoing) > 1:
            lanes = [edge for edge in lanes if edge is not None]
        incoming = [lane for lane, edge in enumerate(lanes) if edge is not None and edge.target == key]
        lane = min(incoming) if incoming else next((index for index, edge in enumerate(lanes) if edge is None), len(lanes))
        if lane == len(lanes):
            lanes.append(None)
        for slot in range(lane, lane + len(outgoing)):
            if slot == len(lanes):
                lanes.append(None)
            occupant = lanes[slot]
            if occupant is None or occupant.target == key:
                continue
            empty = next((index for index in range(slot + 1, len(lanes)) if lanes[index] is None), len(lanes))
            if empty == len(lanes):
                lanes.append(None)
            for index in range(empty, slot, -1):
                lanes[index] = lanes[index - 1]
            lanes[slot] = None
        before = tuple(lanes)
        for index, edge in enumerate(lanes):
            if edge is not None and edge.target == key:
                lanes[index] = None
        for offset, edge in enumerate(outgoing):
            lanes[lane + offset] = edge
        rows.append(DagRow(key, lane, entry, before, tuple(lanes)))
        lane_count = max(lane_count, len(lanes))
        while lanes and lanes[-1] is None:
            lanes.pop()
    assert not any(lanes), "Plan has unclosed dependency edges"
    crossings = False
    for row in rows:
        incident = [row.lane, *(lane for lane, edge in enumerate(row.before) if edge is not None and edge.target == row.key),
                    *(lane for lane, edge in enumerate(row.after) if edge is not None and edge.source == row.key)]
        crossings |= any(edge is not None and edge.target != row.key and min(incident) < lane < max(incident)
                         for lane, edge in enumerate(row.before))
    return DependencyDag(graph, rows, edges, lane_count, crossings, formatter, use_short_ids, shared, status, edge_style)


def dag_section(dag: DependencyDag, ascii_only: bool) -> Group:
    ready, waiting = (">", "o") if ascii_only else ("◇", "○")
    solid, dashed, crossing = ("|", ":", "x") if ascii_only else ("│", "╎", "╪")
    crossings = f"{crossing} crossing without a join; " if dag.crossings else ""
    return section("Plan:", dag, None,
                   Text(f"{ready} deps ready / {waiting} waiting; {solid} strong / {dashed} weak or soft; "
                        f"{crossings}prerequisites first", style="dim"))
