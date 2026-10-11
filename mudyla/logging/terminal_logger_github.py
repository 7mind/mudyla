"""GitHub Actions groups with the established streaming and diagnostic output."""

from pathlib import Path
import sys
from typing import TYPE_CHECKING, Literal, Optional
from uuid import uuid4

from ..dag.graph import ActionKey
from .terminal_logger_verbose import VerboseTerminalLogger
from .terminal_logger_simple import SimpleTerminalLogger
from .terminal_logger import LoggerMode
from .formatters.action import action_group_names
from .formatters.failure import legacy_failure
from .formatters.details import KeyValueRow
from .formatters.result import action_result_fields
from .github import ChildCommands, REGISTERED_COMMANDS, WorkflowCommand, escape_data, resume_command

if TYPE_CHECKING:
    from ..executor.engine import ActionResult, ExecutionResult


class GitHubTerminalLogger(VerboseTerminalLogger):
    MODE = LoggerMode.GITHUB

    def _initialize_actions(self) -> None:
        super()._initialize_actions()
        self._group_names = action_group_names(self._action_keys)
        self._parallel = False
        self._streams["stderr"] = self._streams["stdout"]
        self._open_groups: dict[ActionKey, list[str]] = {}
        self._physical_group: Optional[tuple[ActionKey, str]] = None
        self._children: dict[tuple[ActionKey, str], ChildCommands] = {}
        self._stop_token: Optional[str] = None
        self._stop_owner: Optional[ActionKey] = None
        self._producer: Optional[tuple[ActionKey, Literal["stdout", "stderr"]]] = None

    def begin_action(self, action_key: ActionKey, command: list[str]) -> None:
        with self._stream_lock:
            self._finish_lines()
            self._open_groups[action_key] = []
            self._select_group(action_key)
            SimpleTerminalLogger.begin_action(self, action_key, command)

    def _select_group(self, key: ActionKey) -> None:
        if self._stop_token is not None or key not in self._open_groups:
            return
        name = " / ".join([self._group_names[key], *self._open_groups[key]])
        selected = key, name
        if self._physical_group != selected:
            self._close_physical_group()
            self._finish_lines()
            self._output.print_raw("::group::" + escape_data(name))
            self._physical_group = selected

    def _close_physical_group(self) -> None:
        if self._physical_group is not None:
            assert self._stop_token is None
            self._finish_lines()
            self._output.print_raw("::endgroup::")
            self._physical_group = None

    def _ordinary(self, key: ActionKey, text: str, stream: Literal["stdout", "stderr"]) -> None:
        self._select_group(key)
        producer = key, stream
        if self._producer != producer:
            self._finish_lines()
            self._producer = producer
        sys.stdout.write(text)
        sys.stdout.flush()
        for char in text:
            self._streams["stdout"].consume(char)

    def _command(self, key: ActionKey, command: WorkflowCommand, stream: Literal["stdout", "stderr"]) -> None:
        if self._stop_token is not None:
            self._ordinary(key, command.raw, stream)
            if command.name == self._stop_token.lower():
                self._stop_token = None
                self._stop_owner = None
            return
        if command.name == "stop-commands":
            if not command.data or command.data.lower() in REGISTERED_COMMANDS | {"pause-logging"}:
                raise ValueError("GitHub stop-commands token must be nonempty and not a registered command")
            resume_command(command.data)
            self._close_physical_group()
            self._stop_token = command.data
            self._stop_owner = key
            self._ordinary(key, command.raw, stream)
            return
        children = self._open_groups.get(key)
        if children is None:
            self._ordinary(key, command.raw, stream)
            return
        if command.name == "group":
            children.append(command.data)
        elif children:
            children.pop()
        self._select_group(key)

    def write_output(self, action_key: ActionKey, text: str, stream: Literal["stdout", "stderr"]) -> None:
        with self._stream_lock:
            pair = action_key, stream
            if pair not in self._children:
                self._children[pair] = ChildCommands(lambda value: self._ordinary(action_key, value, stream),
                    lambda command: self._command(action_key, command, stream), lambda: self._stop_token)
            self._children[pair].write(text)

    def _flush_children(self, key: ActionKey) -> None:
        for stream in ("stdout", "stderr"):
            parser = self._children.pop((key, stream), None)
            if parser is not None:
                parser.finish()

    def _resume_owned_commands(self, key: ActionKey) -> None:
        if self._stop_owner == key:
            assert self._stop_token is not None
            self._finish_lines()
            self._output.print_raw(resume_command(self._stop_token))
            self._stop_owner = None
            self._stop_token = None

    def _event(self, state: str, action_key: ActionKey, duration: float) -> None:
        with self._stream_lock:
            self._flush_children(action_key)
            self._resume_owned_commands(action_key)
            if action_key in self._open_groups:
                self._open_groups[action_key].clear()
                self._select_group(action_key)
            self._finish_lines()
            SimpleTerminalLogger._event(self, state, action_key, duration)
            self._marker_written()

    def end_action(self, action_key: ActionKey) -> None:
        with self._stream_lock:
            self._close_group(action_key)

    def _close_group(self, action_key: ActionKey) -> None:
        self._flush_children(action_key)
        self._resume_owned_commands(action_key)
        if action_key in self._open_groups:
            del self._open_groups[action_key]
            if self._physical_group is not None and self._physical_group[0] == action_key:
                self._close_physical_group()

    def finalize(self) -> None:
        with self._stream_lock:
            for action_key in list(self._open_groups):
                self._close_group(action_key)

    def _action_result_fields(self, result: "ExecutionResult") -> list[KeyValueRow]:
        return action_result_fields(result, self._action_order(), self._group_names)

    def report_outcome(self, result: "ExecutionResult", keep_run_dir: bool) -> None:
        self.finalize()
        token = uuid4().hex
        self._output.print_raw("::stop-commands::" + token)
        try:
            super().report_outcome(result, keep_run_dir)
        finally:
            self._output.print_raw(resume_command(token))

    def show_failure(self, action_key: ActionKey, result: "ActionResult", run_directory: Path, suppress_output: bool) -> None:
        legacy_failure(self._output, result, run_directory, False, True)
