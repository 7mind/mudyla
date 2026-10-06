"""GitHub Actions groups with the established streaming and diagnostic output."""

from pathlib import Path
from typing import TYPE_CHECKING

from ..dag.graph import ActionKey
from .action_logger_verbose import ActionLoggerVerbose
from .action_logger_simple import ActionLoggerSimple
from .formatters.failure import legacy_failure
from .formatters import OutputFormatter

if TYPE_CHECKING:
    from ..executor.engine import ActionResult


class ActionLoggerGitHub(ActionLoggerVerbose):
    def __init__(self, action_keys: list[ActionKey], output: OutputFormatter, use_short_ids: bool = True):
        super().__init__(action_keys, output, use_short_ids, parallel=False)
        self._open_groups: set[ActionKey] = set()

    def begin_action(self, action_key: ActionKey, command: list[str]) -> None:
        with self._stream_lock:
            self._finish_lines()
            self._output.print_raw(f"::group::{action_key.id.name}")
            self._open_groups.add(action_key)
            ActionLoggerSimple.begin_action(self, action_key, command)

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
