"""GitHub Actions groups with the established streaming and diagnostic output."""

from pathlib import Path
from typing import TYPE_CHECKING

from ..dag.graph import ActionKey
from .terminal_logger_verbose import VerboseTerminalLogger
from .terminal_logger_simple import SimpleTerminalLogger
from .terminal_logger import LoggerMode
from .formatters.failure import legacy_failure

if TYPE_CHECKING:
    from ..executor.engine import ActionResult


class GitHubTerminalLogger(VerboseTerminalLogger):
    MODE = LoggerMode.GITHUB

    def _initialize_actions(self) -> None:
        super()._initialize_actions()
        self._parallel = False
        self._open_groups: set[ActionKey] = set()

    def begin_action(self, action_key: ActionKey, command: list[str]) -> None:
        with self._stream_lock:
            self._finish_lines()
            self._output.print_raw(f"::group::{action_key.id.name}")
            self._open_groups.add(action_key)
            SimpleTerminalLogger.begin_action(self, action_key, command)

    def end_action(self, action_key: ActionKey) -> None:
        with self._stream_lock:
            self._close_group(action_key)

    def _close_group(self, action_key: ActionKey) -> None:
        if action_key in self._open_groups:
            self._open_groups.remove(action_key)
            self._finish_lines()
            self._output.print_raw("::endgroup::")

    def finalize(self) -> None:
        with self._stream_lock:
            for action_key in list(self._open_groups):
                self._close_group(action_key)

    def show_failure(self, action_key: ActionKey, result: "ActionResult", run_directory: Path, suppress_output: bool) -> None:
        legacy_failure(self._output, result, run_directory, False, False)
