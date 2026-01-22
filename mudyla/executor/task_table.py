"""Rich table manager for displaying task execution status with interactive navigation.

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
from typing import Any, Optional

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text

# Cross-platform terminal handling
IS_WINDOWS = sys.platform == "win32"

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
    name: str
    status: TaskStatus = TaskStatus.TBD
    start_time: Optional[float] = None
    duration: Optional[float] = None
    stdout_size: int = 0
    stderr_size: int = 0
    action_dir: Optional[Path] = None


class TaskTableManager:
    """State machine-based task table with interactive navigation.

    Uses Rich Live display for flicker-free rendering with:
    - Header: View name / status summary
    - Content: Table or scrollable text
    - Footer: Key bindings for current state
    """

    # Key bindings per state
    TABLE_KEYS = "↑/↓/j/k navigate | Enter/l stdout | e stderr | m meta | o output | s source | q kill"
    SCROLL_KEYS = "↑/↓/j/k scroll | Shift+↑ top | Shift+↓ bottom | q back"
    LOG_KEYS = "↑/↓/j/k scroll | Shift+↑ top | Shift+↓ bottom | r refresh | q back"

    def __init__(
        self,
        task_names: list[str],
        no_color: bool = False,
        action_dirs: Optional[dict[str, str]] = None,
        show_dirs: bool = False,
        run_directory: Optional[Path] = None,
        keep_running: bool = False,
    ):
        self.no_color = no_color
        self.show_dirs = show_dirs
        self.run_directory = run_directory
        self.keep_running = keep_running
        self.action_dirs_map = action_dirs or {}

        # Console for rendering
        self.console = Console(force_terminal=True)

        # Shared state - updated by engine
        self.tasks: dict[str, TaskState] = {
            name: TaskState(name=name) for name in task_names
        }
        self.task_order = list(task_names)

        # View state
        self.state = ViewState.TABLE
        self.selected_index = 0
        self.execution_complete = False

        # Scroll states per view (keyed by task_name + view)
        self._scroll_states: dict[str, ScrollState] = {}

        # Terminal state
        self._old_terminal_settings: Optional[list[Any]] = None

        # Threading and Live display
        self.lock = threading.RLock()
        self.stop_flag = False
        self.kill_requested = False  # Flag for engine to check
        self._kill_callback: Optional[callable] = None  # Callback to kill engine processes
        self.live: Optional[Live] = None
        self._main_thread: Optional[threading.Thread] = None

    # =========================================================================
    # Shared State API (called by engine)
    # =========================================================================

    def mark_running(self, task_name: str, action_dir: Optional[Path] = None) -> None:
        """Mark a task as running."""
        with self.lock:
            if task_name in self.tasks:
                self.tasks[task_name].status = TaskStatus.RUNNING
                self.tasks[task_name].start_time = time.time()
                if action_dir:
                    self.tasks[task_name].action_dir = action_dir

    def mark_done(self, task_name: str, duration: float) -> None:
        """Mark a task as done."""
        with self.lock:
            if task_name in self.tasks:
                self.tasks[task_name].status = TaskStatus.DONE
                self.tasks[task_name].duration = duration

    def mark_failed(self, task_name: str, duration: float) -> None:
        """Mark a task as failed."""
        with self.lock:
            if task_name in self.tasks:
                self.tasks[task_name].status = TaskStatus.FAILED
                self.tasks[task_name].duration = duration

    def mark_restored(self, task_name: str, duration: float, action_dir: Optional[Path] = None) -> None:
        """Mark a task as restored from previous run."""
        with self.lock:
            if task_name in self.tasks:
                self.tasks[task_name].status = TaskStatus.RESTORED
                self.tasks[task_name].duration = duration
                if action_dir:
                    self.tasks[task_name].action_dir = action_dir

    def mark_execution_complete(self) -> None:
        """Mark execution as complete."""
        with self.lock:
            self.execution_complete = True

    def update_output_sizes(self, task_name: str, stdout_size: int, stderr_size: int) -> None:
        """Update stdout and stderr sizes for a task."""
        with self.lock:
            if task_name in self.tasks:
                self.tasks[task_name].stdout_size = stdout_size
                self.tasks[task_name].stderr_size = stderr_size

    def set_kill_callback(self, callback: callable) -> None:
        """Set callback to be called when user requests kill (q key).

        The callback should gracefully terminate all running processes.
        """
        self._kill_callback = callback

    def is_kill_requested(self) -> bool:
        """Check if user has requested to kill execution."""
        return self.kill_requested

    # =========================================================================
    # Scroll State Management
    # =========================================================================

    def _get_scroll_key(self, task_name: str, view: ViewState) -> str:
        """Get unique key for scroll state."""
        return f"{task_name}:{view.name}"

    def _get_scroll_state(self, task_name: str, view: ViewState) -> ScrollState:
        """Get or create scroll state for a view."""
        key = self._get_scroll_key(task_name, view)
        if key not in self._scroll_states:
            self._scroll_states[key] = ScrollState()
        return self._scroll_states[key]

    def _update_scroll_state(
        self,
        task_name: str,
        view: ViewState,
        total_lines: int,
        visible_height: int
    ) -> ScrollState:
        """Update scroll state with new content info, handling auto-scroll."""
        state = self._get_scroll_state(task_name, view)
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
        return max(5, height - 6)  # Reserve lines for header/footer/panel borders

    # =========================================================================
    # Key Input (Cross-platform)
    # =========================================================================

    def _read_key_windows(self) -> str:
        """Read a single key press on Windows (non-blocking)."""
        if not msvcrt.kbhit():
            return ""

        ch = msvcrt.getch()

        # Handle special keys (arrow keys return two bytes)
        if ch in (b'\x00', b'\xe0'):
            if msvcrt.kbhit():
                ch2 = msvcrt.getch()
                if ch2 == b'H':  # Up arrow
                    return "up"
                elif ch2 == b'P':  # Down arrow
                    return "down"
                elif ch2 == b'\x8d':  # Shift+Up (141)
                    return "top"
                elif ch2 == b'\x91':  # Shift+Down (145)
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
        }
        return key_map.get(char, "")

    def _read_key_unix(self) -> str:
        """Read a single key press on Unix (non-blocking with short timeout)."""
        try:
            # Check if input is available
            ready, _, _ = select.select([sys.stdin], [], [], 0.02)
            if not ready:
                return ""

            # Read first character using os.read for more reliable behavior
            fd = sys.stdin.fileno()
            ch = os.read(fd, 1).decode('utf-8', errors='ignore')
            if not ch:
                return ""

            # Handle escape sequences for arrow keys
            if ch == "\x1b":
                # Set stdin to non-blocking to read rest of escape sequence
                old_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, old_flags | os.O_NONBLOCK)

                try:
                    # Small delay for escape sequence to arrive fully
                    time.sleep(0.02)

                    # Read up to 5 bytes (handles various escape sequences)
                    seq = b""
                    try:
                        seq = os.read(fd, 5)
                    except (OSError, BlockingIOError):
                        pass

                    seq_str = seq.decode('utf-8', errors='ignore')

                    # Shift+Arrow keys (go to top/bottom)
                    if seq_str.startswith("[1;2A"):
                        return "top"
                    elif seq_str.startswith("[1;2B"):
                        return "bottom"
                    # Standard arrow key sequences
                    elif seq_str.startswith("[A") or seq_str == "OA":
                        return "up"
                    elif seq_str.startswith("[B") or seq_str == "OB":
                        return "down"
                    elif seq_str.startswith("[C") or seq_str == "OC":
                        return "right"
                    elif seq_str.startswith("[D") or seq_str == "OD":
                        return "left"
                    # Some terminals send different sequences
                    elif "A" in seq_str and "[" in seq_str:
                        return "up"
                    elif "B" in seq_str and "[" in seq_str:
                        return "down"
                finally:
                    # Restore original flags
                    fcntl.fcntl(fd, fcntl.F_SETFL, old_flags)

                return ""  # Bare escape or unrecognized sequence

            # Map character keys
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
                self.selected_index = min(len(self.task_order) - 1, self.selected_index + 1)
            elif key == "q":
                # Signal kill - set flag and call callback
                self.kill_requested = True
                if self._kill_callback:
                    try:
                        self._kill_callback()
                    except Exception:
                        pass  # Don't let callback errors break the UI
                return True  # Signal exit/kill all processes
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
        """Handle key in scrollable views."""
        with self.lock:
            task_name = self._get_selected_task_name()
            if not task_name:
                return

            if key == "q":
                self.state = ViewState.TABLE
                return

            scroll_state = self._get_scroll_state(task_name, self.state)
            visible_height = self._get_content_height()
            max_offset = max(0, scroll_state.total_lines - visible_height)

            if key == "up":
                scroll_state.offset = max(0, scroll_state.offset - 1)
                scroll_state.at_end = False
            elif key == "down":
                scroll_state.offset = min(max_offset, scroll_state.offset + 1)
                scroll_state.at_end = scroll_state.offset >= max_offset
            elif key == "top":
                scroll_state.offset = 0
                scroll_state.at_end = max_offset == 0
            elif key == "bottom":
                scroll_state.offset = max_offset
                scroll_state.at_end = True

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

    def _get_selected_task_name(self) -> str:
        """Get currently selected task name."""
        with self.lock:
            if 0 <= self.selected_index < len(self.task_order):
                return self.task_order[self.selected_index]
            return ""

    def _get_selected_task(self) -> Optional[TaskState]:
        """Get currently selected task."""
        name = self._get_selected_task_name()
        return self.tasks.get(name)

    def _format_context(self, context_str: str) -> str:
        """Format context with colors (symbol/emoji + blue bold hex ID)."""
        if context_str and len(context_str) == 7:
            hex_part = context_str[1:]
            try:
                int(hex_part, 16)
                is_short_id = True
            except ValueError:
                is_short_id = False

            if is_short_id:
                symbol = context_str[0]
                hex_id = context_str[1:]
                return f"{symbol}[blue bold]{hex_id}[/blue bold]"
        return context_str

    # =========================================================================
    # View Renderers
    # =========================================================================

    def _build_table(self) -> Table:
        """Build the task table (original style with context markers)."""
        with self.lock:
            has_context = any("#" in name for name in self.task_order)

            table = Table(show_header=True, header_style="bold")

            # Selection indicator column
            table.add_column("", width=1, no_wrap=True)

            if has_context:
                table.add_column("Context", no_wrap=True)
                table.add_column("Action", style="cyan bold", no_wrap=True)
            else:
                table.add_column("Task", style="cyan bold", no_wrap=True)

            if self.show_dirs:
                table.add_column("Dir", style="dim", no_wrap=True)
            table.add_column("Time", justify="right", no_wrap=True)
            table.add_column("Stdout", justify="right", no_wrap=True)
            table.add_column("Stderr", justify="right", no_wrap=True)
            table.add_column("Status", justify="center", no_wrap=True)

            for idx, task_name in enumerate(self.task_order):
                task = self.tasks[task_name]
                status = task.status
                style = self._get_status_style(status)
                is_selected = idx == self.selected_index

                # Selection indicator
                sel_indicator = "▶" if is_selected else " "

                # Calculate time
                if status == TaskStatus.RUNNING and task.start_time:
                    time_str = self._format_duration(time.time() - task.start_time)
                elif task.duration is not None:
                    time_str = self._format_duration(task.duration)
                else:
                    time_str = "-"

                stdout_str = self._format_size(task.stdout_size)
                stderr_str = self._format_size(task.stderr_size)

                # Build row data
                if has_context and "#" in task_name:
                    context_str, action_name = task_name.split("#", 1)
                    context_formatted = self._format_context(context_str)
                    row_data = [
                        sel_indicator,
                        context_formatted,
                        f"[{style}]{action_name}[/{style}]" if style else action_name,
                    ]
                elif has_context:
                    row_data = [
                        sel_indicator,
                        "",
                        f"[{style}]{task_name}[/{style}]" if style else task_name,
                    ]
                else:
                    row_data = [
                        sel_indicator,
                        f"[{style}]{task_name}[/{style}]" if style else task_name,
                    ]

                if self.show_dirs:
                    row_data.append(self.action_dirs_map.get(task_name, "-"))

                row_data.extend([
                    f"[{style}]{time_str}[/{style}]" if style else time_str,
                    f"[{style}]{stdout_str}[/{style}]" if style else stdout_str,
                    f"[{style}]{stderr_str}[/{style}]" if style else stderr_str,
                    f"[{style}]{status.value}[/{style}]" if style else status.value,
                ])

                table.add_row(*row_data)

            return table

    def _build_header(self) -> str:
        """Build header text with status counts."""
        with self.lock:
            if self.state == ViewState.TABLE:
                counts: dict[TaskStatus, int] = {}
                for task in self.tasks.values():
                    counts[task.status] = counts.get(task.status, 0) + 1

                parts = []
                if counts.get(TaskStatus.TBD, 0) > 0:
                    parts.append(f"{counts[TaskStatus.TBD]} pending")
                if counts.get(TaskStatus.RUNNING, 0) > 0:
                    parts.append(f"{counts[TaskStatus.RUNNING]} running")
                if counts.get(TaskStatus.DONE, 0) > 0:
                    parts.append(f"{counts[TaskStatus.DONE]} done")
                if counts.get(TaskStatus.RESTORED, 0) > 0:
                    parts.append(f"{counts[TaskStatus.RESTORED]} restored")
                if counts.get(TaskStatus.FAILED, 0) > 0:
                    parts.append(f"{counts[TaskStatus.FAILED]} failed")

                return " | ".join(parts) if parts else "Tasks"
            else:
                task = self._get_selected_task()
                task_name = task.name if task else "Unknown"
                view_names = {
                    ViewState.META: "Meta",
                    ViewState.LOGS_STDOUT: "Stdout",
                    ViewState.LOGS_STDERR: "Stderr",
                    ViewState.OUTPUT: "Output",
                    ViewState.SOURCE: "Source",
                }
                return f"{view_names.get(self.state, 'View')} - {task_name}"

    def _build_footer(self) -> str:
        """Build footer text with key bindings."""
        if self.state == ViewState.TABLE:
            return self.TABLE_KEYS
        elif self.state in (ViewState.LOGS_STDOUT, ViewState.LOGS_STDERR):
            return self.LOG_KEYS
        else:
            return self.SCROLL_KEYS

    def _build_detail_content(self) -> str:
        """Build content for detail views (logs, meta, output, source)."""
        task = self._get_selected_task()
        if not task or not task.action_dir:
            return "(no action directory)"

        visible_height = self._get_content_height()
        lines: list[str] = []

        if self.state == ViewState.LOGS_STDOUT:
            log_path = task.action_dir / "stdout.log"
            if log_path.exists():
                try:
                    lines = log_path.read_text().splitlines()
                except Exception:
                    lines = ["(error reading file)"]
        elif self.state == ViewState.LOGS_STDERR:
            log_path = task.action_dir / "stderr.log"
            if log_path.exists():
                try:
                    lines = log_path.read_text().splitlines()
                except Exception:
                    lines = ["(error reading file)"]
        elif self.state == ViewState.META:
            meta_path = task.action_dir / "meta.json"
            if meta_path.exists():
                try:
                    data = json.loads(meta_path.read_text())
                    lines = json.dumps(data, indent=2).splitlines()
                except Exception as e:
                    lines = [f"(error: {e})"]
            else:
                lines = ["(meta.json not found)"]
        elif self.state == ViewState.OUTPUT:
            output_path = task.action_dir / "output.json"
            if output_path.exists():
                try:
                    data = json.loads(output_path.read_text())
                    lines = json.dumps(data, indent=2).splitlines()
                except Exception as e:
                    lines = [f"(error: {e})"]
            else:
                lines = ["(output.json not found)"]
        elif self.state == ViewState.SOURCE:
            script_path = None
            for ext in [".sh", ".py"]:
                path = task.action_dir / f"script{ext}"
                if path.exists():
                    script_path = path
                    break
            if script_path:
                try:
                    source_lines = script_path.read_text().splitlines()
                    lines = [f"{i+1:4} │ {line}" for i, line in enumerate(source_lines)]
                except Exception as e:
                    lines = [f"(error: {e})"]
            else:
                lines = ["(script not found)"]

        if not lines:
            lines = ["(empty)"]

        # Update scroll state
        total_lines = len(lines)
        scroll_state = self._update_scroll_state(task.name, self.state, total_lines, visible_height)

        # Get visible lines
        start = scroll_state.offset
        end = start + visible_height
        visible_lines = lines[start:end]

        return "\n".join(visible_lines)

    def _build_renderable(self) -> Group:
        """Build the complete renderable for the current state."""
        header = self._build_header()
        footer = self._build_footer()

        if self.state == ViewState.TABLE:
            content = self._build_table()
        else:
            content = Text(self._build_detail_content())

        header_text = Text(f" {header} ", style="bold reverse")
        footer_text = Text(footer, style="dim")

        return Group(
            header_text,
            content,
            Text(""),
            footer_text,
        )

    # =========================================================================
    # Main Loop (single-threaded for input + updates)
    # =========================================================================

    def _main_loop(self) -> None:
        """Main loop handling both input and display updates."""
        last_update = 0.0
        update_interval = 1.0 / 24.0  # 24 FPS

        while not self.stop_flag:
            # Handle input (non-blocking)
            key = self._read_key()
            if key:
                if self.state == ViewState.TABLE:
                    if self._handle_key_table(key):
                        break
                else:
                    self._handle_key_scroll(key)

                # Immediate update after key press
                if self.live:
                    self.live.update(self._build_renderable())
                    self.live.refresh()

            # Periodic update for running timers at 24 FPS
            now = time.time()
            if now - last_update >= update_interval:
                last_update = now
                if self.live:
                    self.live.update(self._build_renderable())
                    self.live.refresh()

            time.sleep(0.01)  # Small sleep to prevent CPU spinning

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def start(self) -> None:
        """Start the interactive display."""
        self.stop_flag = False

        # Create and start Live display (auto_refresh=False for manual control)
        self.live = Live(
            self._build_renderable(),
            console=self.console,
            refresh_per_second=24,
            transient=False,
            auto_refresh=False,
        )
        self.live.start()
        self.live.refresh()  # Initial render

        # Setup terminal AFTER Live starts (important for proper stdin handling)
        self._setup_terminal()

        # Start main loop in background thread
        self._main_thread = threading.Thread(target=self._main_loop, daemon=True)
        self._main_thread.start()

    def stop(self) -> None:
        """Stop the interactive display."""
        self.stop_flag = True

        if hasattr(self, '_main_thread') and self._main_thread.is_alive():
            self._main_thread.join(timeout=1.0)

        # Restore terminal BEFORE stopping Live
        self._restore_terminal()

        if self.live:
            self.live.update(self._build_renderable())  # Final update
            self.live.stop()
            self.live = None

    def wait_for_quit(self) -> None:
        """Wait for user to quit (call after execution completes with --it)."""
        if not self.keep_running:
            return

        with self.lock:
            self.execution_complete = True

        # Wait for main thread to signal exit
        if hasattr(self, '_main_thread'):
            self._main_thread.join()

        self.stop()
