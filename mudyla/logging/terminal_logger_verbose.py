"""Append-only markers and immediate child output with optional action prefixes."""

from pathlib import Path
import sys
import threading
from typing import TYPE_CHECKING, Literal

from rich.color import ColorSystem
from rich.console import Console

from ..dag.graph import ActionKey
from .terminal_logger_simple import SimpleTerminalLogger
from .terminal_logger import LoggerMode
from .formatters.failure import failure_summary
from .terminal_output import StreamState, same_terminal

if TYPE_CHECKING:
    from ..executor.engine import ActionResult


class VerboseTerminalLogger(SimpleTerminalLogger):
    receives_suppressed_output = True

    MODE = LoggerMode.VERBOSE

    def _initialize_actions(self) -> None:
        super()._initialize_actions()
        self._parallel = self.parallel_execution
        self._stream_lock = threading.Lock()
        self._streams: dict[Literal["stdout", "stderr"], StreamState] = {"stdout": StreamState(), "stderr": StreamState()}
        shared = same_terminal(sys.stdout, sys.stderr)
        if shared:
            self._streams["stderr"] = self._streams["stdout"]
        self._color_ownership = shared or not (sys.stdout.isatty() and sys.stderr.isatty())

    @staticmethod
    def _supports_color(console: Console) -> bool:
        return bool(console.file.isatty() and console.is_terminal and not console.is_dumb_terminal and not console.no_color
                    and not console.legacy_windows and console.color_system in {"standard", "256", "truecolor"})

    def _prefix(self, action_key: ActionKey, stream: Literal["stdout", "stderr"], state: StreamState) -> str:
        identity = self._identity(action_key)
        console = self._output.console_for_stream(stream)
        if not self._color_ownership or state.foreground is None or not self._supports_color(console):
            return identity.plain + ": "
        systems = {"standard": ColorSystem.STANDARD, "256": ColorSystem.EIGHT_BIT, "truecolor": ColorSystem.TRUECOLOR}
        assert console.color_system is not None
        parts: list[str] = []
        for segment in identity.render(console, end=""):
            color = segment.style.color if segment.style is not None else None
            codes = color.downgrade(systems[console.color_system]).get_ansi_codes() if color is not None else ("39",)
            parts.extend(["\x1b[" + ";".join(codes) + "m", segment.text])
        parts.extend([": ", "\x1b[" + state.foreground + "m"])
        return "".join(parts)

    def _marker_written(self) -> None:
        if self._supports_color(self._output.console):
            self._streams["stdout"].foreground = "39"

    def _finish_lines(self) -> None:
        visited: set[int] = set()
        for stream, state in self._streams.items():
            if id(state) in visited:
                continue
            visited.add(id(state))
            if not state.line_start or state.control != "text":
                target = sys.stdout if stream == "stdout" else sys.stderr
                state.finish_control(target)
                target.write("\n")
                target.flush()
                state.line_start = True
                state.carriage_return = False
                state.owner = None

    def _event(self, state: str, action_key: ActionKey, duration: float) -> None:
        with self._stream_lock:
            self._finish_lines()
            super()._event(state, action_key, duration)
            self._marker_written()

    def begin_action(self, action_key: ActionKey, command: list[str]) -> None:
        with self._stream_lock:
            self._finish_lines()
            super().begin_action(action_key, command)
            self._marker_written()

    def write_output(self, action_key: ActionKey, text: str, stream: Literal["stdout", "stderr"]) -> None:
        with self._stream_lock:
            target = sys.stdout if stream == "stdout" else sys.stderr
            state = self._streams[stream]
            fragments: list[str] = []
            for char in text:
                if state.control == "text":
                    follows_cr = state.carriage_return
                    if follows_cr:
                        state.carriage_return = False
                        state.line_start = True
                    if self._parallel and char not in "\r\x1b\x90\x98\x9b\x9d\x9e\x9f" and not (follows_cr and char == "\n"):
                        if state.line_start or state.owner != action_key:
                            if not state.line_start:
                                fragments.append("\n")
                            fragments.append(self._prefix(action_key, stream, state))
                            state.owner = action_key
                fragments.append(char)
                state.consume(char)
            target.write("".join(fragments))
            target.flush()

    def show_failure(self, action_key: ActionKey, result: "ActionResult", run_directory: Path, suppress_output: bool) -> None:
        failure_summary(self._output, result, run_directory)
