"""Action logger base class for execution progress reporting.

Provides the abstract interface for action execution logging.
Implementations provide append-only output or managed terminal views.
"""

from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal, Optional

from ..dag.graph import ActionKey

if TYPE_CHECKING:
    from ..executor.engine import ActionResult


class LoggerMode(str, Enum):
    PURE = "pure"
    TABLE = "table"
    SIMPLE = "simple"
    VERBOSE = "verbose"
    GITHUB = "github"
    TEAMCITY = "teamcity"

    @property
    def compact(self) -> bool:
        return self != LoggerMode.TABLE


def resolve_logger_mode(explicit: Optional[str], simple: bool, verbose: bool, github: bool, teamcity: bool) -> LoggerMode:
    legacy = LoggerMode.GITHUB if github else LoggerMode.VERBOSE if verbose else LoggerMode.SIMPLE if simple else None
    if teamcity:
        if legacy is not None or explicit not in {None, "raw", "teamcity"}:
            raise ValueError("--teamcity conflicts with the selected logging option")
        return LoggerMode.TEAMCITY
    if explicit is None or explicit == "raw":
        return legacy or (LoggerMode.SIMPLE if explicit == "raw" else LoggerMode.PURE)
    selected = LoggerMode(explicit)
    if legacy is not None and selected != legacy:
        raise ValueError("--logger conflicts with the selected legacy logging option")
    return selected


class ActionLogger(ABC):
    """Abstract base class for action execution logging.

    Defines the interface for reporting action execution progress.
    Implementations handle the actual display (text output or interactive table).
    """

    receives_suppressed_output = False

    def begin_action(self, action_key: ActionKey, command: list[str]) -> None:
        pass

    def end_action(self, action_key: ActionKey) -> None:
        pass

    def finalize(self) -> None:
        """Finish remaining records after execution workers have stopped."""
        pass

    @abstractmethod
    def show_failure(self, action_key: ActionKey, result: "ActionResult", run_directory: Path, suppress_output: bool) -> None:
        pass

    @abstractmethod
    def mark_running(self, action_key: ActionKey, action_dir: Optional[Path] = None) -> None:
        """Mark an action as running.

        Args:
            action_key: The action key being started
            action_dir: Optional path to action directory
        """
        pass

    @abstractmethod
    def mark_done(self, action_key: ActionKey, duration: float) -> None:
        """Mark an action as done.

        Args:
            action_key: The action key that completed
            duration: Execution duration in seconds
        """
        pass

    @abstractmethod
    def mark_failed(self, action_key: ActionKey, duration: float) -> None:
        """Mark an action as failed.

        Args:
            action_key: The action key that failed
            duration: Execution duration in seconds
        """
        pass

    @abstractmethod
    def mark_restored(
        self, action_key: ActionKey, duration: float, action_dir: Optional[Path] = None
    ) -> None:
        """Mark an action as restored from previous run.

        Args:
            action_key: The action key that was restored
            duration: Original execution duration in seconds
            action_dir: Optional path to action directory
        """
        pass

    @abstractmethod
    def update_output_sizes(
        self, action_key: ActionKey, stdout_size: int, stderr_size: int
    ) -> None:
        """Update stdout and stderr sizes for an action.

        Args:
            action_key: The action key to update
            stdout_size: Size of stdout in bytes
            stderr_size: Size of stderr in bytes
        """
        pass

    def write_output(self, action_key: ActionKey, text: str, stream: Literal["stdout", "stderr"]) -> None:
        """Receive captured output; file-backed viewers need no additional delivery."""
        pass

    def uses_terminal_input(self) -> bool:
        return False

    def set_input_callback(self, callback: Callable[[ActionKey, Optional[str]], Optional[str]]) -> None:
        pass

    def report_input_error(self, action_key: ActionKey, message: str) -> None:
        pass

    @abstractmethod
    def set_kill_callback(self, callback: Callable[[], None]) -> None:
        """Set callback to be called when user requests kill.

        Args:
            callback: Function to call to terminate running processes
        """
        pass

    @abstractmethod
    def is_kill_requested(self) -> bool:
        """Check if user has requested to kill execution.

        Returns:
            True if kill was requested
        """
        pass

    @abstractmethod
    def start(self) -> None:
        """Start the logger display."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Stop the logger display."""
        pass

    @abstractmethod
    def wait_for_quit(self) -> None:
        """Wait for user to quit (for interactive modes with --it flag)."""
        pass
