"""Raw action logger for simple text-based progress reporting.

Used for verbose, github_actions, or simple_log modes.
Prints action state changes to console using OutputFormatter.
"""

from pathlib import Path
from typing import Callable, Optional

from ..dag.graph import ActionKey
from ..formatters import OutputFormatter
from .action_logger import ActionLogger


class ActionLoggerRaw(ActionLogger):
    """Simple text-based action logger for verbose/github_actions/simple_log modes.

    Prints action state changes to console using OutputFormatter.
    Rich console handles no_color mode automatically.
    """

    def __init__(
        self,
        action_keys: list[ActionKey],
        output: OutputFormatter,
        use_short_ids: bool = True,
        github_actions: bool = False,
    ):
        """Initialize the raw action logger.

        Args:
            action_keys: List of action keys in execution order
            output: OutputFormatter for printing
            use_short_ids: Whether to use short context IDs in labels
            github_actions: Whether running in GitHub Actions mode
        """
        self._action_keys = list(action_keys)
        self._output = output
        self._use_short_ids = use_short_ids
        self._github_actions = github_actions
        self._kill_callback: Optional[Callable[[], None]] = None
        self._kill_requested = False

    def _format_action_label(self, action_key: ActionKey) -> str:
        """Format action key for display."""
        return self._output.action.format_label_plain(action_key, self._use_short_ids)

    def _format_duration(self, seconds: float) -> str:
        """Format duration for display."""
        if seconds < 60.0:
            return f"{seconds:.1f}s"
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}m {secs:.0f}s"

    def mark_running(self, action_key: ActionKey, _action_dir: Optional[Path] = None) -> None:
        """Mark an action as running."""
        if self._github_actions:
            return  # GitHub Actions uses ::group:: markers instead

        sym = self._output.symbols
        label = self._format_action_label(action_key)
        self._output.print(f"{sym.Play} [dim]start:[/dim] [bold cyan]{label}[/bold cyan]")

    def mark_done(self, action_key: ActionKey, duration: float) -> None:
        """Mark an action as done."""
        sym = self._output.symbols
        label = self._format_action_label(action_key)
        duration_str = self._format_duration(duration)
        self._output.print(f"{sym.Check} [dim]done:[/dim] [bold cyan]{label}[/bold cyan] [dim]({duration_str})[/dim]")

    def mark_failed(self, action_key: ActionKey, duration: float) -> None:
        """Mark an action as failed."""
        sym = self._output.symbols
        label = self._format_action_label(action_key)
        duration_str = self._format_duration(duration)
        self._output.print(f"{sym.Cross} [bold red]failed:[/bold red] [bold cyan]{label}[/bold cyan] [dim]({duration_str})[/dim]")

    def mark_restored(
        self, action_key: ActionKey, duration: float, _action_dir: Optional[Path] = None
    ) -> None:
        """Mark an action as restored from previous run."""
        sym = self._output.symbols
        label = self._format_action_label(action_key)
        duration_str = self._format_duration(duration)
        self._output.print(f"{sym.Recycle} [dim]done:[/dim] [bold cyan]{label}[/bold cyan] [dim]({duration_str}) (restored)[/dim]")

    def update_output_sizes(
        self, _action_key: ActionKey, _stdout_size: int, _stderr_size: int
    ) -> None:
        """Update output sizes - no-op for raw logger."""
        pass

    def set_kill_callback(self, callback: Callable[[], None]) -> None:
        """Set kill callback."""
        self._kill_callback = callback

    def is_kill_requested(self) -> bool:
        """Check if kill was requested."""
        return self._kill_requested

    def start(self) -> None:
        """Start the logger - no-op for raw logger."""
        pass

    def stop(self) -> None:
        """Stop the logger - no-op for raw logger."""
        pass

    def wait_for_quit(self) -> None:
        """Wait for quit - no-op for raw logger."""
        pass
