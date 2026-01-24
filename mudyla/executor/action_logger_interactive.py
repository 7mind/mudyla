"""Interactive action logger with Rich table display for execution progress.

State machine with views:
- TABLE: Main task list with status (navigable with ↑/↓)
- META: Action metadata (scrollable)
- LOGS_STDOUT: Action stdout logs (scrollable, auto-scroll at end)
- LOGS_STDERR: Action stderr logs (scrollable, auto-scroll at end)
- OUTPUT: Action output.json (scrollable)
- SOURCE: Action script source (scrollable)

All views share a common layout: Header | Content | Footer
"""

import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Optional

from rich.console import Console, Group
from rich.live import Live
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from ..dag.graph import ActionKey
from ..formatters import OutputFormatter
from .action_logger import ActionLogger

# Cross-platform terminal handling
IS_WINDOWS = sys.platform == "win32"

# Selection indicator - Windows console encoding doesn't support Unicode triangles
SELECTION_INDICATOR = ">" if IS_WINDOWS else "▶"

if IS_WINDOWS:
    import msvcrt
else:
    import fcntl
    import select
    import termios
    import tty


class TaskStatus(Enum):
    """Task execution status."""
    TBD = "tbd"
    RUNNING = "running"
    DONE = "done"
    RESTORED = "restored"
    FAILED = "failed"


class ViewState(Enum):
    """State machine states for the interactive viewer."""
    TABLE = auto()
    META = auto()
    LOGS_STDOUT = auto()
    LOGS_STDERR = auto()
    OUTPUT = auto()
    SOURCE = auto()


@dataclass
class ScrollState:
    """Scroll state for a scrollable view."""
    offset: int = 0
    total_lines: int = 0
    at_end: bool = True  # Auto-scroll when at end


@dataclass
class TaskState:
    """State for a single task."""
    action_key: ActionKey
    status: TaskStatus = TaskStatus.TBD
    start_time: Optional[float] = None
    duration: Optional[float] = None
    stdout_size: int = 0
    stderr_size: int = 0
    action_dir: Optional[Path] = None


