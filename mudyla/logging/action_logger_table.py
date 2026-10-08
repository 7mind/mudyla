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
from rich.align import Align
from rich.cells import cell_len
from rich.console import Console, Group, RenderableType
from rich.control import Control
from rich.live import Live
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.style import Style
from rich.segment import Segment, Segments

from ..dag.graph import ActionKey
from .formatters import OutputFormatter
from .action_logger import ActionLogger
from .formatters.failure import legacy_failure
from .formatters.details import action_label, context_label
from .formatters.sections import heading
from .terminal_background import BACKGROUND_QUERY, BackgroundProbe

if TYPE_CHECKING:
    from ..executor.engine import ActionResult
from .windows_mouse import WindowsMouseInput

# Cross-platform terminal handling
IS_WINDOWS = sys.platform == "win32"

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


class InlineDisplay:
    """Keep the mutable frame in one logical terminal line across reflow."""

    def __init__(self, renderable: RenderableType, console: Console):
        self.console = console
        self.renderable = renderable
        self.started = False
        self.transient = True

    def start(self) -> None:
        self.started = True
        self.console.show_cursor(False)

    def update(self, renderable: RenderableType, refresh: bool) -> None:
        self.renderable = renderable
        if not refresh or not self.started:
            return
        width, height = self.console.size
        rows = self.console.render_lines(renderable, pad=False)[:height]
        segments = [segment for index, row in enumerate(rows)
                    for segment in Segment.adjust_line_length(row, width, pad=index < len(rows) - 1)]
        self.console.file.write("\x1b[J")
        self.console.file.flush()
        self.console.print(Segments(segments), end="", soft_wrap=True)
        self.console.control(Control.move_to_column(0), Control.move(y=1 - len(rows)))

    def stop(self) -> None:
        if not self.started:
            return
        self.started = False
        try:
            self.console.file.write("\x1b[J")
            self.console.file.flush()
        finally:
            self.console.show_cursor(True)


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
    TABLE_FRAME_ROWS = 7
    OVERVIEW_BOTTOM_ROWS = 1
    CONTENT_HORIZONTAL_PADDING = 4
    WRAP_HIGHLIGHTED_CONTENT = False
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
        run_info: Optional[RenderableType] = None,
        fullscreen: bool = False,
    ):
        self.no_color = no_color
        self.show_dirs = show_dirs
        self.run_directory = run_directory
        self.keep_running = keep_running
        self.fullscreen = fullscreen or keep_running
        self.action_dirs_map = action_dirs or {}
        self.use_short_ids = use_short_ids
        self._run_info = run_info
        self._prefix_cache_key: Optional[tuple[int, str, bool]] = None
        self._prefix_lines: list[list[Segment]] = []
        self._overview_offset = 0
        self._overview_initialized = False
        self._overview_prefix_length = 0
        self._overview_height = 0
        self._action_anchors: dict[ActionKey, int] = {}

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
        self._screen_active = False
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
        self.live: Optional[Live | InlineDisplay] = None
        self._main_thread: Optional[threading.Thread] = None
        self._display_error: Optional[BaseException] = None

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
        enabled = enabled and self._screen_active and self.uses_terminal_input()
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
            if self._overview_is_scrollable():
                rows = self._overview_rows()
                height = self._overview_height
                if key in {"top", "bottom", "page_up", "page_down", "half_up", "half_down", "wheel_up", "wheel_down"}:
                    maximum = max(0, len(rows) - height)
                    if key == "top":
                        self._overview_offset = 0
                    elif key == "bottom":
                        self._overview_offset = maximum
                    else:
                        amount = self.MOUSE_WHEEL_ROWS if key.startswith("wheel") else max(1, height // 2) if key.startswith("half") else height
                        self._overview_offset += -amount if key.endswith("up") else amount
                        self._overview_offset = max(0, min(maximum, self._overview_offset))
                    return False
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
            if self._overview_is_scrollable() and key in {"up", "down"} and self.action_keys:
                row = self._overview_prefix_length + self._action_anchors[self.action_keys[self.selected_index]]
                self._overview_offset = min(self._overview_offset, row)
                self._overview_offset = max(self._overview_offset, row - self._overview_height + 1 + self.OVERVIEW_BOTTOM_ROWS)
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
        width, height = self._get_terminal_size()
        if self.fullscreen and not self.stop_flag:
            start = max(0, min(len(self.action_keys), self._overview_offset - self._overview_prefix_length))
            header_rows = max(0, self._overview_prefix_length - self._overview_offset)
            visible = max(0, self._overview_height - self.OVERVIEW_BOTTOM_ROWS - header_rows)
            return start, min(len(self.action_keys), start + visible)
        directory_rows = int(self.show_dirs and width < 100)
        visible = max(1, height - self.TABLE_FRAME_ROWS - directory_rows)
        start = max(0, min(self.selected_index - visible // 2, len(self.action_keys) - visible))
        return start, min(len(self.action_keys), start + visible)

    def _build_table(self) -> Table:
        """Render only the visible actions, preserving the selected row."""
        with self.lock:
            width, _ = self._get_terminal_size()
            detailed = width >= 76
            table = Table(box=box.ASCII if self.console.options.ascii_only else box.ROUNDED,
                          padding=(0, 1), header_style="dim", border_style="dim", highlight=False, safe_box=True)
            table.add_column("", width=1, no_wrap=True)
            table.add_column("Action", no_wrap=True, overflow="crop" if self.console.options.ascii_only else "ellipsis")
            table.add_column("Status", width=8, no_wrap=True)
            if width >= 40:
                table.add_column("Time", justify="right", no_wrap=True)
            if detailed:
                table.add_column("Stdout", justify="right", no_wrap=True)
                table.add_column("Stderr", justify="right", no_wrap=True)
            if self.show_dirs and width >= 100:
                table.add_column("Directory", no_wrap=True, overflow="crop" if self.console.options.ascii_only else "ellipsis")

            start, end = self._table_window()
            rows: list[list[Text]] = []
            for index in range(start, end):
                action_key = self.action_keys[index]
                task = self.tasks[action_key]
                style = self._get_status_style(task.status)
                selected = index == self.selected_index
                full_label = self._action_formatter.format_label_plain(action_key, self.use_short_ids)
                label = (Text(action_key.id.name, style="bold") if str(action_key.context_id) == "default" else
                         action_label(action_key, self._context_formatter, self.use_short_ids, True))
                label.stylize("dim not bold", len(action_key.id.name) + 1)
                label.plain = self._display_text(label.plain)
                if task.status == TaskStatus.RUNNING and task.start_time is not None:
                    duration = self._format_duration(time.time() - task.start_time)
                else:
                    duration = self._format_duration(task.duration) if task.duration is not None else "-"
                cells = [Text(">" if selected else " ", style="dim"), label,
                         Text(self.STATUS_DISPLAY[task.status][3], style=style)]
                if width >= 40:
                    cells.append(Text(duration, style="dim"))
                if detailed:
                    cells.extend([Text(self._format_size(task.stdout_size)), Text(self._format_size(task.stderr_size))])
                if self.show_dirs and width >= 100:
                    cells.append(Text(self._display_text(self.action_dirs_map.get(full_label, "-"))))
                rows.append(cells)

            flexible = [1]
            if self.show_dirs and width >= 100:
                flexible.append(len(table.columns) - 1)
            for index, column in enumerate(table.columns):
                if index not in flexible and column.width is None:
                    column.width = max([cell_len(str(column.header)), *(row[index].cell_len for row in rows)])
            # Reserve both borders, column dividers, and two padding cells per column before label widths.
            available = width - (3 * len(table.columns) + 1) - sum(column.width or 0 for column in table.columns)
            natural = [max([cell_len(str(table.columns[index].header)), *(row[index].cell_len for row in rows)])
                       for index in flexible]
            allocated = [min(size, available // len(flexible)) for size in natural]
            remaining = available - sum(allocated)
            for index, size in enumerate(natural):
                extra = min(size - allocated[index], remaining)
                allocated[index] += extra
                remaining -= extra
                table.columns[flexible[index]].max_width = allocated[index]

            for key, cells in zip(self.action_keys[start:end], rows):
                if cells[1].cell_len > allocated[0] and str(key.context_id) != "default":
                    identity = context_label(key.context_id, self._context_formatter, self.use_short_ids)
                    identity.plain = self._display_text(identity.plain)
                    identity.stylize("dim not bold")
                    name_width = max(0, allocated[0] - identity.cell_len - 1)
                    name = Text(self._display_text(key.id.name), style="bold")
                    name.truncate(name_width, overflow="crop" if self.console.options.ascii_only else "ellipsis")
                    cells[1] = name + Text(" " if name_width else "") + identity
                table.add_row(*cells)
            if not self.fullscreen or self.stop_flag:
                table.caption = self._build_progress_caption()
            table.caption_justify = "left"
            return table

    def _build_progress_caption(self) -> Text:
        summary = self._build_text_status_header()
        start, end = self._table_window()
        if end > start and len(self.action_keys) > end - start:
            summary.append(f"  |  {start + 1}-{end}/{len(self.action_keys)}")
        summary.stylize("dim")
        summary.no_wrap = True
        summary.overflow = "crop"
        return summary

    def _render_table(self) -> Segments:
        lines = self.console.render_lines(self._build_table(), pad=False)
        selection_style = self._selection_style()
        if selection_style is not None and self.action_keys:
            start, end = self._table_window()
            if start <= self.selected_index < end:
                row = 3 + self.selected_index - start  # Top border, headings, and heading separator.
                lines[row] = [Segment(segment.text, (segment.style or Style()) + selection_style, segment.control)
                              for segment in lines[row]]
        return Segments(segment for line in lines for segment in [*line, Segment.line()])

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
            minimum_height = (self.TABLE_FRAME_ROWS + 1 + int(self.show_dirs and width < 100)
                              if self.state == ViewState.TABLE else self.FRAME_ROWS)
            if height < minimum_height or width < 24:
                task = self._get_selected_task()
                label = task.action_key.id.name if task is not None else "No actions"
                lines = [self._build_footer()]
                if height > 1:
                    lines.insert(0, Text(self._display_text(label), no_wrap=True, overflow="crop"))
                return Group(*lines)
            if self.state == ViewState.TABLE:
                rows: list[RenderableType]
                if self.fullscreen and not self.stop_flag:
                    rows = [self._overview_content(), self._build_progress_caption()]
                else:
                    rows = [heading("Actions:"), self._render_table()]
                if self.show_dirs and width < 100:
                    task = self._get_selected_task()
                    if task is not None:
                        label = self._action_formatter.format_label_plain(task.action_key, self.use_short_ids)
                        rows.append(Text(self._display_text(self.action_dirs_map.get(label, "-")),
                                         no_wrap=True, overflow="crop"))
                rows.append(self._build_footer())
                return Group(*rows)
            title = "mudyla / " + self._build_header()
            task = self._get_selected_task()
            summary = Text("No action selected")
            if task is not None:
                summary = Text(self.STATUS_DISPLAY[task.status][3], style=self._get_status_style(task.status))
                summary.append(f"  stdout {self._format_size(task.stdout_size)}  stderr {self._format_size(task.stderr_size)}")
            content = self._build_detail_content()
            summary.no_wrap = True
            summary.overflow = "crop"
            panel = Panel(Group(summary, Text(""), content), title=Text(self._display_text(title)[:width - 8]), title_align="left",
                          border_style="" if self.no_color else "dim", padding=(0, 1),
                          width=width, safe_box=True,
                          box=box.ASCII if IS_WINDOWS or self.console.options.ascii_only else box.ROUNDED)
            return Group(panel, self._build_footer())

    def _overview_is_scrollable(self) -> bool:
        return self.fullscreen

    def _preparation_renderable(self) -> RenderableType:
        return self._run_info if self._run_info is not None else Group()

    def _cached_preparation(self) -> list[list[Segment]]:
        cache_key = (self.console.width, self.console.encoding, self.console.options.ascii_only)
        if cache_key != self._prefix_cache_key:
            lines = self.console.render_lines(self._preparation_renderable(), pad=False)
            self._prefix_lines = [[Segment(segment.text.encode(self.console.encoding, errors="replace").decode(self.console.encoding),
                                           segment.style, segment.control) for segment in line] for line in lines]
            self._prefix_cache_key = cache_key
        return self._prefix_lines

    def _overview_rows(self) -> list[list[Segment]]:
        width, terminal_height = self._get_terminal_size()
        height = max(1, terminal_height - 2 - int(self.show_dirs and width < 100))
        resized = height != self._overview_height or (self._prefix_cache_key is not None and self._prefix_cache_key[0] != width)
        previous_row = self._overview_prefix_length + self.selected_index
        relative = previous_row - self._overview_offset
        prefix = self._cached_preparation() + self.console.render_lines(heading("Actions:"), pad=False)
        self._overview_prefix_length = len(prefix) + 3
        selected_row = self._overview_prefix_length + self.selected_index
        if not self._overview_initialized:
            self._overview_offset = max(len(self._prefix_lines), selected_row - height + 1 + self.OVERVIEW_BOTTOM_ROWS)
            self._overview_initialized = True
        elif 0 <= relative < self._overview_height:
            self._overview_offset = selected_row - min(relative, height - 1 - self.OVERVIEW_BOTTOM_ROWS)
        maximum = max(0, self._overview_prefix_length + len(self.action_keys) + self.OVERVIEW_BOTTOM_ROWS - height)
        actions_height = self._overview_prefix_length - len(self._prefix_lines) + len(self.action_keys) + self.OVERVIEW_BOTTOM_ROWS
        if resized and 0 <= relative < self._overview_height and actions_height <= height:
            self._overview_offset = maximum
        self._overview_offset = max(0, min(maximum, self._overview_offset))
        self._overview_height = height
        self._action_anchors = {key: index for index, key in enumerate(self.action_keys)}
        start, end = self._table_window()
        lines = self.console.render_lines(self._render_table(), pad=False)
        rows = prefix + lines[:3] + [[]] * start + lines[3:3 + end - start] + [[]] * (len(self.action_keys) - end) + lines[3 + end - start:]
        visible_end = self._overview_offset + height
        if self._overview_prefix_length <= visible_end < len(rows):
            rows[visible_end - 1] = lines[-1]
        return rows

    def _overview_content(self) -> Group:
        rows = self._overview_rows()
        visible = rows[self._overview_offset:self._overview_offset + self._overview_height]
        segments = Segments([segment for row in visible for segment in [*row, Segment.line()]])
        return Group(Align(segments, height=self._overview_height) if self.fullscreen else segments)

    # =========================================================================
    # Main Loop
    # =========================================================================

    def _selection_style(self) -> Optional[Style]:
        return (self._background_probe.selection_style(self.console.color_system)
                if self._background_probe is not None and not self.no_color and not self.console.no_color else None)

    def _probe_terminal_background(self) -> None:
        if (self._background_probe is None and self._input_enabled
                and (self._terminal_active or self._windows_mouse is not None)
                and sys.stdin.isatty() and self.console.file.isatty()
                and not self.no_color and not self.console.no_color
                and not self.console.is_dumb_terminal and os.environ.get("TERM") not in {"dumb", "unknown"}
                and self.console.color_system in {"truecolor", "256"}):
            self._background_probe = BackgroundProbe(time.monotonic())
            self.console.file.write(BACKGROUND_QUERY)
            self.console.file.flush()

    def _main_loop(self) -> None:
        try:
            self._run_main_loop()
        except BaseException as error:
            self._display_error = error
            self.stop_flag = True
            self.kill_requested = True
            try:
                self._drain_background_reply()
            except BaseException as input_error:
                error.add_note(f"Terminal reply drain failed: {input_error}")
            with self.lock:
                self._cleanup_after_error(error)
            if self._kill_callback is not None:
                self._kill_callback()

    def _run_main_loop(self) -> None:
        """Main loop handling both input and display updates."""
        if not self.stop_flag:
            self._probe_terminal_background()
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

        self._drain_background_reply()

    def _drain_background_reply(self) -> None:
        while (self._background_probe is not None and self._background_probe.pending(time.monotonic())
               and self._input_enabled):
            if self._read_key() == "terminal_eof":
                self._input_enabled = False
                break
            time.sleep(0.001)

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def show_failure(self, action_key: ActionKey, result: "ActionResult", run_directory: Path, suppress_output: bool) -> None:
        legacy_failure(self._output, result, run_directory, suppress_output, False)

    def start(self) -> None:
        """Start the interactive display."""
        self.stop_flag = False
        self._display_error = None
        self._setup_terminal()
        try:
            self._refresh_display()
        except BaseException as error:
            self.stop_flag = True
            self._cleanup_after_error(error)
            raise

        self._main_thread = threading.Thread(target=self._main_loop, daemon=True)
        self._main_thread.start()

    def _refresh_display(self) -> None:
        with self.lock:
            if self.stop_flag:
                return
            fullscreen = self.fullscreen or self.state != ViewState.TABLE
            screen = fullscreen and self.console.is_terminal and not self.console.legacy_windows
            frame = self._build_renderable()
            if self.live is not None and self._screen_active != screen:
                self._release_live(False)
            if self.live is None:
                if not screen and self.console.is_terminal and not self.console.legacy_windows and not self.console.is_dumb_terminal:
                    self.live = InlineDisplay(frame, self.console)
                else:
                    self.live = Live(frame, console=self.console, screen=screen,
                                     refresh_per_second=24, transient=False, auto_refresh=False,
                                     vertical_overflow="crop")
                self._screen_active = screen
                self.live.start()
            self._set_mouse_capture(True)
            self.live.update(frame, refresh=True)

    def _release_live(self, discard: bool) -> None:
        live, self.live = self.live, None
        try:
            self._set_mouse_capture(False)
        finally:
            try:
                if live is not None:
                    live.transient = True
                    if discard:
                        with self.console.capture():
                            pass
                        live.update(Text(""), refresh=False)
                    try:
                        live.stop()
                    except BaseException as error:
                        # Live may fail before installing the hook that its stop requires.
                        restorations = [lambda: self.console.show_cursor(True)]
                        if self._screen_active:
                            restorations.append(lambda: self.console.set_alt_screen(False))
                        for restore in restorations:
                            try:
                                restore()
                            except BaseException as restore_error:
                                error.add_note(f"Terminal display restoration failed: {restore_error}")
                        raise
            finally:
                self._screen_active = False

    def _cleanup_after_error(self, error: BaseException) -> None:
        for cleanup in (lambda: self._release_live(True), self._restore_terminal):
            try:
                cleanup()
            except BaseException as cleanup_error:
                error.add_note(f"Terminal cleanup failed: {cleanup_error}")

    def stop(self) -> None:
        """Stop the interactive display.

        Thread-safe and idempotent: may be called multiple times from
        different threads (e.g. timeout timer thread and main execution
        thread). Only the first call performs the actual shutdown.
        """
        while (self._background_probe is not None and self._background_probe.pending(time.monotonic())
               and self._main_thread is not None and self._main_thread.is_alive() and not self.stop_flag):
            time.sleep(min(0.01, max(0, self._background_probe.deadline - time.monotonic())))
        with self.lock:
            self.stop_flag = True

        if self._main_thread is not None and self._main_thread.is_alive():
            self._main_thread.join(timeout=1.0)

        with self.lock:
            self.mark_execution_complete()
            self.state = ViewState.TABLE
            try:
                if self.live is not None:
                    frame = self._build_renderable()
                    self._release_live(False)
                    self._restore_terminal()
                    self.console.print(frame)
                else:
                    self._restore_terminal()
            except BaseException as error:
                self._cleanup_after_error(error)
                raise
            display_error, self._display_error = self._display_error, None
            if display_error is not None:
                raise display_error

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
