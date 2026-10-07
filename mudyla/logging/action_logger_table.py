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

import codecs
import json
import os
import sys
import threading
import time
from bisect import bisect_right
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from rich import box
from rich.cells import cell_len
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from ..dag.graph import ActionKey
from .formatters import OutputFormatter
from .action_logger import ActionLogger
from .formatters.failure import legacy_failure
from .terminal_background import BackgroundProbe

if TYPE_CHECKING:
    from ..executor.engine import ActionResult
from .windows_mouse import WindowsMouseInput

# Cross-platform terminal handling
IS_WINDOWS = sys.platform == "win32"

# Selection indicator - Windows console encoding doesn't support Unicode triangles
SELECTION_INDICATOR = ">" if IS_WINDOWS else "▶"

if IS_WINDOWS:
    import msvcrt
else:
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
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


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
    line_anchors: list[tuple[int, int]] = field(default_factory=list)
    anchor: Optional[tuple[int, int]] = None


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
    latest: str = ""
    stream: str = "stdout"


class ActionLoggerTable(ActionLogger):
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
    FRAME_ROWS = 6
    CONTENT_HORIZONTAL_PADDING = 4
    WRAP_HIGHLIGHTED_CONTENT = False
    ALTERNATE_SCREEN = False
    ESCAPE_WAIT_SECONDS = 0.02
    MAX_ESCAPE_BYTES = 32
    MAX_INPUT_BYTES = 4096
    MOUSE_WHEEL_ROWS = 3
    MAX_INPUT_CHARS = 4096

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
        terminal_env = dict(os.environ)
        if terminal_env.get("TERM") in {"dumb", "unknown"}:
            terminal_env["TERM"] = "xterm-256color"
        self.console = Console(force_terminal=True, force_interactive=True, no_color=no_color, _environ=terminal_env)

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
        self._terminal_active = False
        self._mouse_enabled = False
        self._windows_mouse: Optional[WindowsMouseInput] = None
        self._input_enabled = sys.stdin.isatty()
        self._input_action: Optional[ActionKey] = None
        self._input_text = ""
        self._input_cursor = 0
        self._input_message = ""
        self._input_callback: Optional[Callable[[ActionKey, Optional[str]], Optional[str]]] = None
        self._input_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._pending_input = b""
        self._pending_windows_input = ""
        self._background_probe: Optional[BackgroundProbe] = None
        self._windows_input_decoder = codecs.getincrementaldecoder("utf-16-le")(errors="replace")

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
            if self._input_action is not None:
                self._input_message = self._input_message or "Action finished"
            self._input_action = None
            for task in self.tasks.values():
                if task.status == TaskStatus.TBD:
                    task.status = TaskStatus.SKIPPED
                elif task.status == TaskStatus.RUNNING:
                    task.status = TaskStatus.CANCELLED

    def uses_terminal_input(self) -> bool:
        return self._input_enabled

    def set_input_callback(self, callback: Callable[[ActionKey, Optional[str]], Optional[str]]) -> None:
        self._input_callback = callback

    def report_input_error(self, action_key: ActionKey, message: str) -> None:
        with self.lock:
            label = self._action_formatter.format_label_plain(action_key, self.use_short_ids)
            self._input_message = f"{label}: {message}"

    def _handle_input_key(self, key: str) -> None:
        with self.lock:
            action_key = self._input_action
            if action_key is None:
                return
            if key == "escape":
                self._input_action = None
                self._input_message = ""
            elif key in ("enter", "eof"):
                if self._input_callback is None:
                    raise RuntimeError("Action input callback is not installed")
                error = self._input_callback(action_key, None if key == "eof" else self._input_text + "\n")
                self._input_message = error or ""
                if error is None:
                    self._input_text = ""
                    self._input_cursor = 0
                    if key == "eof":
                        self._input_action = None
            elif key == "backspace" and self._input_cursor:
                self._input_text = self._input_text[:self._input_cursor - 1] + self._input_text[self._input_cursor:]
                self._input_cursor -= 1
            elif key == "left":
                self._input_cursor = max(0, self._input_cursor - 1)
            elif key == "right":
                self._input_cursor = min(len(self._input_text), self._input_cursor + 1)
            elif len(key) == 1 and key.isprintable():
                if len(self._input_text) < self.MAX_INPUT_CHARS:
                    self._input_text = self._input_text[:self._input_cursor] + key + self._input_text[self._input_cursor:]
                    self._input_cursor += 1
                    self._input_message = ""
                else:
                    self._input_message = "Input line is full"

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
        state.total_lines = total_lines

        max_offset = max(0, total_lines - visible_height)

        # Follow the end across both content and viewport changes.
        if state.at_end:
            state.offset = max_offset

        # Clamp offset
        state.offset = max(0, min(state.offset, max_offset))

        return state

    # =========================================================================
    # Terminal Management (Cross-platform)
    # =========================================================================

    def _setup_terminal(self) -> None:
        """Set up terminal for raw input (cross-platform)."""
        if not self._input_enabled:
            return
        if sys.platform == "win32":
            self._old_terminal_settings = None
            self._windows_mouse = WindowsMouseInput(msvcrt.get_osfhandle(sys.stdin.fileno()))
        else:
            try:
                self._old_terminal_settings = termios.tcgetattr(sys.stdin)
                settings = termios.tcgetattr(sys.stdin)
                tty.cfmakecbreak(settings)
                settings[3] |= termios.NOFLSH
                termios.tcsetattr(sys.stdin, termios.TCSANOW, settings)
                self._terminal_active = True
            except (termios.error, AttributeError, ValueError):
                self._old_terminal_settings = None

    def _restore_terminal(self) -> None:
        """Restore terminal settings (cross-platform)."""
        if self._windows_mouse is not None:
            self._windows_mouse.set_capture(False)
        if sys.platform != "win32" and self._terminal_active and self._old_terminal_settings:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._old_terminal_settings)
                self._terminal_active = False
            except (termios.error, ValueError):
                pass

    def _set_mouse_capture(self, enabled: bool) -> None:
        enabled = enabled and self.ALTERNATE_SCREEN and self.uses_terminal_input()
        if self._mouse_enabled == enabled:
            return
        if self._windows_mouse is not None:
            self._windows_mouse.set_capture(enabled)
        self._mouse_enabled = enabled
        controls = "\x1b[?1000h\x1b[?1006h" if enabled else "\x1b[?1006l\x1b[?1000l"
        self.console.file.write(controls)
        self.console.file.flush()

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
        return max(1, height - self.FRAME_ROWS)

    # =========================================================================
    # Key Input (Cross-platform)
    # =========================================================================

    def _read_key_windows(self) -> str:
        """Read a single key press on Windows (non-blocking)."""
        if sys.platform != "win32":
            raise RuntimeError("Windows terminal input requires Windows")
        if self._background_probe is not None:
            self._pending_windows_input += self._background_probe.feed("", time.monotonic())
        for _ in range(self.MAX_INPUT_BYTES):
            if self._windows_mouse is not None:
                mouse_key = self._windows_mouse.read()
                if mouse_key is not None:
                    return mouse_key
            if self._pending_windows_input:
                ch, self._pending_windows_input = self._pending_windows_input[0], self._pending_windows_input[1:]
                break
            if not msvcrt.kbhit():
                return ""
            ch = msvcrt.getwch()
            if self._background_probe is None:
                break
            filtered = self._background_probe.feed(ch, time.monotonic())
            if filtered:
                ch, self._pending_windows_input = filtered[0], filtered[1:]
                break
        else:
            return ""

        if ch in ('\x00', '\xe0'):
            if self._pending_windows_input or msvcrt.kbhit():
                if self._pending_windows_input:
                    ch2, self._pending_windows_input = self._pending_windows_input[0], self._pending_windows_input[1:]
                else:
                    ch2 = msvcrt.getwch()
                if ch2 == 'H':
                    return "up"
                elif ch2 == 'P':
                    return "down"
                elif ch2 == 'I':
                    return "page_up"
                elif ch2 == 'Q':
                    return "page_down"
                elif ch2 == 'G':
                    return "top"
                elif ch2 == 'O':
                    return "bottom"
                elif ch2 == '\x8d':
                    return "top"
                elif ch2 == '\x91':
                    return "bottom"
                elif ch2 == 'K':
                    return "left"
                elif ch2 == 'M':
                    return "right"
            return ""

        char = self._windows_input_decoder.decode(ch.encode("utf-16-le", errors="surrogatepass"))
        if not char:
            return ""
        if self._input_action is not None:
            return {"\r": "enter", "\n": "enter", "\x1b": "escape", "\b": "backspace", "\x04": "eof"}.get(char, char)

        key_map = {
            "\r": "enter", "\n": "enter",
            "q": "q", "Q": "q",
            "m": "m", "M": "m",
            "l": "l", "L": "l",
            "e": "e", "E": "e",
            "o": "o", "O": "o",
            "s": "s", "S": "s",
            "r": "r", "R": "r",
            "i": "i", "I": "i",
            "v": "v", "V": "v",
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

    def _read_unix_byte(self, timeout: float) -> Optional[bytes]:
        if self._background_probe is not None:
            self._pending_input += self._background_probe.feed("", time.monotonic()).encode("latin1")
        remaining = self.MAX_INPUT_BYTES if self._background_probe is not None else 1
        while not self._pending_input and remaining:
            fd = sys.stdin.fileno()
            ready, _, _ = select.select([fd], [], [], timeout)
            if not ready:
                return None
            raw = os.read(fd, remaining)
            if not raw:
                return b""
            self._pending_input = (self._background_probe.feed(raw.decode("latin1"), time.monotonic()).encode("latin1")
                                   if self._background_probe is not None else raw)
            remaining -= len(raw)
            timeout = 0
        if not self._pending_input:
            return None
        raw, self._pending_input = self._pending_input[:1], self._pending_input[1:]
        return raw

    def _read_key_unix(self) -> str:
        """Read a single key press on Unix (non-blocking with short timeout)."""
        try:
            raw = self._read_unix_byte(self.ESCAPE_WAIT_SECONDS)
            if raw is None:
                return ""
            if not raw:
                return "terminal_eof"
            ch = self._input_decoder.decode(raw)
            if not ch:
                return ""

            if ch == "\x1b":
                seq = bytearray()
                while len(seq) < self.MAX_ESCAPE_BYTES:
                    part = self._read_unix_byte(self.ESCAPE_WAIT_SECONDS)
                    if not part:
                        break
                    seq.extend(part)
                    if len(seq) == 1:
                        if part not in (b"[", b"O"):
                            self._pending_input = part + self._pending_input
                            break
                    elif b"@" <= part <= b"~":
                        break
                sequence = seq.decode("ascii", errors="ignore")
                if sequence.startswith("[<") and sequence.endswith(("M", "m")):
                    fields = sequence[2:-1].split(";")
                    if sequence.endswith("M") and len(fields) == 3 and all(field.isdigit() for field in fields):
                        button = int(fields[0])
                        if button & 64 and button & 3 in (0, 1):
                            return "wheel_down" if button & 1 else "wheel_up"
                    return ""
                return {
                    "[1;2A": "top", "[1;2B": "bottom",
                    "[A": "up", "OA": "up", "[B": "down", "OB": "down",
                    "[C": "right", "OC": "right", "[D": "left", "OD": "left",
                    "[5~": "page_up", "[6~": "page_down",
                    "[H": "top", "[1~": "top", "OH": "top",
                    "[F": "bottom", "[4~": "bottom", "OF": "bottom",
                }.get(sequence, "escape")

            if self._input_action is not None:
                return {"\r": "enter", "\n": "enter", "\x7f": "backspace", "\b": "backspace", "\x04": "eof"}.get(ch, ch)

            key_map = {
                "\r": "enter", "\n": "enter",
                "q": "q", "Q": "q",
                "m": "m", "M": "m",
                "l": "l", "L": "l",
                "e": "e", "E": "e",
                "o": "o", "O": "o",
                "s": "s", "S": "s",
                "r": "r", "R": "r",
                "i": "i", "I": "i",
                "v": "v", "V": "v",
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
        except (OSError, ValueError):
            return "terminal_eof"

    def _read_key(self) -> str:
        """Read a single key press (cross-platform, non-blocking)."""
        if not self._input_enabled:
            return ""
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
            elif key in {"page_up", "wheel_up", "page_down", "wheel_down"}:
                amount = self.MOUSE_WHEEL_ROWS if key.startswith("wheel") else self._get_content_height()
                if key in {"page_up", "wheel_up"}:
                    amount = -amount
                self.selected_index = max(0, min(len(self.action_keys) - 1, self.selected_index + amount))
            elif key == "top":
                self.selected_index = 0
            elif key == "bottom":
                self.selected_index = max(0, len(self.action_keys) - 1)
            elif key == "q":
                self.kill_requested = not self.execution_complete
                if self.kill_requested and self._kill_callback:
                    try:
                        self._kill_callback()
                    except Exception:
                        pass
                return True
            elif key == "i":
                action_key = self._get_input_target()
                if action_key is not None:
                    self._input_action = action_key
                    self._input_text = ""
                    self._input_cursor = 0
                    self._input_message = ""
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
            scroll_state.anchor = None
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

            if key == "wheel_up":
                scroll_state.offset = max(0, scroll_state.offset - self.MOUSE_WHEEL_ROWS)
                scroll_state.at_end = False
            elif key == "wheel_down":
                scroll_state.offset = min(max_offset, scroll_state.offset + self.MOUSE_WHEEL_ROWS)
                scroll_state.at_end = scroll_state.offset >= max_offset
            elif key == "up":
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

    def _display_text(self, value: str) -> str:
        return value.encode(self.console.encoding, errors="replace").decode(self.console.encoding)

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
            TaskStatus.SKIPPED: "dim",
            TaskStatus.CANCELLED: "yellow",
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

    def _get_input_target(self) -> Optional[ActionKey]:
        if self.state not in {ViewState.TABLE, ViewState.LOGS_STDOUT}:
            return None
        task = self._get_selected_task()
        return task.action_key if task is not None and task.status == TaskStatus.RUNNING else None

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
        TaskStatus.SKIPPED: ("-", "-", "dim", "skipped"),
        TaskStatus.CANCELLED: ("!", "!", "yellow", "cancelled"),
    }

    def _table_window(self) -> tuple[int, int]:
        visible = self._get_content_height()
        start = max(0, min(self.selected_index - visible // 2, len(self.action_keys) - visible))
        return start, min(len(self.action_keys), start + visible)

    def _build_table(self) -> Table:
        """Render only the visible actions, preserving the selected row."""
        with self.lock:
            width, _ = self._get_terminal_size()
            detailed = width >= 76
            table = Table(box=None, expand=True, padding=(0, 1), pad_edge=False,
                          header_style="" if self.no_color else "dim", highlight=False)
            table.add_column("", width=1, no_wrap=True)
            table.add_column("Action", ratio=1, no_wrap=True, overflow="crop")
            table.add_column("Status", width=8, no_wrap=True)
            if width >= 40:
                table.add_column("Time", width=7, justify="right", no_wrap=True)
            if detailed:
                table.add_column("Stdout", width=7, justify="right", no_wrap=True)
                table.add_column("Stderr", width=7, justify="right", no_wrap=True)
            if self.show_dirs and width >= 100:
                table.add_column("Directory", ratio=1, no_wrap=True, overflow="crop")

            start, end = self._table_window()
            for index in range(start, end):
                action_key = self.action_keys[index]
                task = self.tasks[action_key]
                style = self._get_status_style(task.status)
                selected = index == self.selected_index
                full_label = self._action_formatter.format_label_plain(action_key, self.use_short_ids)
                label = action_key.id.name if str(action_key.context_id) == "default" else full_label
                if task.status == TaskStatus.RUNNING and task.start_time is not None:
                    duration = self._format_duration(time.time() - task.start_time)
                else:
                    duration = self._format_duration(task.duration) if task.duration is not None else "-"
                marker = ">" if self.console.options.ascii_only else SELECTION_INDICATOR
                cells = [Text(marker if selected else " "), Text(self._display_text(label)),
                         Text(self.STATUS_DISPLAY[task.status][3], style=style)]
                if width >= 40:
                    cells.append(Text(duration))
                if detailed:
                    cells.extend([Text(self._format_size(task.stdout_size)), Text(self._format_size(task.stderr_size))])
                if self.show_dirs and width >= 100:
                    cells.append(Text(self._display_text(self.action_dirs_map.get(full_label, "-"))))
                table.add_row(*cells, style="reverse" if selected and not self.no_color else "")
            return table

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
            if counts.get(TaskStatus.SKIPPED, 0) > 0:
                parts.append(f"{counts[TaskStatus.SKIPPED]} skipped")
            if counts.get(TaskStatus.CANCELLED, 0) > 0:
                parts.append(f"{counts[TaskStatus.CANCELLED]} cancelled")

            return Text(" | ".join(parts) if parts else "No tasks")

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
        """Keep the exit/back key visible even when other hints do not fit."""
        width, _ = self._get_terminal_size()
        if self._input_action is not None:
            available = max(1, width - 1)
            prefix = Text(self._display_text(f"{self._input_action.id.name} > "))
            prefix.truncate(available // 3, overflow="crop")
            before = self._display_text(self._input_text[:self._input_cursor])
            after = self._display_text(self._input_text[self._input_cursor:])
            room = max(0, available - prefix.cell_len - 1)
            start = len(before)
            occupied = 0
            while start and occupied + cell_len(before[start - 1]) <= room:
                start -= 1
                occupied += cell_len(before[start])
            tail = Text(after)
            tail.truncate(room - occupied, overflow="crop")
            line = prefix + Text(before[start:] + "|", no_wrap=True) + tail
            hint = self._input_message or "Enter send / Esc back / Ctrl+D EOF"
            if line.cell_len + cell_len(hint) + 2 <= available:
                line.append("  " + self._display_text(hint))
            line.no_wrap = True
            line.overflow = "crop"
            return line
        input_hint = "  i input" if self._get_input_target() is not None else ""
        if self.state == ViewState.TABLE:
            ending = "q close" if self.execution_complete else "q kill"
            hints = [f"{ending}  j/k select  Enter stdout  e stderr  m meta  o output  s source{input_hint}  PgUp/PgDn scroll  Home/End",
                     f"{ending}  j/k select  Enter logs  e/m/o/s views{input_hint}  PgUp/PgDn scroll",
                     f"{ending}  j/k  Enter logs{input_hint}  PgUp/PgDn", f"{ending}  j/k  Enter logs{input_hint}", ending]
        else:
            task = self._get_selected_task()
            position = ""
            if task is not None:
                scroll = self._get_scroll_state(task.action_key, self.state)
                last = min(scroll.offset + self._get_content_height(), scroll.total_lines)
                first = scroll.offset + 1 if scroll.total_lines else 0
                position = f"  {first}-{last}/{scroll.total_lines}"
                if scroll.at_end and self.state in (ViewState.LOGS_STDOUT, ViewState.LOGS_STDERR):
                    position += " live"
            hints = ["q back  j/k scroll  d/u half  PgUp/PgDn page  gg/G top/end  r refresh" + input_hint + position,
                     "q back  j/k scroll  PgUp/PgDn  gg/G" + input_hint + position,
                     "q back  j/k" + position, "q back"]
        return self._footer_with_feedback(hints)

    def _footer_with_feedback(self, hints: list[str]) -> Text:
        width, _ = self._get_terminal_size()
        if self._input_message:
            feedback = " / " + self._display_text(self._input_message)
            label = next((hint + feedback for hint in hints[:-1] if cell_len(hint + feedback) <= width), None)
            if label is None:
                navigation = next((hint for hint in reversed(hints[:-1]) if cell_len(hint) <= width), hints[-1])
                label = navigation + feedback
        else:
            label = next((hint for hint in hints if cell_len(hint) <= width), hints[-1])
        footer = Text(label, style="" if self.no_color else "dim", no_wrap=True, overflow="crop")
        footer.truncate(width, overflow="crop")
        return footer

    def _build_detail_content(self) -> RenderableType:
        """Build content for detail views with syntax highlighting."""
        task = self._get_selected_task()
        if not task or not task.action_dir:
            return Text("(no action directory)")

        visible_height = self._get_content_height()

        # Determine content source and type
        content: str = ""
        lexer: Optional[str] = None
        preserve_ansi: bool = False

        if self.state == ViewState.LOGS_STDOUT:
            log_path = task.action_dir / "stdout.log"
            preserve_ansi = True
            if log_path.exists():
                try:
                    content = log_path.read_text(encoding="utf-8")
                except Exception:
                    content = "(error reading file)"
        elif self.state == ViewState.LOGS_STDERR:
            log_path = task.action_dir / "stderr.log"
            preserve_ansi = True
            if log_path.exists():
                try:
                    content = log_path.read_text(encoding="utf-8")
                except Exception:
                    content = "(error reading file)"
        elif self.state == ViewState.META:
            meta_path = task.action_dir / "meta.json"
            if meta_path.exists():
                lexer = "json"
                try:
                    data = json.loads(meta_path.read_text(encoding="utf-8"))
                    content = json.dumps(data, indent=2)
                except Exception as e:
                    content = f"(error: {e})"
            else:
                content = "(meta.json not found)"
        elif self.state == ViewState.OUTPUT:
            output_path = task.action_dir / "output.json"
            if output_path.exists():
                lexer = "json"
                try:
                    data = json.loads(output_path.read_text(encoding="utf-8"))
                    content = json.dumps(data, indent=2)
                except Exception as e:
                    content = f"(error: {e})"
            else:
                content = "(output.json not found)"
        elif self.state == ViewState.SOURCE:
            for ext, ext_lexer in [(".py", "python"), (".sh", "bash")]:
                script_path = task.action_dir / f"script{ext}"
                if script_path.exists():
                    lexer = ext_lexer
                    try:
                        content = script_path.read_text(encoding="utf-8")
                    except Exception as e:
                        content = f"(error: {e})"
                    break
            else:
                content = "(script not found)"

        if not content:
            content = "(empty)"

        content = self._display_text(content)
        lines = content.splitlines()
        total_lines = len(lines)

        # Use Syntax for highlighted content (json, python, bash) - scroll by logical lines
        if lexer and not self.no_color:
            if self.WRAP_HIGHLIGHTED_CONTENT:
                text = Syntax(content, lexer, background_color="default").highlight(content)
                return self._render_text_lines(task, list(text.split("\n")), True)
            scroll_state = self._update_scroll_state(task.action_key, self.state, total_lines, visible_height)
            start = scroll_state.offset
            end = start + visible_height
            return Syntax(
                content,
                lexer,
                line_numbers=True,
                line_range=(start + 1, end),
                start_line=1,
                word_wrap=False,
                background_color="default",
            )

        text_lines = [Text.from_ansi(line) for line in lines]
        if self.no_color or not preserve_ansi:
            text_lines = [Text(line.plain) for line in text_lines]
        return self._render_text_lines(task, text_lines, True)

    def _render_text_lines(self, task: TaskState, lines: list[Text], show_line_numbers: bool) -> Text:
        """Wrap styled text using the same source anchors as live logs."""
        term_width, _ = self._get_terminal_size()
        line_num_width = max(4, len(str(len(lines))))
        prefix_width = line_num_width + 3 if show_line_numbers else 0
        content_width = max(1, term_width - self.CONTENT_HORIZONTAL_PADDING - prefix_width)

        # Build visual lines: list of (logical_line_num or None for continuation, text_content)
        visual_lines: list[tuple[Optional[int], Text]] = []
        line_anchors: list[tuple[int, int]] = []
        for logical_idx, line_text in enumerate(lines):
            # Wrap the line to content width
            wrapped = line_text.wrap(self.console, content_width) if line_text.plain else [Text("")]
            char_offset = 0
            for wrap_idx, wrapped_part in enumerate(wrapped):
                line_num = (logical_idx + 1) if wrap_idx == 0 else None
                visual_lines.append((line_num, wrapped_part))
                line_anchors.append((logical_idx, char_offset))
                char_offset += len(wrapped_part.plain)
        return self._render_visual_lines(task, visual_lines, line_anchors, line_num_width, show_line_numbers)

    def _render_visual_lines(self, task: TaskState, visual_lines: list[tuple[Optional[int], Text]],
                             line_anchors: list[tuple[int, int]], line_num_width: int,
                             show_line_numbers: bool) -> Text:
        """Apply the common anchored viewport to wrapped content."""
        visible_height = self._get_content_height()
        sep = "|" if IS_WINDOWS or self.no_color or self.console.options.ascii_only else "│"
        dim_style = "" if self.no_color else "dim"
        wrap_marker = ":" if IS_WINDOWS or self.no_color or self.console.options.ascii_only else "┆"
        total_visual = len(visual_lines)
        scroll_state = self._get_scroll_state(task.action_key, self.state)
        if not scroll_state.at_end and scroll_state.line_anchors:
            if scroll_state.anchor is None:
                scroll_state.anchor = scroll_state.line_anchors[min(scroll_state.offset, len(scroll_state.line_anchors) - 1)]
            scroll_state.offset = max(0, bisect_right(line_anchors, scroll_state.anchor) - 1)
        scroll_state = self._update_scroll_state(task.action_key, self.state, total_visual, visible_height)
        scroll_state.line_anchors = line_anchors

        start = scroll_state.offset
        end = start + visible_height
        visible = visual_lines[start:end]

        result = Text()
        for i, (line_num, line_content) in enumerate(visible):
            if i > 0:
                result.append("\n")

            if not show_line_numbers:
                result.append_text(line_content)
                continue
            if line_num is not None:
                result.append(f"{line_num:{line_num_width}} ", style=dim_style)
                result.append(f"{sep} ", style=dim_style)
            else:
                result.append(" " * line_num_width + " ", style=dim_style)
                result.append(f"{wrap_marker} ", style=dim_style)

            result.append_text(line_content)

        return result

    def _build_renderable(self) -> Group:
        """Compose compact views bounded by the terminal viewport."""
        with self.lock:
            width, height = self._get_terminal_size()
            if height < self.FRAME_ROWS or width < 24:
                task = self._get_selected_task()
                label = task.action_key.id.name if task is not None else "No actions"
                lines = [self._build_footer()]
                if height > 1:
                    lines.insert(0, Text(self._display_text(label), no_wrap=True, overflow="crop"))
                return Group(*lines)
            if self.state == ViewState.TABLE:
                summary = self._build_text_status_header()
                start, end = self._table_window()
                if len(self.action_keys) > end - start:
                    summary.append(f"  |  {start + 1}-{end}/{len(self.action_keys)}")
                title = "mudyla / Actions"
                content: RenderableType = self._build_table()
            else:
                title = "mudyla / " + self._build_header()
                task = self._get_selected_task()
                summary = Text("No action selected")
                if task is not None:
                    summary = Text(self.STATUS_DISPLAY[task.status][3], style=self._get_status_style(task.status))
                    summary.append(f"  stdout {self._format_size(task.stdout_size)}  stderr {self._format_size(task.stderr_size)}")
                content = self._build_detail_content()
            summary.no_wrap = True
            summary.overflow = "crop"
            directory = Text("")
            if self.show_dirs and self.state == ViewState.TABLE and width < 100:
                task = self._get_selected_task()
                if task is not None:
                    label = self._action_formatter.format_label_plain(task.action_key, self.use_short_ids)
                    directory = Text(self._display_text(self.action_dirs_map.get(label, "-")), no_wrap=True, overflow="crop")
            panel = Panel(Group(summary, directory, content), title=Text(self._display_text(title)[:width - 8]), title_align="left",
                          border_style="" if self.no_color else "dim", padding=(0, 1),
                          width=width, safe_box=True,
                          box=box.ASCII if IS_WINDOWS or self.console.options.ascii_only else box.ROUNDED)
            return Group(panel, self._build_footer())

    # =========================================================================
    # Main Loop
    # =========================================================================

    def _main_loop(self) -> None:
        """Main loop handling both input and display updates."""
        last_update = 0.0
        update_interval = 1.0 / 24.0

        while not self.stop_flag:
            with self.lock:
                if self._input_action is not None and self.tasks[self._input_action].status != TaskStatus.RUNNING:
                    self._input_action = None
                    self._input_message = self._input_message or "Action finished"
            key = self._read_key()
            if key:
                if key == "terminal_eof":
                    self._input_enabled = False
                    self._handle_key_table("q")
                    break
                if key in {"wheel_up", "wheel_down"}:
                    if self.state == ViewState.TABLE:
                        self._handle_key_table(key)
                    else:
                        self._handle_key_scroll(key)
                elif self._input_action is not None:
                    self._handle_input_key(key)
                elif key == "i":
                    self._handle_key_table(key)
                elif self.state == ViewState.TABLE:
                    if self._handle_key_table(key):
                        break
                else:
                    self._handle_key_scroll(key)

                self._refresh_display()
                last_update = time.time()
                continue

            now = time.time()
            if now - last_update >= update_interval:
                last_update = now
                self._refresh_display()

            time.sleep(0.01)

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def show_failure(self, action_key: ActionKey, result: "ActionResult", run_directory: Path, suppress_output: bool) -> None:
        legacy_failure(self._output, result, run_directory, suppress_output, False)

    def start(self) -> None:
        """Start the interactive display."""
        self.stop_flag = False
        self._setup_terminal()
        try:
            self._refresh_display()
        except BaseException:
            self.stop_flag = True
            live, self.live = self.live, None
            try:
                # Discard a failed encoded frame before emitting restoration controls.
                with self.console.capture():
                    pass
                self._set_mouse_capture(False)
                if live is not None:
                    live.update(Text(""))
                    live.stop()
            finally:
                self._restore_terminal()
            raise

        self._main_thread = threading.Thread(target=self._main_loop, daemon=True)
        self._main_thread.start()

    def _refresh_display(self) -> None:
        with self.lock:
            if self.live is None:
                self.live = Live(self._build_renderable(), console=self.console, screen=self.ALTERNATE_SCREEN,
                                 refresh_per_second=24, transient=False, auto_refresh=False,
                                 vertical_overflow="crop")
                self.live.start()
            self._set_mouse_capture(True)
            self.live.update(self._build_renderable(), refresh=True)

    def stop(self) -> None:
        """Stop the interactive display.

        Thread-safe and idempotent: may be called multiple times from
        different threads (e.g. timeout timer thread and main execution
        thread). Only the first call performs the actual shutdown.
        """
        self.stop_flag = True

        if self._main_thread is not None and self._main_thread.is_alive():
            self._main_thread.join(timeout=1.0)

        self._set_mouse_capture(False)
        self._restore_terminal()

        # Atomically claim the Live instance so only one thread performs shutdown
        with self.lock:
            self.mark_execution_complete()
            self.state = ViewState.TABLE
            live = self.live
            self.live = None

        if live:
            try:
                live.update(self._build_renderable(), refresh=True)
            except BaseException:
                with self.console.capture():
                    pass
                live.update(Text(""))
                raise
            finally:
                live.stop()
            if self.ALTERNATE_SCREEN:
                self.console.print(self._build_renderable())

    def wait_for_quit(self) -> None:
        """Wait for user to quit (call after execution completes with --it)."""
        if not self.keep_running or not self.uses_terminal_input():
            return

        self.mark_execution_complete()

        try:
            if self._main_thread is not None:
                self._main_thread.join()
        finally:
            self.stop()
