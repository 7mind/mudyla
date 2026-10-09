"""Compact execution checklist with a streaming fallback for redirected output."""

from pathlib import Path
import json
import time
from typing import TYPE_CHECKING, Literal, Optional, cast

from rich.align import Align
from rich.console import Group, RenderableType
from rich.segment import Segment, Segments
from rich.syntax import Syntax
from rich.style import Style
from rich.text import Text

from ..dag.graph import ActionKey
from .formatters.details import JsonValue, KeyValueRow, KeyValueView, action_label, context_label, literal_text, metadata_view, output_view
from .formatters.plan import execution_table, execution_tree, sharing_counts, tree_section
from .formatters.dag import dag_section, execution_dag
from .formatters.branches import BranchTheme
from .terminal_background import LIGHT_BACKGROUND_THRESHOLD, luminance
from .formatters.sections import heading, section
from .terminal_logger_table import TableTerminalLogger, ScrollState, TaskStatus, ViewState
from .terminal_logger import LoggerMode
from .formatters.failure import legacy_failure
from .formatters.symbols import StatusSymbol
from .terminal_output import LatestLine

if TYPE_CHECKING:
    from ..executor.engine import ActionResult

TIME_COLUMN_WIDTH = 9
CURSOR_WIDTH = 2


class PureTerminalLogger(TableTerminalLogger):
    """Shared action navigation with a borderless checklist and detail views."""

    OVERVIEW_BOTTOM_ROWS = 0
    CONTENT_HORIZONTAL_PADDING = 0
    WRAP_HIGHLIGHTED_CONTENT = True
    MODE = LoggerMode.PURE

    def _action_order(self) -> list[ActionKey]:
        assert self.execution_order is not None
        if self.plan_style == "dag":
            assert self.layout is not None and self.layout.display is self.display
            return list(self.layout.keys)
        return self.execution_order

    def _initialize_actions(self) -> None:
        assert self.execution_order is not None and self.graph is not None
        super()._initialize_actions()
        self._completed = 0
        self._open_line: Optional[tuple[ActionKey, str]] = None
        self._latest_lines: dict[tuple[ActionKey, str], LatestLine] = {}
        self._interactive = self.force_interactive or (self.console.is_terminal and not self.console.is_dumb_terminal)
        self._graph = self.graph
        self._plan_style = self.plan_style
        self._plan_display = self.display
        self._tree_frame_time = time.time()
        self._sharing_counts = sharing_counts(self.graph, self.execution_order, [key.id.name for key in self.graph.goals])
        self._dag = None
        if self.plan_style == "dag":
            assert self.layout is not None
            self._dag = execution_dag(self.graph, self.execution_order, self.output.context, self.use_short_ids, self._sharing_counts,
                                      self._tree_status, self._plan_edge_style, self._branch_theme, layout=self.layout)
        self._raw_json_views: set[tuple[ActionKey, ViewState]] = set()
        self._static_snapshot_printed = False

    def _text(self, value: str, style: str) -> Text:
        return self._output.display_text(value, style)

    def _label(self, action_key: ActionKey) -> str:
        return self._label_text(action_key).plain

    def _label_text(self, action_key: ActionKey) -> Text:
        label = action_label(action_key, self._output.context, self.use_short_ids, True)
        label.plain = self._display_text(label.plain)
        return label

    def _get_scroll_key(self, action_key: ActionKey, view: ViewState) -> str:
        key = super()._get_scroll_key(action_key, view)
        if view in {ViewState.META, ViewState.OUTPUT}:
            key += ":raw" if (action_key, view) in self._raw_json_views else ":formatted"
        return key

    def _get_scroll_state(self, action_key: ActionKey, view: ViewState) -> ScrollState:
        new = self._get_scroll_key(action_key, view) not in self._scroll_states
        state = super()._get_scroll_state(action_key, view)
        if new and view in {ViewState.META, ViewState.OUTPUT}:
            state.at_end = False
        return state

    def _handle_key_scroll(self, key: str) -> None:
        if key == "v" and self.state in {ViewState.META, ViewState.OUTPUT}:
            action_key = self._get_selected_action_key()
            if action_key is not None:
                view = (action_key, self.state)
                if view in self._raw_json_views:
                    self._raw_json_views.remove(view)
                else:
                    self._raw_json_views.add(view)
            self._pending_g = False
            return
        super()._handle_key_scroll(key)

    def _build_footer(self) -> Text:
        if self._input_action is not None:
            return super()._build_footer()
        input_hint = "  i input" if self._get_input_target() is not None else ""
        if self.state != ViewState.TABLE:
            hints = [f"q back  j/k scroll  PgUp/PgDn page  gg/G top/end{input_hint}  r refresh",
                     f"q back  j/k scroll{input_hint}  r refresh", "q back  j/k scroll", "q back"]
        else:
            ending = "q close" if self.execution_complete else "q kill"
            scroll = "Wheel/PgUp/PgDn" if self.fullscreen else "PgUp/PgDn"
            hints = [f"{ending}  j/k select  Enter logs  e stderr  m meta  o output  s source{input_hint}  {scroll} scroll",
                     f"{ending}  j/k select  Enter logs  e err  m meta  o out  s src{input_hint}",
                     f"{ending}  j/k select  Enter logs{input_hint}", f"{ending}  j/k select", ending]
        footer = self._footer_with_feedback(hints)
        if not self.no_color:
            footer.highlight_regex(r"(?<!\S)(q (?:back|close|kill)|v (?:JSON|formatted)|j/k|Enter|e/m/o/s|e|m|o|s|i|d/u|PgUp/PgDn|Home/End|gg/G|r)(?=\s|$)", "bold cyan")
        return footer

    def _detail_toolbar(self) -> Optional[Text]:
        if self._input_action is not None or self.state not in {ViewState.META, ViewState.OUTPUT}:
            return None
        key = self._get_selected_action_key()
        raw = (key, self.state) in self._raw_json_views
        toolbar = Text("View: " + ("JSON" if raw else "formatted") + "  ", style="dim")
        toolbar.append("v " + ("formatted" if raw else "JSON"), style="bold cyan")
        toolbar.truncate(self.console.width, overflow="crop")
        return toolbar

    def _detail_summary(self) -> Text:
        task = self._get_selected_task()
        if task is None:
            return Text("No action selected", style="dim")
        scroll = self._get_scroll_state(task.action_key, self.state)
        first = scroll.offset + 1 if scroll.total_lines else 0
        last = min(scroll.total_lines, scroll.offset + self._get_content_height())
        live = " live" if scroll.at_end and self.state in {ViewState.LOGS_STDOUT, ViewState.LOGS_STDERR} else ""
        summary = self._text(f"{first}-{last}/{scroll.total_lines}{live} / {self.STATUS_DISPLAY[task.status][3]} / "
                             f"out {self._format_size(task.stdout_size)} / err {self._format_size(task.stderr_size)}", "dim")
        summary.truncate(self.console.width, overflow="crop")
        return summary

    def _build_detail_content(self) -> RenderableType:
        if self.state not in {ViewState.META, ViewState.OUTPUT}:
            return super()._build_detail_content()
        task = self._get_selected_task()
        if task is None:
            return Text("(no action selected)")
        filename = "meta.json" if self.state == ViewState.META else "output.json"
        path = task.action_dir / filename if task.action_dir is not None else None
        raw: Optional[str] = None
        error: Optional[str] = None
        if path is not None and path.exists():
            try:
                raw = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                error = str(exc)
        raw_mode = (task.action_key, self.state) in self._raw_json_views
        if raw_mode:
            text = literal_text(raw if raw is not None else error or f"({filename} not available)")
            text.plain = self._display_text(text.plain)
            if not self.no_color:
                text = Syntax(text.plain, "json", background_color="default").highlight(text.plain)
            return self._render_text_lines(task, list(text.split("\n")), True)
        data: JsonValue = None
        if raw is not None:
            try:
                data = cast(JsonValue, json.loads(raw))
            except (ValueError, RecursionError) as exc:
                error = str(exc)
        if self.state == ViewState.META:
            elapsed = task.duration if task.duration is not None else time.time() - task.start_time if task.start_time is not None else None
            view = metadata_view(data, raw is not None and error is None, self.STATUS_DISPLAY[task.status][3],
                                 self._get_status_style(task.status), elapsed, task.stdout_size, task.stderr_size)
        elif raw is None or error is not None:
            view = KeyValueView([KeyValueRow(Text("Outputs", style="dim"), Text("not available", style="dim"), 0)])
        else:
            view = output_view(data)
        if error is not None:
            view.rows.append(KeyValueRow(Text("Read error", style="red"), literal_text(error), 0))
        lines, anchors = view.visual_lines(self.console, self.console.width)
        return self._render_visual_lines(task, [(None, line) for line in lines], anchors, 0, False)

    def _stream_text(self, owner: tuple[ActionKey, str], value: str) -> str:
        return self._latest_lines.setdefault(owner, LatestLine()).plain(value)

    def show_failure(self, action_key: ActionKey, result: "ActionResult", run_directory: Path, suppress_output: bool) -> None:
        legacy_failure(self._output, result, run_directory, suppress_output, True)

    def start(self) -> None:
        if self._interactive:
            super().start()

    def uses_terminal_input(self) -> bool:
        return self._interactive and super().uses_terminal_input()

    def _get_terminal_size(self) -> tuple[int, int]:
        return self.console.width, self.console.height

    def _overview_directory(self) -> Optional[Path]:
        task = self._get_selected_task()
        return task.action_dir if self.show_dirs and task is not None else None

    def _get_content_height(self) -> int:
        height = self.console.height
        if self.state != ViewState.TABLE:
            return max(1, height - (self._input_action is not None) if height <= 5 else height - 3 - (self._detail_toolbar() is not None))
        return max(1, height - 2 - int(height >= 5 and self._overview_directory() is not None))

    def _action_rows(self) -> list[Text]:
        actions = [(key, self.tasks[key]) for key in self.action_keys]
        width, _ = self._get_terminal_size()
        time_width = min(TIME_COLUMN_WIDTH, max(5, width // 6))
        label_space = max(4, width - 4 - time_width - 4 - min(12, width // 5))
        name_width = min(max((self._text(key.id.name, "").cell_len for key in self.action_keys), default=0),
                         max(min(6, width // 3), label_space // 2))
        contexts = {key: context_label(key.context_id, self._output.context, self.use_short_ids) for key in self.action_keys}
        context_width = min(max((label.cell_len for label in contexts.values()), default=0), max(0, label_space - name_width))
        ascii_only = self.console.options.ascii_only
        overflow: Literal["crop", "ellipsis"] = "crop" if ascii_only else "ellipsis"
        lines = []
        now = time.time()
        for index, (key, state) in enumerate(actions):
            line = Text()
            line.append_text(self._text(f"{'>' if index == self.selected_index else ' '} ", self._get_status_style(state.status)))
            line.append_text(self._status_marker(key, now))
            name = self._text(key.id.name, "bold")
            name.truncate(name_width, overflow=overflow)
            name.pad_right(name_width - name.cell_len)
            line.append_text(name)
            if context_width:
                identity = self._text(contexts[key].plain, str(contexts[key].style))
                identity.stylize("dim not bold")
                identity.truncate(context_width, overflow="crop" if context_width < 4 else overflow)
                identity.pad_right(context_width - identity.cell_len)
                line.append(" ")
                line.append_text(identity)
            duration = state.duration if state.duration is not None else now - state.start_time if state.start_time is not None else None
            elapsed = self._format_duration(duration) if duration is not None else "-"
            line.append(f" {elapsed:>{time_width}}: ", style="dim")
            line.append_text(self._output.latest_message(state.latest, state.stream))
            line.truncate(max(1, width - 1), overflow=overflow)
            lines.append(line)
        return lines

    def _status_marker(self, key: ActionKey, now: float) -> Text:
        status = self.tasks[key].status
        style = self._get_status_style(status)
        symbol = {TaskStatus.TBD: StatusSymbol.READY, TaskStatus.RUNNING: StatusSymbol.RUNNING,
                  TaskStatus.DONE: StatusSymbol.DONE, TaskStatus.FAILED: StatusSymbol.FAILED,
                  TaskStatus.RESTORED: StatusSymbol.RESTORED, TaskStatus.SKIPPED: StatusSymbol.SKIPPED,
                  TaskStatus.CANCELLED: StatusSymbol.CANCELLED}[status]
        if status == TaskStatus.RUNNING:
            style = "yellow"
        if status == TaskStatus.TBD and self._graph is not None:
            ready = self._dependencies_ready(key)
            if not ready:
                symbol = StatusSymbol.WAITING
            style = "cyan" if ready else "dim"
        glyph = self._output.symbols.status(symbol, now=now)
        return self._text(f"{glyph} ", style)

    def _action_lines(self) -> tuple[list[list[Segment]], dict[ActionKey, int]]:
        options = self.console.options.update(width=self.console.width)
        if self._dag is None:
            lines = [self.console.render_lines(row, options, pad=False)[0] for row in self._action_rows()]
            selected_rows = range(self.selected_index, self.selected_index + 1) if lines else range(0)
            self._highlight_rows(lines, selected_rows)
            return lines, {key: index for index, key in enumerate(self.action_keys)}
        self._tree_frame_time = time.time()
        parts = {key: self._dag.label_parts(key) for key in self.action_keys}
        name_width = max((name.cell_len for name, _ in parts.values()), default=0)
        context_width = max((annotation.cell_len for _, annotation in parts.values()), default=0)
        selected = self._get_selected_action_key()
        durations = {}
        for key in self.action_keys:
            task = self.tasks[key]
            elapsed = (task.duration if task.duration is not None else
                       self._tree_frame_time - task.start_time if task.start_time is not None else None)
            durations[key] = self._format_duration(elapsed) if elapsed is not None else "-"
        time_width = max((len(value) for value in durations.values()), default=1)

        def node_label(key: ActionKey, width: int) -> Text:
            name, annotation = (part.copy() for part in parts[key])
            if name_width + context_width + time_width + 4 <= width:
                name.pad_right(name_width - name.cell_len)
                annotation.pad_right(context_width - annotation.cell_len)
            label = Text()
            label.append_text(name)
            label.append(" ")
            label.append_text(annotation)
            label.append(f" {durations[key]:>{time_width}}:", style="dim not bold")
            available = width - label.cell_len - 1
            task = self.tasks[key]
            if available > 0:
                preview = self._output.latest_message(task.latest, task.stream)
                preview.stylize("not bold")
                preview.truncate(available, overflow="crop" if options.ascii_only else "ellipsis")
                label.append(" ")
                label.append_text(preview)
            return label

        lines, node_rows = self._dag.visual_lines(self.console, options.update(width=max(1, options.max_width - CURSOR_WIDTH)), node_label)
        anchors = {key: rows.start for key, rows in node_rows.items()}
        cursor = anchors.get(selected) if selected is not None else None
        selected_rows = node_rows[selected] if selected is not None else range(0)
        rendered = [[Segment("> " if index == cursor else " " * CURSOR_WIDTH, Style(dim=True)),
                     *(Segment(segment.text.encode(self.console.encoding, errors="replace").decode(self.console.encoding), segment.style, segment.control) for segment in line)]
                    for index, line in enumerate(lines)]
        self._highlight_rows(rendered, selected_rows)
        return rendered, anchors

    def _highlight_rows(self, lines: list[list[Segment]], selected_rows: range) -> None:
        selection_style = self._selection_style()
        if selection_style is not None:
            for index in selected_rows:
                styled = [Segment(segment.text, (segment.style or Style()) + selection_style, segment.control)
                          for segment in lines[index]]
                lines[index] = Segment.adjust_line_length(styled, self.console.width, style=selection_style)

    def _tree_status(self, key: ActionKey) -> Text:
        return self._status_marker(key, self._tree_frame_time)

    def _checklist_footer(self) -> tuple[Text, Text]:
        actions = list(self.tasks.items())
        width, _ = self._get_terminal_size()
        overflow: Literal["crop", "ellipsis"] = "crop" if self.console.options.ascii_only else "ellipsis"
        counts = {status: sum(state.status == status for _, state in actions) for status in TaskStatus}
        total = "  ".join(f"{count} {self.STATUS_DISPLAY[status][3]}" for status, count in counts.items() if count)
        summary = self._text(total, "dim")
        summary.truncate(max(1, width - 1), overflow=overflow)
        footer = self._text("Logs: --keep-run-dir", "dim") if self.stop_flag else self._build_footer()
        footer.truncate(max(1, width - 1), overflow=overflow)
        return summary, footer

    def _render_checklist(self) -> Group:
        start, end = self._table_window()
        return Group(*self._action_rows()[start:end], *self._checklist_footer())

    def _dependencies_ready(self, key: ActionKey) -> bool:
        assert self._graph is not None
        stopped = self.kill_requested or self.execution_complete or any(
            state.status in {TaskStatus.FAILED, TaskStatus.CANCELLED} for state in self.tasks.values())
        return not stopped and all(self.tasks[dep.action].status in {TaskStatus.DONE, TaskStatus.RESTORED}
                                   for dep in self._graph.get_node(key).dependencies)

    def _branch_theme(self) -> BranchTheme:
        if self._output.no_color or self.console.no_color or self.console.color_system is None:
            return BranchTheme.DISABLED
        if self._background_probe is None or self._background_probe.background is None or self.console.color_system == "standard":
            return BranchTheme.TERMINAL
        return (BranchTheme.LIGHT if luminance(self._background_probe.background) >= LIGHT_BACKGROUND_THRESHOLD
                else BranchTheme.DARK)

    def _plan_edge_style(self, key: ActionKey) -> str:
        status = self.tasks[key].status
        active = (self._dependencies_ready(key) if status == TaskStatus.TBD
                  else status != TaskStatus.SKIPPED)
        return "not dim" if active else "dim"

    def _plan_section(self) -> Group:
        assert self._graph is not None
        self._tree_frame_time = time.time()
        if self._plan_style == "dag":
            assert self._dag is not None
            return dag_section(self._dag, self._output.symbols, toolbar=self.planning_summary)
        if self._plan_style == "table":
            return section("Plan:", execution_table(self._graph, self.action_keys, self._output.context,
                           self.use_short_ids, self._sharing_counts, self.console.options.ascii_only), None, None)
        assert self._plan_style == "tree"
        assert self._plan_display is not None
        return tree_section(execution_tree(self._graph, self.action_keys, self._output.context, self.use_short_ids,
                                          self._sharing_counts, self._tree_status, display=self._plan_display), self._output.symbols,
                            toolbar=self.planning_summary)

    def _preparation_renderable(self) -> RenderableType:
        prefix = super()._preparation_renderable() if self.fullscreen else Group()
        return Group(prefix, self._plan_section(), Text("")) if self._graph is not None and self._plan_style == "table" else prefix

    def _overview_is_scrollable(self) -> bool:
        return True

    def _overview_prefix(self) -> list[list[Segment]]:
        width = self.console.width
        prefix = self._cached_preparation()
        dynamic: list[RenderableType] = []
        if self._graph is not None and self._plan_style == "tree":
            dynamic.extend([self._plan_section(), Text("")])
        dynamic.append(heading("Actions:"))
        if self.planning_summary is not None:
            dynamic.append(self.planning_summary)
        lines = self.console.render_lines(Group(*dynamic), self.console.options.update(width=width), pad=False)
        rendered = prefix + [[Segment(segment.text.encode(self.console.encoding, errors="replace").decode(self.console.encoding), segment.style, segment.control)
                                          for segment in line] for line in lines]
        self._overview_prefix_length = len(rendered)
        return rendered

    def _overview_rows(self) -> list[list[Segment]]:
        previous_length = self._overview_prefix_length
        selected = self._get_selected_action_key()
        previous_row = previous_length + self._action_anchors[selected] if selected in self._action_anchors else None
        height = self._get_content_height()
        relative = previous_row - self._overview_offset if previous_row is not None else None
        prefix = self._overview_prefix()
        actions, anchors = self._action_lines()
        self._action_anchors = anchors
        if not self._overview_initialized:
            if self._dag is not None and selected is not None:
                self._overview_offset = max(0, len(prefix) + anchors[selected] - height + 1)
            else:
                self._overview_offset = max(len(self._prefix_lines), len(prefix) + min(3, len(actions)) - height)
            self._overview_initialized = True
        elif selected is not None and relative is not None and 0 <= relative < self._overview_height:
            self._overview_offset = len(prefix) + anchors[selected] - min(relative, height - 1)
        elif self._overview_offset >= previous_length:
            self._overview_offset += len(prefix) - previous_length
        maximum = max(0, len(prefix) + len(actions) - height)
        self._overview_offset = max(0, min(maximum, self._overview_offset))
        self._overview_height = height
        return prefix + actions

    def _build_renderable(self) -> Group:
        with self.lock:
            width, height = self._get_terminal_size()
            if self.state == ViewState.TABLE:
                summary, controls = self._checklist_footer()
                if self.stop_flag:
                    if self._dag is not None:
                        rows, _ = self._action_lines()
                        dag_actions = Segments([segment for row in rows for segment in [*row, Segment.line()]])
                        return section("Actions:", dag_actions, self.planning_summary, summary)
                    actions = section("Actions:", Group(*self._action_rows()), self.planning_summary, summary)
                    return Group(self._plan_section(), Text(""), actions) if self._graph is not None else actions
                content = self._overview_content()
                directory_path = self._overview_directory()
                if height < 5 or directory_path is None:
                    return Group(content, summary, controls)
                directory = self._text(str(directory_path), "")
                room = max(0, width - 2)
                if directory.cell_len > room:
                    while directory.cell_len > max(0, room - 3):
                        directory = directory[1:]
                    directory = Text("...") + directory
                    directory.truncate(room, overflow="crop")
                return section(directory, content, None, Group(summary, controls))
            detail = Align(self._build_detail_content(), height=self._get_content_height(), vertical="top")
            if height <= 5:
                return Group(detail, self._build_footer()) if self._input_action is not None else Group(detail)
            task = self._get_selected_task()
            title = Text({ViewState.META: "Metadata", ViewState.OUTPUT: "Outputs"}.get(self.state, self._build_header().split(" - ", 1)[0]) + " / ")
            if task is not None:
                title.append_text(self._label_text(task.action_key))
            title.truncate(width, overflow="crop")
            return section(title, detail, self._detail_toolbar(), Group(self._detail_summary(), self._build_footer()))

    def _close_output_line(self) -> None:
        if self._open_line is not None:
            self.console.print()
            self._open_line = None

    def stop(self) -> None:
        if self._interactive:
            super().stop()
        with self.lock:
            self.stop_flag = True
            self.mark_execution_complete()
            self._close_output_line()
            self._latest_lines.clear()
            if not self._interactive and self._graph is not None and not self._static_snapshot_printed:
                self._static_snapshot_printed = True
                self.console.print(self._build_renderable())

    def _status(self, action_key: ActionKey, status: str, style: str, duration: Optional[float]) -> None:
        with self.lock:
            if duration is not None:
                for stream in ("stdout", "stderr"):
                    self._latest_lines.pop((action_key, stream), None)
            if self._interactive:
                return
            self._close_output_line()
            label = {"running": "RUN", "done": "DONE", "failed": "FAIL", "restored": "RESTORED"}[status]
            text = self._text(f"  {label:<8} ", style)
            text.append_text(self._label_text(action_key))
            if duration is not None:
                self._completed += 1
                self._latest_lines.pop((action_key, "stdout"), None)
                self._latest_lines.pop((action_key, "stderr"), None)
                text.append(f"  {self._format_duration(duration)}  [{self._completed}/{len(self.action_keys)}]", style="dim")
            self.console.print(text)

    def mark_running(self, action_key: ActionKey, action_dir: Optional[Path] = None) -> None:
        super().mark_running(action_key, action_dir)
        self._status(action_key, "running", "cyan", None)

    def mark_done(self, action_key: ActionKey, duration: float) -> None:
        super().mark_done(action_key, duration)
        self._status(action_key, "done", "green", duration)

    def mark_failed(self, action_key: ActionKey, duration: float) -> None:
        super().mark_failed(action_key, duration)
        self._status(action_key, "failed", "bold red", duration)

    def mark_restored(self, action_key: ActionKey, duration: float, action_dir: Optional[Path] = None) -> None:
        super().mark_restored(action_key, duration, action_dir)
        self._status(action_key, "restored", "green", duration)

    def write_output(self, action_key: ActionKey, text: str, stream: Literal["stdout", "stderr"]) -> None:
        with self.lock:
            owner = (action_key, stream)
            decoded = self._stream_text(owner, text)
            latest = self._latest_lines[owner].consume(decoded)
            if latest is not None:
                self.tasks[action_key].latest = latest
            self.tasks[action_key].stream = stream
            if self._interactive:
                return
            for part in decoded.splitlines(keepends=True):
                complete = part.endswith("\n")
                if self._open_line != owner:
                    self._close_output_line()
                    line = Text()
                    line.append_text(self._text(f"    {self._label(action_key)} / {stream}  ", "dim"))
                else:
                    line = Text()
                line.append_text(self._text(part.rstrip("\r\n"), "red" if stream == "stderr" else ""))
                self.console.print(line, end="\n" if complete else "", soft_wrap=True)
                self._open_line = None if complete else owner
