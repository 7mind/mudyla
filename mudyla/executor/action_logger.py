"""Action logger base class for execution progress reporting.

Provides the abstract interface for action execution logging.
See action_logger_raw.py and action_logger_interactive.py for implementations.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Optional

from ..dag.graph import ActionKey


class ActionLogger(ABC):
    """Abstract base class for action execution logging.

    Defines the interface for reporting action execution progress.
    Implementations handle the actual display (text output or interactive table).
    """

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
