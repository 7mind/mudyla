"""Output formatter - the main entry point for all formatting operations.

Provides a centralized formatter that creates and manages all sub-formatters.
The OutputFormatter creates a Rich console with no_color support, and all
sub-formatters return Rich Text objects that are printed through this console.

Usage:
    output = OutputFormatter(no_color=False)
    output.print(output.context.format_full(context_id))
    output.print(output.action.format_label(action_key, use_short_ids=True))
"""

from io import TextIOWrapper
import sys
from typing import Literal, Optional

from rich.console import Console, Group, RenderableType
from rich.text import Text

from .symbols import SymbolsFormatter
from .context import ContextFormatter
from .action import ActionFormatter
from .details import KeyValueRow, KeyValueView, summary_field
from .sections import section
from ..terminal_output import StreamState
from ..teamcity import TeamCityWriter, standard_writer


class OutputFormatter:
    """Central formatter that manages Rich console and all sub-formatters.

    The OutputFormatter is the main entry point for all formatting operations.
    It creates a Rich console that handles no_color mode, and provides access
    to sub-formatters for specific formatting needs.

    All sub-formatters return Rich Text objects with styling markers. The
    no_color option is handled by the Rich console when printing, so formatters
    don't need to conditionally apply styles.

    Attributes:
        console: The Rich console for output
        symbols: SymbolsFormatter for emoji/ASCII symbols
        context: ContextFormatter for ContextId formatting
        action: ActionFormatter for ActionKey formatting

    Example:
        output = OutputFormatter(no_color=False)
        output.print(f"{output.symbols.Check} Task completed!")
        output.print(output.context.format_full(ctx))
        output.print(output.action.format_label(key, use_short_ids=True))
    """

    def __init__(self, no_color: bool, *, plain: bool = False, compact: bool = False,
                 teamcity: bool = False, console: Optional[Console] = None):
        """Initialize the output formatter with all sub-formatters.

        Args:
            no_color: If True, disable all colors and styling in output
        """
        for stream in (sys.stdout, sys.stderr):
            if isinstance(stream, TextIOWrapper):
                stream.reconfigure(errors="replace", newline="")

        self.teamcity_writer: Optional[TeamCityWriter] = standard_writer() if teamcity else None
        sink = self.teamcity_writer.sink(None) if self.teamcity_writer is not None else None
        self._no_color = no_color
        self.compact = compact
        self._recorded_renderables: Optional[list[RenderableType]] = None
        self._defer_recording = False
        self._run_fields: Optional[list[KeyValueRow]] = None

        # Create the Rich console with no_color support
        self._console = console if console is not None else Console(
            file=sink,
            no_color=no_color,
            color_system=None if plain else "auto",
            force_terminal=None if sys.stdout.isatty() else False,
            highlight=False,
        )
        self._stderr_console = Console(
            file=sink,
            no_color=no_color,
            color_system=None if plain else "auto",
            force_terminal=None if sys.stderr.isatty() else False,
            highlight=False,
            stderr=True,
        )

        # Create all sub-formatters - symbols first as others depend on it
        self._symbols = SymbolsFormatter(self._console, decorative_ascii=no_color or compact)
        self._context = ContextFormatter(symbols=self._symbols)
        self._action = ActionFormatter(context_formatter=self._context)

    @property
    def console(self) -> Console:
        """Get the underlying Rich console."""
        return self._console

    def console_for_stream(self, stream: Literal["stdout", "stderr"]) -> Console:
        return self._console if stream == "stdout" else self._stderr_console

    @property
    def symbols(self) -> SymbolsFormatter:
        """Get the symbols formatter for emoji/ASCII symbol access.

        Usage:
            output.symbols.Check  # Returns "●" or "+"
            output.symbols.Globe  # Returns "🌍" or "*"
        """
        return self._symbols

    @property
    def context(self) -> ContextFormatter:
        """Get the context formatter for ContextId formatting.

        Usage:
            output.context.format_full(context_id)
            output.context.format_id_with_symbol(context_id, use_short_ids=True)
        """
        return self._context

    @property
    def action(self) -> ActionFormatter:
        """Get the action formatter for ActionKey formatting.

        Usage:
            output.action.format_label(action_key, use_short_ids=True)
            output.action.format_full(action_key)
        """
        return self._action

    @property
    def supports_emoji(self) -> bool:
        """Check if terminal supports emoji display."""
        return self._symbols.supports_emoji

    @property
    def no_color(self) -> bool:
        """Check if colors are disabled."""
        return self._no_color
    
    def escape(self, message: str) -> str:
        from rich.markup import escape
        return escape(message)

    def start_recording(self, *, defer: bool = False) -> None:
        """Collect preparation renderables for the interactive run overview."""
        assert self._recorded_renderables is None, "Output recording already started"
        self._recorded_renderables = []
        self._defer_recording = defer
        if defer:
            self._run_fields = []
            self._recorded_renderables.append(section("Run info:", KeyValueView(self._run_fields), None, None))

    @property
    def recording_preparation(self) -> bool:
        return self._run_fields is not None

    def print_run_field(self, name: str, value: Text, legacy: str) -> None:
        if self.compact or self.recording_preparation:
            assert self._run_fields is not None, "Run fields require preparation recording"
            self._run_fields.append(summary_field(name, value))
        else:
            self.print(legacy)

    def flush_recording(self, exclude: Optional[RenderableType] = None) -> None:
        """Emit deferred preparation on success, early return, or failure exactly once."""
        if self._defer_recording:
            assert self._recorded_renderables is not None
            self._defer_recording = False
            self._console.print(Group(*(item for item in self._recorded_renderables if item is not exclude)), highlight=False)

    def stop_recording(self, exclude: Optional[RenderableType] = None) -> Group:
        """Freeze the preparation snapshot before action execution starts."""
        assert self._recorded_renderables is not None, "Output recording was not started"
        self.flush_recording(exclude=exclude)
        snapshot = Group(*(item for item in self._recorded_renderables if item is not exclude))
        self._recorded_renderables = None
        self._run_fields = None
        return snapshot

    def print(self, message: RenderableType) -> None:
        """Print message using Rich console.

        The console handles no_color mode, so Rich Text objects with styling
        markers will have their styles stripped when no_color is True.

        Args:
            message: Message to print (string or Rich Text)
        """
        if self._recorded_renderables is not None:
            renderable = self._console.render_str(message, highlight=False) if isinstance(message, str) else message
            self._recorded_renderables.append(renderable)
        if not self._defer_recording:
            self._console.print(message, highlight=False)

    def print_raw(self, message: str) -> None:
        """Print message without any Rich processing.

        Use for output that requires exact formatting (e.g., GitHub Actions markers).

        Args:
            message: Raw message to print exactly as-is
        """
        if self.teamcity_writer is not None:
            self.teamcity_writer.message(message + "\n", None)
            return
        state = StreamState()
        for char in message:
            state.consume(char)
        sys.stdout.write(message)
        state.finish_control(sys.stdout)
        sys.stdout.write("\n")

    def print_warning(self, message: str) -> None:
        """Print a warning message.

        Args:
            message: Warning message to print
        """
        line = Text()
        line.append(f"{self._symbols.Warning} ", style="yellow")
        line.append("Warning: ", style="bold yellow")
        line.append(message)
        self._stderr_console.print(line, highlight=False)
