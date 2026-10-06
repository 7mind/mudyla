"""Compact execution checklist with a streaming fallback for redirected output."""

from pathlib import Path
import json
import time
from typing import TYPE_CHECKING, Literal, Optional, cast
import unicodedata

from rich.align import Align
from rich.console import Group, RenderableType
from rich.segment import Segment, Segments
from rich.syntax import Syntax
from rich.text import Text

from ..dag.graph import ActionGraph, ActionKey
from .formatters import OutputFormatter
from .formatters.details import JsonValue, KeyValueRow, KeyValueView, action_label, context_label, literal_text, metadata_view, output_view
from .formatters.plan import execution_tree, sharing_counts, tree_section
from .formatters.sections import heading, section
from .action_logger_table import ActionLoggerTable, ScrollState, TaskStatus, ViewState
from .formatters.failure import legacy_failure

if TYPE_CHECKING:
    from ..executor.engine import ActionResult

AnsiState = Literal["text", "escape", "csi", "osc", "osc_escape", "string", "string_escape"]
MAX_LOG_CHARS = 4096
TIME_COLUMN_WIDTH = 9


class ActionLoggerPure(ActionLoggerTable):
    """Shared action navigation with a borderless checklist and detail views."""

    CONTENT_HORIZONTAL_PADDING = 0
    WRAP_HIGHLIGHTED_CONTENT = True

    def __init__(self, action_keys: list[ActionKey], output: OutputFormatter, use_short_ids: bool,
                 *, keep_running: bool = False, show_dirs: bool = False,
                 action_dirs: Optional[dict[str, str]] = None, run_directory: Optional[Path] = None,
                 force_interactive: bool = False, run_info: Optional[RenderableType] = None,
                 graph: Optional[ActionGraph] = None) -> None:
        super().__init__(action_keys, no_color=output.no_color, use_short_ids=use_short_ids,
                         keep_running=keep_running, show_dirs=show_dirs, action_dirs=action_dirs,
                         run_directory=run_directory)
        self._output = output
        self._action_formatter = output.action
        if force_interactive:
            self.console.file = output.console.file
        else:
            self.console = output.console
        self._completed = 0
        self._open_line: Optional[tuple[ActionKey, str]] = None
        self._escape_states: dict[tuple[ActionKey, str], AnsiState] = {}
        self._partial_lines: dict[tuple[ActionKey, str], str] = {}
        self._interactive = force_interactive or (self.console.is_terminal and not self.console.is_dumb_terminal)
        self._run_info = run_info
        self._graph = graph
        self._tree_frame_time = time.time()
        self._sharing_counts = sharing_counts(graph, action_keys, [key.id.name for key in graph.goals]) if graph is not None else {}
        self._overview_offset = 0
        self._overview_initialized = False
        self._overview_prefix_length = 0
        self._prefix_cache_key: Optional[tuple[int, str, bool]] = None
        self._prefix_lines: list[list[Segment]] = []
        self._raw_json_views: set[tuple[ActionKey, ViewState]] = set()
        self._static_snapshot_printed = False

    def _text(self, value: str, style: str) -> Text:
        plain = Text.from_ansi(value).plain
        plain = "".join(char for char in plain if char in "\n\t" or unicodedata.category(char) not in {"Cc", "Cs"})
        plain = plain.encode(self.console.encoding, errors="replace").decode(self.console.encoding)
        return Text(plain, style=style)

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
            hints = [f"{ending}  j/k select  Enter logs  e stderr  m meta  o output  s source{input_hint}  Wheel/PgUp/PgDn scroll",
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
        state = self._escape_states.get(owner, "text")
        result = []
        for char in value:
            if state == "text":
                if char == "\x1b":
                    state = "escape"
                elif char == "\x9b":
                    state = "csi"
                elif char == "\x9d":
                    state = "osc"
                elif char in "\x90\x98\x9e\x9f":
                    state = "string"
                else:
                    result.append(char)
            elif state == "escape":
                if char == "[":
                    state = "csi"
                elif char == "]":
                    state = "osc"
                elif char in "PX^_":
                    state = "string"
                elif char != "\x1b" and not " " <= char <= "/":
                    state = "text"
            elif state == "csi":
                if char == "\x1b":
                    state = "escape"
                elif "@" <= char <= "~":
                    state = "text"
            elif state in {"osc", "string"}:
                if char == "\x9c" or (state == "osc" and char == "\x07"):
                    state = "text"
                elif char == "\x1b":
                    state = "osc_escape" if state == "osc" else "string_escape"
            elif char in "\\\x9c" or (state == "osc_escape" and char == "\x07"):
                state = "text"
            elif char != "\x1b":
                state = "osc" if state == "osc_escape" else "string"
        if state == "text":
            self._escape_states.pop(owner, None)
        else:
            self._escape_states[owner] = state
        return "".join(result)

    def show_failure(self, action_key: ActionKey, result: "ActionResult", run_directory: Path, suppress_output: bool) -> None:
        legacy_failure(self._output, result, run_directory, suppress_output, True)

    def start(self) -> None:
        if self._interactive:
            super().start()
        else:
            self.console.print(heading(f"mudyla / {len(self.action_keys)} actions"))

    def uses_terminal_input(self) -> bool:
        return self._interactive and super().uses_terminal_input()

    def _get_terminal_size(self) -> tuple[int, int]:
        return self.console.width, self.console.height

    def _get_content_height(self) -> int:
        height = self.console.height
        if self.state != ViewState.TABLE:
            return max(1, height - (self._input_action is not None) if height <= 5 else height - 3 - (self._detail_toolbar() is not None))
        return max(1, height - (2 if height < 5 else 3))

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
            glyph = self._status_glyph(state.status, now)
            line = Text()
            line.append_text(self._text(f"{'>' if index == self.selected_index else ' '} {glyph} ", self._get_status_style(state.status)))
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
            label = self.STATUS_DISPLAY[state.status][3]
            elapsed = self._format_duration(duration) if duration is not None else "-"
            line.append(f" {elapsed:>{time_width}}: ", style="dim")
            if state.latest:
                line.append_text(self._text(state.latest, "red" if state.stream == "stderr" else ""))
            elif duration is not None and state.status in {TaskStatus.SKIPPED, TaskStatus.CANCELLED, TaskStatus.RESTORED}:
                line.append(label, style=self._get_status_style(state.status))
            line.truncate(max(1, width - 1), overflow=overflow)
            lines.append(line)
        return lines

    def _status_glyph(self, status: TaskStatus, now: float) -> str:
        ascii_only = self.console.options.ascii_only
        glyphs = {TaskStatus.TBD: "o" if ascii_only else "○", TaskStatus.DONE: "+" if ascii_only else "✓",
                  TaskStatus.FAILED: "x" if ascii_only else "✕", TaskStatus.RESTORED: "+" if ascii_only else "↺",
                  TaskStatus.SKIPPED: "-", TaskStatus.CANCELLED: "!"}
        frames = "|/-\\" if ascii_only else "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        return frames[int(now * 8) % len(frames)] if status == TaskStatus.RUNNING else glyphs[status]

    def _tree_status(self, key: ActionKey) -> Text:
        task = self.tasks[key]
        glyph = self._status_glyph(task.status, self._tree_frame_time)
        style = self._get_status_style(task.status)
        if task.status == TaskStatus.TBD:
            assert self._graph is not None
            stopped = self.kill_requested or self.execution_complete or any(
                state.status in {TaskStatus.FAILED, TaskStatus.CANCELLED} for state in self.tasks.values())
            ready = not stopped and all(self.tasks[dep.action].status in {TaskStatus.DONE, TaskStatus.RESTORED}
                                        for dep in self._graph.get_node(key).dependencies)
            if ready:
                glyph = ">" if self.console.options.ascii_only else "◇"
            style = "cyan" if ready else "dim"
        return self._text(f"{glyph} ", style)

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

    def _tree_section(self) -> Group:
        assert self._graph is not None
        self._tree_frame_time = time.time()
        return tree_section(execution_tree(self._graph, self.action_keys, self._output.context, self.use_short_ids,
                                           self._sharing_counts, self._tree_status), self.console.options.ascii_only)

    def _overview_prefix(self) -> list[list[Segment]]:
        width = self.console.width
        cache_key = (width, self.console.encoding, self.console.options.ascii_only)
        if cache_key != self._prefix_cache_key:
            prefix = self._run_info if self._run_info is not None else Group()
            lines = self.console.render_lines(prefix, self.console.options.update(width=width), pad=False)
            self._prefix_lines = [[Segment(self._text(segment.text, "").plain, segment.style, segment.control)
                                   for segment in line] for line in lines]
            self._prefix_cache_key = cache_key
        previous_length = self._overview_prefix_length
        dynamic: list[RenderableType] = []
        if self._graph is not None:
            dynamic.extend([self._tree_section(), Text("")])
        dynamic.append(heading("Actions:"))
        lines = self.console.render_lines(Group(*dynamic), self.console.options.update(width=width), pad=False)
        rendered = self._prefix_lines + [[Segment(self._text(segment.text, "").plain, segment.style, segment.control)
                                          for segment in line] for line in lines]
        self._overview_prefix_length = len(rendered)
        if not self._overview_initialized:
            self._overview_offset = max(len(self._prefix_lines), len(rendered) + min(3, len(self.action_keys)) - self._get_content_height())
            self._overview_initialized = True
        elif self._overview_offset >= previous_length:
            self._overview_offset += len(rendered) - previous_length
        maximum = max(0, self._overview_prefix_length + len(self.action_keys) - self._get_content_height())
        self._overview_offset = max(0, min(maximum, self._overview_offset))
        return rendered

    def _handle_key_table(self, key: str) -> bool:
        with self.lock:
            self._overview_prefix()
            height = self._get_content_height()
            if key in {"top", "bottom", "page_up", "page_down", "half_up", "half_down", "wheel_up", "wheel_down"}:
                maximum = max(0, self._overview_prefix_length + len(self.action_keys) - height)
                if key == "top":
                    self._overview_offset = 0
                elif key == "bottom":
                    self._overview_offset = maximum
                else:
                    amount = self.MOUSE_WHEEL_ROWS if key.startswith("wheel") else max(1, height // 2) if key.startswith("half") else height
                    self._overview_offset += -amount if key.endswith("up") else amount
                    self._overview_offset = max(0, min(maximum, self._overview_offset))
                return False
            result = super()._handle_key_table(key)
            if key in {"up", "down"}:
                row = self._overview_prefix_length + self.selected_index
                self._overview_offset = min(self._overview_offset, row)
                self._overview_offset = max(self._overview_offset, row - height + 1)
            return result

    def _overview_content(self) -> Group:
        prefix = self._overview_prefix()
        options = self.console.options.update(width=self.console.width)
        rows = prefix + [self.console.render_lines(row, options, pad=False)[0] for row in self._action_rows()]
        visible = rows[self._overview_offset:self._overview_offset + self._get_content_height()]
        segments = Segments([segment for row in visible for segment in [*row, Segment.line()]])
        return Group(Align(segments, height=self._get_content_height()))

    def _build_renderable(self) -> Group:
        with self.lock:
            width, height = self._get_terminal_size()
            if self.state == ViewState.TABLE:
                summary, controls = self._checklist_footer()
                if self.stop_flag:
                    actions = section("Actions:", Group(*self._action_rows()), None, summary)
                    return Group(self._tree_section(), Text(""), actions) if self._graph is not None else actions
                content = self._overview_content()
                if height < 5:
                    return Group(content, summary, controls)
                title = self._text(f"mudyla / {len(self.action_keys)} actions", "bold cyan")
                if self.show_dirs:
                    task = self._get_selected_task()
                    if task is not None and task.action_dir is not None:
                        directory = self._text(str(task.action_dir), "")
                        room = max(0, width - title.cell_len - 5)
                        if directory.cell_len > room:
                            while directory.cell_len > max(0, room - 3):
                                directory = directory[1:]
                            directory = Text("...") + directory
                            directory.truncate(room, overflow="crop")
                        title.append(" / ")
                        title.append_text(directory)
                title.truncate(max(0, width - 2), overflow="crop")
                return section(title, content, None, Group(summary, controls))
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
            self._escape_states.clear()
            self._partial_lines.clear()
            if not self._interactive and self._graph is not None and not self._static_snapshot_printed:
                self._static_snapshot_printed = True
                self.console.print(self._build_renderable())

    def _status(self, action_key: ActionKey, status: str, style: str, duration: Optional[float]) -> None:
        with self.lock:
            if duration is not None:
                for stream in ("stdout", "stderr"):
                    self._escape_states.pop((action_key, stream), None)
                    self._partial_lines.pop((action_key, stream), None)
            if self._interactive:
                return
            self._close_output_line()
            label = {"running": "RUN", "done": "DONE", "failed": "FAIL", "restored": "RESTORED"}[status]
            text = self._text(f"  {label:<8} ", style)
            text.append_text(self._label_text(action_key))
            if duration is not None:
                self._completed += 1
                self._escape_states.pop((action_key, "stdout"), None)
                self._escape_states.pop((action_key, "stderr"), None)
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
            if self._interactive:
                parts = decoded.replace("\r", "\n").split("\n")
                parts[0] = self._partial_lines.get(owner, "") + parts[0]
                latest = next((part[-MAX_LOG_CHARS:] for part in reversed(parts) if part), self.tasks[action_key].latest)
                self._partial_lines[owner] = parts[-1][-MAX_LOG_CHARS:]
                self.tasks[action_key].latest = latest
                self.tasks[action_key].stream = stream
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
