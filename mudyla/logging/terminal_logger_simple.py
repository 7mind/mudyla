"""Append-only lifecycle events with failure captures on demand."""

from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from rich.text import Text

from ..dag.graph import ActionKey
from .terminal_logger import LoggerMode, TerminalLogger
from .formatters.details import context_label, duration_text, literal_text
from .formatters.failure import failure_summary
from .formatters.sections import heading

if TYPE_CHECKING:
    from ..executor.engine import ActionResult


class SimpleTerminalLogger(TerminalLogger):
    MODE = LoggerMode.SIMPLE

    def _initialize_actions(self) -> None:
        assert self.execution_order is not None
        self._action_keys = self.execution_order
        self._use_short_ids = self.use_short_ids
        self._kill_callback: Optional[Callable[[], None]] = None
        self._kill_requested = False

    def _identity(self, action_key: ActionKey) -> Text:
        identity = Text.assemble(literal_text(action_key.id.name, "cyan"),
                                 context_label(action_key.context_id, self._output.context, self._use_short_ids))
        identity.stylize("not dim not bold")
        return identity

    def _event(self, state: str, action_key: ActionKey, duration: float) -> None:
        self._emit_marker(action_key, Text.assemble(self._identity(action_key), ": ",
                           (f"{state} ({duration_text(duration)})", "red not dim not bold" if state == "Failed" else "green not dim not bold")))

    def begin_action(self, action_key: ActionKey, command: list[str]) -> None:
        self._emit_marker(action_key, Text.assemble(self._identity(action_key), ": ",
                           ("Running command", "yellow not dim not bold"), " ",
                           literal_text("`" + " ".join(command) + "`", "blue not dim not bold")))

    def _emit_marker(self, action_key: ActionKey, text: Text) -> None:
        self._output.console.print(text, soft_wrap=True, highlight=False)

    def mark_running(self, action_key: ActionKey, action_dir: Optional[Path] = None) -> None:
        pass

    def mark_done(self, action_key: ActionKey, duration: float) -> None:
        self._event("Finished", action_key, duration)

    def mark_failed(self, action_key: ActionKey, duration: float) -> None:
        self._event("Failed", action_key, duration)

    def mark_restored(self, action_key: ActionKey, duration: float, action_dir: Optional[Path] = None) -> None:
        self._event("Restored", action_key, duration)

    def show_failure(self, action_key: ActionKey, result: "ActionResult", run_directory: Path, suppress_output: bool) -> None:
        failure_summary(self._output, result, run_directory)
        if suppress_output:
            self._output.print(Text("Output suppressed; use --verbose or inspect the log files.", style="dim"))
        elif result.stdout_path.exists():
            self._output.print(heading("Captured output:"))
            self._output.print_raw(result.stdout_path.read_text(encoding="utf-8"))

    def update_output_sizes(self, action_key: ActionKey, stdout_size: int, stderr_size: int) -> None:
        pass

    def set_kill_callback(self, callback: Callable[[], None]) -> None:
        self._kill_callback = callback

    def is_kill_requested(self) -> bool:
        return self._kill_requested

    def start(self) -> None:
        self._output.print(heading("Actions:"))

    def stop(self) -> None:
        pass

    def wait_for_quit(self) -> None:
        pass
