"""Rich table manager for displaying task execution status."""

import threading
import time
from enum import Enum
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.table import Table


class TaskStatus(Enum):
    """Task execution status."""

    TBD = "tbd"
    RUNNING = "running"
    DONE = "done"
    RESTORED = "restored"
    FAILED = "failed"


class TaskTableManager:
    """Manages a dynamic rich table showing task execution status."""

    def __init__(self, task_names: list[str], no_color: bool = False):
        """Initialize the task table manager.

        Args:
            task_names: List of task names in execution order
            no_color: Whether colors are disabled
        """
        self.task_names = task_names
        self.no_color = no_color
        self.console = Console()

        # Task state
        self.task_status: dict[str, TaskStatus] = {name: TaskStatus.TBD for name in task_names}
        self.task_start_times: dict[str, float] = {}
        self.task_durations: dict[str, float] = {}
        self.task_stdout_sizes: dict[str, int] = {name: 0 for name in task_names}
        self.task_stderr_sizes: dict[str, int] = {name: 0 for name in task_names}

        # Threading
        self.lock = threading.Lock()
        self.live: Optional[Live] = None
        self.stop_updating = False
        self.update_thread: Optional[threading.Thread] = None

    def _get_status_style(self, status: TaskStatus) -> str:
        """Get the rich style for a status.

        Args:
            status: Task status

        Returns:
            Rich style string
        """
        if self.no_color:
            return ""

        return {
            TaskStatus.TBD: "dim",
            TaskStatus.RUNNING: "cyan",
            TaskStatus.DONE: "green",
            TaskStatus.RESTORED: "green",
            TaskStatus.FAILED: "red",
        }[status]

    def _get_status_text(self, status: TaskStatus) -> str:
        """Get the display text for a status.

        Args:
            status: Task status

        Returns:
            Status text
        """
        return status.value

    def _format_duration(self, seconds: float) -> str:
        """Format duration for display.

        Args:
            seconds: Duration in seconds

        Returns:
            Formatted duration string
        """
        if seconds < 1.0:
            return f"{seconds:.1f}s"
        elif seconds < 60.0:
            return f"{seconds:.1f}s"
        else:
            minutes = int(seconds // 60)
            secs = seconds % 60
            return f"{minutes}m {secs:.0f}s"

    def _format_size(self, size_bytes: int) -> str:
        """Format size for display.

        Args:
            size_bytes: Size in bytes

        Returns:
            Formatted size string
        """
        if size_bytes == 0:
            return "-"
        elif size_bytes < 1024:
            return f"{size_bytes}B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f}K"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f}M"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f}G"

    def _build_table(self) -> Table:
        """Build the current state of the table.

        Returns:
            Rich Table object
        """
        # Check if any tasks have context (contain "#")
        has_context = any("#" in name for name in self.task_names)

        table = Table(show_header=True, header_style="bold")

        if has_context:
            # Multi-context mode: separate context and action columns
            # Don't set a default style for Context - let per-cell formatting handle colors
            table.add_column("Context", no_wrap=True)
            table.add_column("Action", style="cyan bold", no_wrap=True)
        else:
            # Single context mode: just task column
            table.add_column("Task", style="cyan bold", no_wrap=True)

        table.add_column("Time", justify="right", no_wrap=True)
        table.add_column("Stdout", justify="right", no_wrap=True)
        table.add_column("Stderr", justify="right", no_wrap=True)
        table.add_column("Status", justify="center", no_wrap=True)

        with self.lock:
            for task_name in self.task_names:
                status = self.task_status[task_name]
                style = self._get_status_style(status)
                status_text = self._get_status_text(status)

                # Calculate time
                if status == TaskStatus.RUNNING and task_name in self.task_start_times:
                    elapsed = time.time() - self.task_start_times[task_name]
                    time_str = self._format_duration(elapsed)
                elif status in (TaskStatus.DONE, TaskStatus.RESTORED, TaskStatus.FAILED) and task_name in self.task_durations:
                    time_str = self._format_duration(self.task_durations[task_name])
                else:
                    time_str = "-"

                # Format sizes
                stdout_str = self._format_size(self.task_stdout_sizes.get(task_name, 0))
                stderr_str = self._format_size(self.task_stderr_sizes.get(task_name, 0))

                # Split task name into context and action if present
                if has_context and "#" in task_name:
                    context_str, action_name = task_name.split("#", 1)
                    # Format context with colors (symbol/emoji + blue bold hex ID)
                    # Check if context is a short ID (7 chars: 1 symbol + 6 hex)
                    if context_str and len(context_str) == 7:
                        # Check if last 6 chars are hex (means it's a short ID)
                        hex_part = context_str[1:]
                        try:
                            int(hex_part, 16)  # Try parsing as hex
                            is_short_id = True
                        except ValueError:
                            is_short_id = False

                        if is_short_id:
                            # Short ID with symbol: symbol + blue bold hex
                            symbol = context_str[0]
                            hex_id = context_str[1:]
                            context_formatted = f"{symbol}[blue bold]{hex_id}[/blue bold]"
                        else:
                            # Full context: just display as-is
                            context_formatted = context_str
                    else:
                        # Full context: format axis:value pairs
                        # This shouldn't normally happen in the table, but handle it
                        context_formatted = context_str

                    row_data = [
                        context_formatted,  # Don't apply status style to context - keep blue formatting
                        f"[{style}]{action_name}[/{style}]" if style else action_name,
                    ]
                elif has_context:
                    # No context for this task, use empty string for context column
                    row_data = [
                        "",
                        f"[{style}]{task_name}[/{style}]" if style else task_name,
                    ]
                else:
                    # Single context mode
                    row_data = [
                        f"[{style}]{task_name}[/{style}]" if style else task_name,
                    ]

                # Add remaining columns
                row_data.extend([
                    f"[{style}]{time_str}[/{style}]" if style else time_str,
                    f"[{style}]{stdout_str}[/{style}]" if style else stdout_str,
                    f"[{style}]{stderr_str}[/{style}]" if style else stderr_str,
                    f"[{style}]{status_text}[/{style}]" if style else status_text,
                ])

                table.add_row(*row_data)

        return table

    def _update_loop(self) -> None:
        """Background thread that updates the table display."""
        while not self.stop_updating:
            if self.live:
                self.live.update(self._build_table())
            time.sleep(0.1)  # Update 10 times per second

    def start(self) -> None:
        """Start the live table display."""
        self.stop_updating = False
        self.live = Live(self._build_table(), console=self.console, refresh_per_second=10)
        self.live.start()

        # Start background update thread
        self.update_thread = threading.Thread(target=self._update_loop, daemon=True)
        self.update_thread.start()

    def stop(self) -> None:
        """Stop the live table display."""
        self.stop_updating = True
        if self.update_thread:
            self.update_thread.join(timeout=1.0)
        if self.live:
            self.live.update(self._build_table())  # Final update
            self.live.stop()

    def mark_running(self, task_name: str) -> None:
        """Mark a task as running.

        Args:
            task_name: Task name
        """
        with self.lock:
            self.task_status[task_name] = TaskStatus.RUNNING
            self.task_start_times[task_name] = time.time()

    def mark_done(self, task_name: str, duration: float) -> None:
        """Mark a task as done.

        Args:
            task_name: Task name
            duration: Task duration in seconds
        """
        with self.lock:
            self.task_status[task_name] = TaskStatus.DONE
            self.task_durations[task_name] = duration

    def mark_restored(self, task_name: str, duration: float) -> None:
        """Mark a task as restored from previous run.

        Args:
            task_name: Task name
            duration: Task duration in seconds
        """
        with self.lock:
            self.task_status[task_name] = TaskStatus.RESTORED
            self.task_durations[task_name] = duration

    def mark_failed(self, task_name: str, duration: float) -> None:
        """Mark a task as failed.

        Args:
            task_name: Task name
            duration: Task duration in seconds
        """
        with self.lock:
            self.task_status[task_name] = TaskStatus.FAILED
            self.task_durations[task_name] = duration

    def update_output_sizes(self, task_name: str, stdout_size: int, stderr_size: int) -> None:
        """Update stdout and stderr sizes for a task.

        Args:
            task_name: Task name
            stdout_size: Current stdout size in bytes
            stderr_size: Current stderr size in bytes
        """
        with self.lock:
            self.task_stdout_sizes[task_name] = stdout_size
            self.task_stderr_sizes[task_name] = stderr_size