class ActionLoggerInteractive(ActionLogger):
    """State machine-based task table with interactive navigation.

    Implements ActionLogger interface for interactive Rich table display.
    Uses Rich Live display for flicker-free rendering with:
    - Header: View name / status summary
    - Content: Table or scrollable text
    - Footer: Key bindings for current state
    """

    # Key bindings per state
    TABLE_KEYS = "j/k/Arrows navigate | Enter/l stdout | e stderr | m meta | o output | s source | q kill"
    SCROLL_KEYS = "j/k/Arrows scroll | d/u half | PgUp/PgDn/f/b page | gg/Home top | G/End bottom | q back"
    LOG_KEYS = "j/k/Arrows | d/u half | PgUp/PgDn page | gg/G top/bottom | r refresh | q back"

    def __init__(
        self,
        action_keys: list[ActionKey],
        no_color: bool = False,
        action_dirs: Optional[dict[str, str]] = None,
        show_dirs: bool = False,
        run_directory: Optional[Path] = None,
        keep_running: bool = False,
        use_short_ids: bool = True,
    ):
        self.no_color = no_color
        self.show_dirs = show_dirs
        self.run_directory = run_directory
        self.keep_running = keep_running
        self.action_dirs_map = action_dirs or {}
        self.use_short_ids = use_short_ids

        # Formatters - use OutputFormatter which creates all sub-formatters
        self._output = OutputFormatter(no_color=no_color)
        self._action_formatter = self._output.action
        self._context_formatter = self._output.context

        # Store action keys - these are the canonical identifiers
        self.action_keys: list[ActionKey] = list(action_keys)

        # Console for rendering - respect no_color setting
        self.console = Console(force_terminal=True, no_color=no_color)

        # Shared state - keyed by ActionKey, formatting done at display time
        self.tasks: dict[ActionKey, TaskState] = {
            key: TaskState(action_key=key) for key in action_keys
        }

        # View state
        self.state = ViewState.TABLE
        self.selected_index = 0
        self.execution_complete = False

        # Scroll states per view (keyed by ActionKey string + view)
        self._scroll_states: dict[str, ScrollState] = {}

        # Vim-like 'gg' sequence tracking
        self._pending_g: bool = False

        # Terminal state
        self._old_terminal_settings: Optional[list[Any]] = None

        # Threading and Live display
        self.lock = threading.RLock()
        self.stop_flag = False
        self.kill_requested = False  # Flag for engine to check
        self._kill_callback: Optional[Callable[[], None]] = None
        self.live: Optional[Live] = None
        self._main_thread: Optional[threading.Thread] = None

    # =========================================================================
    # ActionLogger Interface Implementation
    # =========================================================================

    def mark_running(self, action_key: ActionKey, action_dir: Optional[Path] = None) -> None:
        """Mark a task as running."""
        with self.lock:
            if action_key in self.tasks:
                self.tasks[action_key].status = TaskStatus.RUNNING
                self.tasks[action_key].start_time = time.time()
                if action_dir:
                    self.tasks[action_key].action_dir = action_dir

    def mark_done(self, action_key: ActionKey, duration: float) -> None:
        """Mark a task as done."""
        with self.lock:
            if action_key in self.tasks:
                self.tasks[action_key].status = TaskStatus.DONE
                self.tasks[action_key].duration = duration

    def mark_failed(self, action_key: ActionKey, duration: float) -> None:
        """Mark a task as failed."""
        with self.lock:
            if action_key in self.tasks:
                self.tasks[action_key].status = TaskStatus.FAILED
                self.tasks[action_key].duration = duration

    def mark_restored(self, action_key: ActionKey, duration: float, action_dir: Optional[Path] = None) -> None:
        """Mark a task as restored from previous run."""
        with self.lock:
            if action_key in self.tasks:
                self.tasks[action_key].status = TaskStatus.RESTORED
                self.tasks[action_key].duration = duration
                if action_dir:
                    self.tasks[action_key].action_dir = action_dir

    def mark_execution_complete(self) -> None:
        """Mark execution as complete."""
        with self.lock:
            self.execution_complete = True

    def update_output_sizes(self, action_key: ActionKey, stdout_size: int, stderr_size: int) -> None:
        """Update stdout and stderr sizes for a task."""
        with self.lock:
            if action_key in self.tasks:
                self.tasks[action_key].stdout_size = stdout_size
                self.tasks[action_key].stderr_size = stderr_size

    def set_kill_callback(self, callback: Callable[[], None]) -> None:
        """Set callback to be called when user requests kill (q key)."""
        self._kill_callback = callback

    def is_kill_requested(self) -> bool:
        """Check if user has requested to kill execution."""
        return self.kill_requested

    # =========================================================================
    # Scroll State Management
    # =========================================================================

    def _get_scroll_key(self, action_key: ActionKey, view: ViewState) -> str:
        """Get unique key for scroll state."""
        return f"{action_key}:{view.name}"

    def _get_scroll_state(self, action_key: ActionKey, view: ViewState) -> ScrollState:
        """Get or create scroll state for a view."""
        key = self._get_scroll_key(action_key, view)
        if key not in self._scroll_states:
            self._scroll_states[key] = ScrollState()
        return self._scroll_states[key]

    def _update_scroll_state(
        self,
        action_key: ActionKey,
        view: ViewState,
        total_lines: int,
        visible_height: int
    ) -> ScrollState:
        """Update scroll state with new content info, handling auto-scroll."""
        state = self._get_scroll_state(action_key, view)
        prev_total = state.total_lines
        state.total_lines = total_lines

        max_offset = max(0, total_lines - visible_height)

        # Auto-scroll if at end and new content added
        if state.at_end and total_lines > prev_total:
            state.offset = max_offset

        # Clamp offset
        state.offset = max(0, min(state.offset, max_offset))

        # Update at_end flag
        state.at_end = state.offset >= max_offset

        return state

    # =========================================================================
    # Terminal Management (Cross-platform)
    # =========================================================================

    def _setup_terminal(self) -> None:
        """Set up terminal for raw input (cross-platform)."""
        if IS_WINDOWS:
            self._old_terminal_settings = None
        else:
            try:
                self._old_terminal_settings = termios.tcgetattr(sys.stdin)
                tty.setcbreak(sys.stdin.fileno())
            except (termios.error, AttributeError, ValueError):
                self._old_terminal_settings = None

    def _restore_terminal(self) -> None:
        """Restore terminal settings (cross-platform)."""
        if not IS_WINDOWS and self._old_terminal_settings:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._old_terminal_settings)
            except (termios.error, ValueError):
                pass

    def _get_terminal_size(self) -> tuple[int, int]:
        """Get terminal width and height."""
        try:
            size = os.get_terminal_size()
            return (size.columns, size.lines)
        except OSError:
            return (80, 24)

    def _get_content_height(self) -> int:
        """Get height available for content (minus header/footer)."""
        _, height = self._get_terminal_size()
        return max(5, height - 6)

    # =========================================================================
    # Key Input (Cross-platform)
    # =========================================================================

    def _read_key_windows(self) -> str:
        """Read a single key press on Windows (non-blocking)."""
        if not msvcrt.kbhit():
            return ""

        ch = msvcrt.getch()

        if ch in (b'\x00', b'\xe0'):
            if msvcrt.kbhit():
                ch2 = msvcrt.getch()
                if ch2 == b'H':
                    return "up"
                elif ch2 == b'P':
                    return "down"
                elif ch2 == b'I':
                    return "page_up"
                elif ch2 == b'Q':
                    return "page_down"
                elif ch2 == b'G':
                    return "top"
                elif ch2 == b'O':
                    return "bottom"
                elif ch2 == b'\x8d':
                    return "top"
                elif ch2 == b'\x91':
                    return "bottom"
            return ""

        try:
            char = ch.decode('utf-8', errors='ignore')
        except Exception:
            return ""

        key_map = {
            "\r": "enter", "\n": "enter",
            "q": "q", "Q": "q",
            "m": "m", "M": "m",
            "l": "l", "L": "l",
            "e": "e", "E": "e",
            "o": "o", "O": "o",
            "s": "s", "S": "s",
            "r": "r", "R": "r",
            "j": "down", "J": "down",
            "k": "up", "K": "up",
            "g": "g",
            "G": "G",
            "d": "half_down", "\x04": "half_down",
            "u": "half_up", "\x15": "half_up",
            "f": "page_down", "\x06": "page_down",
            "b": "page_up", "\x02": "page_up",
        }
        return key_map.get(char, "")

    def _read_key_unix(self) -> str:
        """Read a single key press on Unix (non-blocking with short timeout)."""
        try:
            ready, _, _ = select.select([sys.stdin], [], [], 0.02)
            if not ready:
                return ""

            fd = sys.stdin.fileno()
            ch = os.read(fd, 1).decode('utf-8', errors='ignore')
            if not ch:
                return ""

            if ch == "\x1b":
                old_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, old_flags | os.O_NONBLOCK)

                try:
                    time.sleep(0.02)
                    seq = b""
                    try:
                        seq = os.read(fd, 5)
                    except (OSError, BlockingIOError):
                        pass

                    seq_str = seq.decode('utf-8', errors='ignore')

                    if seq_str.startswith("[1;2A"):
                        return "top"
                    elif seq_str.startswith("[1;2B"):
                        return "bottom"
                    elif seq_str.startswith("[A") or seq_str == "OA":
                        return "up"
                    elif seq_str.startswith("[B") or seq_str == "OB":
                        return "down"
                    elif seq_str.startswith("[C") or seq_str == "OC":
                        return "right"
                    elif seq_str.startswith("[D") or seq_str == "OD":
                        return "left"
                    elif seq_str.startswith("[5~"):
                        return "page_up"
                    elif seq_str.startswith("[6~"):
                        return "page_down"
                    elif seq_str.startswith("[H") or seq_str.startswith("[1~") or seq_str == "OH":
                        return "top"
                    elif seq_str.startswith("[F") or seq_str.startswith("[4~") or seq_str == "OF":
                        return "bottom"
                    elif "A" in seq_str and "[" in seq_str:
                        return "up"
                    elif "B" in seq_str and "[" in seq_str:
                        return "down"
                finally:
                    fcntl.fcntl(fd, fcntl.F_SETFL, old_flags)

                return ""

            key_map = {
                "\r": "enter", "\n": "enter",
                "q": "q", "Q": "q",
                "m": "m", "M": "m",
                "l": "l", "L": "l",
                "e": "e", "E": "e",
                "o": "o", "O": "o",
                "s": "s", "S": "s",
                "r": "r", "R": "r",
                "j": "down", "J": "down",
                "k": "up", "K": "up",
                "g": "g",
                "G": "G",
                "d": "half_down", "\x04": "half_down",
                "u": "half_up", "\x15": "half_up",
                "f": "page_down", "\x06": "page_down",
                "b": "page_up", "\x02": "page_up",
            }
            return key_map.get(ch, "")
        except Exception:
            return ""

    def _read_key(self) -> str:
        """Read a single key press (cross-platform, non-blocking)."""
        if IS_WINDOWS:
            return self._read_key_windows()
        else:
            return self._read_key_unix()

    # =========================================================================
    # Key Handlers
    # =========================================================================

    def _handle_key_table(self, key: str) -> bool:
        """Handle key in TABLE state. Returns True if should exit/kill."""
        with self.lock:
            if key == "up":
                self.selected_index = max(0, self.selected_index - 1)
            elif key == "down":
                self.selected_index = min(len(self.action_keys) - 1, self.selected_index + 1)
            elif key == "q":
                self.kill_requested = True
                if self._kill_callback:
                    try:
                        self._kill_callback()
                    except Exception:
                        pass
                return True
            elif key == "m":
                self.state = ViewState.META
            elif key in ("l", "enter"):
                self.state = ViewState.LOGS_STDOUT
            elif key == "e":
                self.state = ViewState.LOGS_STDERR
            elif key == "o":
                self.state = ViewState.OUTPUT
            elif key == "s":
                self.state = ViewState.SOURCE
        return False

    def _handle_key_scroll(self, key: str) -> None:
        """Handle key in scrollable views with vim-like navigation."""
        with self.lock:
            action_key = self._get_selected_action_key()
            if not action_key:
                self._pending_g = False
                return

            if key == "q":
                self.state = ViewState.TABLE
                self._pending_g = False
                return

            scroll_state = self._get_scroll_state(action_key, self.state)
            visible_height = self._get_content_height()
            max_offset = max(0, scroll_state.total_lines - visible_height)
            half_page = max(1, visible_height // 2)

            if key == "g":
                if self._pending_g:
                    scroll_state.offset = 0
                    scroll_state.at_end = max_offset == 0
                    self._pending_g = False
                else:
                    self._pending_g = True
                return
            else:
                self._pending_g = False

            if key == "up":
                scroll_state.offset = max(0, scroll_state.offset - 1)
                scroll_state.at_end = False
            elif key == "down":
                scroll_state.offset = min(max_offset, scroll_state.offset + 1)
                scroll_state.at_end = scroll_state.offset >= max_offset
            elif key == "top":
                scroll_state.offset = 0
                scroll_state.at_end = max_offset == 0
            elif key in ("bottom", "G"):
                scroll_state.offset = max_offset
                scroll_state.at_end = True
            elif key == "half_down":
                scroll_state.offset = min(max_offset, scroll_state.offset + half_page)
                scroll_state.at_end = scroll_state.offset >= max_offset
            elif key == "half_up":
                scroll_state.offset = max(0, scroll_state.offset - half_page)
                scroll_state.at_end = False
            elif key == "page_down":
                scroll_state.offset = min(max_offset, scroll_state.offset + visible_height)
                scroll_state.at_end = scroll_state.offset >= max_offset
            elif key == "page_up":
                scroll_state.offset = max(0, scroll_state.offset - visible_height)
                scroll_state.at_end = False

    # =========================================================================
    # Formatting Helpers
    # =========================================================================

    def _format_duration(self, seconds: float) -> str:
        """Format duration for display."""
        if seconds < 60.0:
            return f"{seconds:.1f}s"
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}m {secs:.0f}s"

    def _format_size(self, size_bytes: int) -> str:
        """Format size for display."""
        if size_bytes == 0:
            return "-"
        elif size_bytes < 1024:
            return f"{size_bytes}B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f}K"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f}M"
        return f"{size_bytes / (1024 * 1024 * 1024):.1f}G"

    def _get_status_style(self, status: TaskStatus) -> str:
        """Get the rich style for a status."""
        if self.no_color:
            return ""
        return {
            TaskStatus.TBD: "dim",
            TaskStatus.RUNNING: "cyan",
            TaskStatus.DONE: "green",
            TaskStatus.RESTORED: "green",
            TaskStatus.FAILED: "red",
        }[status]

    def _get_selected_action_key(self) -> Optional[ActionKey]:
        """Get currently selected action key."""
        with self.lock:
            if 0 <= self.selected_index < len(self.action_keys):
                return self.action_keys[self.selected_index]
            return None

    def _get_selected_task(self) -> Optional[TaskState]:
        """Get currently selected task."""
        action_key = self._get_selected_action_key()
        if action_key is None:
            return None
        return self.tasks.get(action_key)

    # =========================================================================
    # View Renderers
    # =========================================================================

    # Status display configuration: (symbol_ascii, symbol_unicode, color, label)
    STATUS_DISPLAY = {
        TaskStatus.TBD: (".", "░", "dim", "pending"),
        TaskStatus.RUNNING: ("~", "▒", "cyan", "running"),
        TaskStatus.DONE: ("#", "█", "green", "done"),
        TaskStatus.RESTORED: ("+", "▓", "blue", "restored"),
        TaskStatus.FAILED: ("!", "█", "red", "failed"),
    }

    def _build_table(self, include_progress: bool = False) -> Table:
        """Build the task table."""
        with self.lock:
            has_context = any(str(key.context_id) != "default" for key in self.action_keys)

            caption = None
            if include_progress and not self.no_color:
                caption = self._build_progress_caption()

            header_style = "" if self.no_color else "bold"
            action_style = "" if self.no_color else "cyan bold"
            dim_style = "" if self.no_color else "dim"

            table = Table(show_header=True, header_style=header_style, caption=caption, caption_justify="left")

            table.add_column("", width=1, no_wrap=True)

            if has_context:
                table.add_column("Context", no_wrap=True)
                table.add_column("Action", style=action_style, no_wrap=True)
            else:
                table.add_column("Task", style=action_style, no_wrap=True)

            if self.show_dirs:
                table.add_column("Dir", style=dim_style, no_wrap=True)
            table.add_column("Time", justify="right", no_wrap=True)
            table.add_column("Stdout", justify="right", no_wrap=True)
            table.add_column("Stderr", justify="right", no_wrap=True)
            table.add_column("Status", justify="center", no_wrap=True)

            for idx, action_key in enumerate(self.action_keys):
                task = self.tasks[action_key]
                status = task.status
                style = self._get_status_style(status)
                is_selected = idx == self.selected_index

                sel_indicator = SELECTION_INDICATOR if is_selected else " "

                if status == TaskStatus.RUNNING and task.start_time:
                    time_str = self._format_duration(time.time() - task.start_time)
                elif task.duration is not None:
                    time_str = self._format_duration(task.duration)
                else:
                    time_str = "-"

                stdout_str = self._format_size(task.stdout_size)
                stderr_str = self._format_size(task.stderr_size)

                action_name = action_key.id.name

                if has_context:
                    context_formatted = self._context_formatter.format_id_with_symbol(
                        action_key.context_id, self.use_short_ids
                    )
                    row_data = [
                        sel_indicator,
                        context_formatted,
                        f"[{style}]{action_name}[/{style}]" if style else action_name,
                    ]
                else:
                    row_data = [
                        sel_indicator,
                        f"[{style}]{action_name}[/{style}]" if style else action_name,
                    ]

                if self.show_dirs:
                    action_key_str = self._action_formatter.format_label_plain(action_key, self.use_short_ids)
                    row_data.append(self.action_dirs_map.get(action_key_str, "-"))

                row_data.extend([
                    f"[{style}]{time_str}[/{style}]" if style else time_str,
                    f"[{style}]{stdout_str}[/{style}]" if style else stdout_str,
                    f"[{style}]{stderr_str}[/{style}]" if style else stderr_str,
                    f"[{style}]{status.value}[/{style}]" if style else status.value,
                ])

                table.add_row(*row_data)

            return table

    def _build_progress_caption(self) -> Table:
        """Build progress bar and legend as table caption."""
        with self.lock:
            counts: dict[TaskStatus, int] = {}
            for task in self.tasks.values():
                counts[task.status] = counts.get(task.status, 0) + 1

            total = len(self.tasks)

            caption_table = Table(
                show_header=False,
                show_edge=False,
                box=None,
                padding=0,
                expand=True,
            )
            caption_table.add_column(ratio=1)

            if total == 0:
                caption_table.add_row(Text("No tasks", style="dim"))
                return caption_table

            status_order = [TaskStatus.DONE, TaskStatus.RESTORED, TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.TBD]

            bar_table = Table(
                show_header=False,
                show_edge=False,
                box=None,
                padding=0,
                expand=True,
            )

            for status in status_order:
                count = counts.get(status, 0)
                if count > 0:
                    bar_table.add_column(ratio=count, no_wrap=True, overflow="crop")

            row_data = []
            for status in status_order:
                count = counts.get(status, 0)
                if count > 0:
                    ascii_sym, unicode_sym, color, _ = self.STATUS_DISPLAY[status]
                    symbol = ascii_sym if IS_WINDOWS else unicode_sym
                    row_data.append(Text(symbol * 500, style=color))

            if row_data:
                bar_table.add_row(*row_data)

            legend = Text()
            first = True
            for status in status_order:
                count = counts.get(status, 0)
                if count == 0:
                    continue

                ascii_sym, unicode_sym, color, label = self.STATUS_DISPLAY[status]
                symbol = ascii_sym if IS_WINDOWS else unicode_sym

                if not first:
                    legend.append("  ")
                first = False

                legend.append(symbol, style=color)
                legend.append(f" {label}: ", style="dim")
                legend.append(str(count), style=color)

            caption_table.add_row(bar_table)
            caption_table.add_row(legend)

            return caption_table

    def _build_text_status_header(self) -> Text:
        """Build text-based status header with counts (for no-color mode)."""
        with self.lock:
            counts: dict[TaskStatus, int] = {}
            for task in self.tasks.values():
                counts[task.status] = counts.get(task.status, 0) + 1

            parts = []
            if counts.get(TaskStatus.DONE, 0) > 0:
                parts.append(f"{counts[TaskStatus.DONE]} done")
            if counts.get(TaskStatus.RESTORED, 0) > 0:
                parts.append(f"{counts[TaskStatus.RESTORED]} restored")
            if counts.get(TaskStatus.RUNNING, 0) > 0:
                parts.append(f"{counts[TaskStatus.RUNNING]} running")
            if counts.get(TaskStatus.FAILED, 0) > 0:
                parts.append(f"{counts[TaskStatus.FAILED]} failed")
            if counts.get(TaskStatus.TBD, 0) > 0:
                parts.append(f"{counts[TaskStatus.TBD]} pending")

            return Text(" | ".join(parts) if parts else "No tasks")

    def _build_legend(self) -> Text:
        """Build legend showing status symbols, colors, and counts."""
        with self.lock:
            counts: dict[TaskStatus, int] = {}
            for task in self.tasks.values():
                counts[task.status] = counts.get(task.status, 0) + 1

            legend = Text()
            status_order = [TaskStatus.DONE, TaskStatus.RESTORED, TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.TBD]

            dim_style = "" if self.no_color else "dim"

            first = True
            for status in status_order:
                count = counts.get(status, 0)
                if count == 0:
                    continue

                ascii_sym, unicode_sym, color, label = self.STATUS_DISPLAY[status]
                symbol = ascii_sym if IS_WINDOWS or self.no_color else unicode_sym

                if not first:
                    legend.append("  ", style=dim_style)
                first = False

                if self.no_color:
                    legend.append(f"{symbol} {label}: {count}")
                else:
                    legend.append(symbol, style=color)
                    legend.append(f" {label}: ", style=dim_style)
                    legend.append(str(count), style=color)

            return legend

    def _build_header(self) -> str:
        """Build header text for detail views."""
        with self.lock:
            if self.state == ViewState.TABLE:
                return "Tasks"
            else:
                task = self._get_selected_task()
                task_label = self._action_formatter.format_label_plain(task.action_key, self.use_short_ids) if task else "Unknown"
                view_names = {
                    ViewState.META: "Meta",
                    ViewState.LOGS_STDOUT: "Stdout",
                    ViewState.LOGS_STDERR: "Stderr",
                    ViewState.OUTPUT: "Output",
                    ViewState.SOURCE: "Source",
                }
                return f"{view_names.get(self.state, 'View')} - {task_label}"

    def _build_footer(self) -> Text:
        """Build footer with key bindings, line counter, and progress bar."""
        dim_style = "" if self.no_color else "dim"
        cyan_style = "" if self.no_color else "cyan"
        cyan_dim_style = "" if self.no_color else "cyan dim"

        if self.state == ViewState.TABLE:
            return Text(self.TABLE_KEYS, style=dim_style)

        task = self._get_selected_task()
        if not task:
            keys = self.LOG_KEYS if self.state in (ViewState.LOGS_STDOUT, ViewState.LOGS_STDERR) else self.SCROLL_KEYS
            return Text(keys, style=dim_style)

        scroll_state = self._get_scroll_state(task.action_key, self.state)
        visible_height = self._get_content_height()
        total = scroll_state.total_lines

        if total == 0:
            line_info = "0 lines"
            progress_pct = 100
        else:
            start_line = scroll_state.offset + 1
            end_line = min(scroll_state.offset + visible_height, total)
            line_info = f"{start_line}-{end_line}/{total}"
            progress_pct = min(100, int((end_line / total) * 100)) if total > 0 else 100

        bar_width = 10
        filled = int(bar_width * progress_pct / 100)
        if IS_WINDOWS or self.no_color:
            bar = "#" * filled + "-" * (bar_width - filled)
        else:
            bar = "█" * filled + "░" * (bar_width - filled)

        keys = self.LOG_KEYS if self.state in (ViewState.LOGS_STDOUT, ViewState.LOGS_STDERR) else self.SCROLL_KEYS

        separator = " | " if IS_WINDOWS or self.no_color else " │ "

        footer = Text()
        footer.append(keys, style=dim_style)
        footer.append(separator, style=dim_style)
        footer.append(line_info, style=cyan_style)
        footer.append(" ", style=dim_style)
        footer.append(bar, style=cyan_dim_style)
        footer.append(f" {progress_pct}%", style=cyan_style)

        return footer

    def _format_line_number(self, line_num: int, total_lines: int) -> Text:
        """Format line number prefix with appropriate width."""
        width = max(4, len(str(total_lines)))
        sep = "|" if IS_WINDOWS or self.no_color else "│"
        dim_style = "" if self.no_color else "dim"
        text = Text()
        text.append(f"{line_num:{width}} ", style=dim_style)
        text.append(f"{sep} ", style=dim_style)
        return text

    def _format_log_line(self, line: str, line_num: int, total_lines: int) -> Text:
        """Format log line with line number prefix, keeping original content unmodified."""
        text = self._format_line_number(line_num, total_lines)
        text.append(line)
        return text

    def _build_detail_content(self):
        """Build content for detail views with syntax highlighting."""
        task = self._get_selected_task()
        if not task or not task.action_dir:
            return Text("(no action directory)")

        visible_height = self._get_content_height()
        lines: list[str] = []
        content_type = "text"

        if self.state == ViewState.LOGS_STDOUT:
            log_path = task.action_dir / "stdout.log"
            content_type = "log"
            if log_path.exists():
                try:
                    lines = log_path.read_text(encoding="utf-8").splitlines()
                except Exception:
                    lines = ["(error reading file)"]
        elif self.state == ViewState.LOGS_STDERR:
            log_path = task.action_dir / "stderr.log"
            content_type = "log"
            if log_path.exists():
                try:
                    lines = log_path.read_text(encoding="utf-8").splitlines()
                except Exception:
                    lines = ["(error reading file)"]
        elif self.state == ViewState.META:
            meta_path = task.action_dir / "meta.json"
            if meta_path.exists():
                content_type = "json"
                try:
                    data = json.loads(meta_path.read_text(encoding="utf-8"))
                    lines = json.dumps(data, indent=2).splitlines()
                except Exception as e:
                    content_type = "text"
                    lines = [f"(error: {e})"]
            else:
                content_type = "text"
                lines = ["(meta.json not found)"]
        elif self.state == ViewState.OUTPUT:
            output_path = task.action_dir / "output.json"
            if output_path.exists():
                content_type = "json"
                try:
                    data = json.loads(output_path.read_text(encoding="utf-8"))
                    lines = json.dumps(data, indent=2).splitlines()
                except Exception as e:
                    content_type = "text"
                    lines = [f"(error: {e})"]
            else:
                content_type = "text"
                lines = ["(output.json not found)"]
        elif self.state == ViewState.SOURCE:
            script_path = None
            for ext in [".sh", ".py"]:
                path = task.action_dir / f"script{ext}"
                if path.exists():
                    script_path = path
                    content_type = "python" if ext == ".py" else "shell"
                    break
            if script_path:
                try:
                    lines = script_path.read_text(encoding="utf-8").splitlines()
                except Exception as e:
                    content_type = "text"
                    lines = [f"(error: {e})"]
            else:
                content_type = "text"
                lines = ["(script not found)"]

        if not lines:
            lines = ["(empty)"]

        total_lines = len(lines)
        scroll_state = self._update_scroll_state(task.action_key, self.state, total_lines, visible_height)

        start = scroll_state.offset
        end = start + visible_height
        visible_lines = lines[start:end]

        if not self.no_color and content_type in ("json", "shell", "python"):
            lexer_map = {"json": "json", "shell": "bash", "python": "python"}
            lexer = lexer_map[content_type]

            full_content = "\n".join(lines)

            syntax = Syntax(
                full_content,
                lexer,
                line_numbers=True,
                line_range=(start + 1, end),
                start_line=start + 1,
                word_wrap=False,
                background_color="default",
            )
            return syntax

        result = Text()
        width = max(4, len(str(total_lines)))
        sep = "|" if IS_WINDOWS or self.no_color else "│"
        dim_style = "" if self.no_color else "dim"

        for i, line in enumerate(visible_lines):
            if i > 0:
                result.append("\n")

            line_num = start + i + 1

            if self.no_color:
                result.append(f"{line_num:{width}} {sep} {line}")
            elif content_type == "log":
                result.append_text(self._format_log_line(line, line_num, total_lines))
            else:
                result.append(f"{line_num:{width}} ", style=dim_style)
                result.append(f"{sep} ", style=dim_style)
                result.append(line)

        return result

    def _build_renderable(self) -> Group:
        """Build the complete renderable for the current state."""
        footer = self._build_footer()

        if self.state == ViewState.TABLE:
            if self.no_color:
                header = self._build_text_status_header()
                content = self._build_table(include_progress=False)
                return Group(
                    header,
                    content,
                    Text(""),
                    footer,
                )
            else:
                content = self._build_table(include_progress=True)
                return Group(
                    content,
                    Text(""),
                    footer,
                )
        else:
            header = self._build_header()
            content = self._build_detail_content()
            header_style = "" if self.no_color else "bold reverse"
            header_text = Text(f" {header} ", style=header_style)

            return Group(
                header_text,
                content,
                Text(""),
                footer,
            )

    # =========================================================================
    # Main Loop
    # =========================================================================

    def _main_loop(self) -> None:
        """Main loop handling both input and display updates."""
        last_update = 0.0
        update_interval = 1.0 / 24.0

        while not self.stop_flag:
            key = self._read_key()
            if key:
                if self.state == ViewState.TABLE:
                    if self._handle_key_table(key):
                        break
                else:
                    self._handle_key_scroll(key)

                if self.live:
                    self.live.update(self._build_renderable(), refresh=True)

            now = time.time()
            if now - last_update >= update_interval:
                last_update = now
                if self.live:
                    self.live.update(self._build_renderable(), refresh=True)

            time.sleep(0.01)

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def start(self) -> None:
        """Start the interactive display."""
        self.stop_flag = False

        self.live = Live(
            self._build_renderable(),
            console=self.console,
            refresh_per_second=24,
            transient=False,
            auto_refresh=False,
            vertical_overflow="visible",
        )
        self.live.start()
        self.live.refresh()

        self._setup_terminal()

        self._main_thread = threading.Thread(target=self._main_loop, daemon=True)
        self._main_thread.start()

    def stop(self) -> None:
        """Stop the interactive display."""
        self.stop_flag = True

        if hasattr(self, '_main_thread') and self._main_thread.is_alive():
            self._main_thread.join(timeout=1.0)

        self._restore_terminal()

        if self.live:
            self.live.update(self._build_renderable(), refresh=True)
            self.live.stop()
            self.live = None

    def wait_for_quit(self) -> None:
        """Wait for user to quit (call after execution completes with --it)."""
        if not self.keep_running:
            return

        with self.lock:
            self.execution_complete = True

        if hasattr(self, '_main_thread'):
            self._main_thread.join()

        self.stop()
