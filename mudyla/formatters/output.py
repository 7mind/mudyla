"""Output formatter - the main entry point for all formatting operations.

Provides a centralized formatter that creates and manages all sub-formatters.
The OutputFormatter creates a Rich console with no_color support, and all
sub-formatters return Rich Text objects that are printed through this console.

Usage:
    output = OutputFormatter(no_color=False)
    output.print(output.context.format_full(context_id))
    output.print(output.action.format_label(action_key, use_short_ids=True))
"""

from typing import Union

from rich.console import Console
from rich.text import Text

from .symbols import SymbolsFormatter
from .context import ContextFormatter
from .action import ActionFormatter


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

    def __init__(self, no_color: bool):
        """Initialize the output formatter with all sub-formatters.

        Args:
            no_color: If True, disable all colors and styling in output
        """
        self._no_color = no_color

        # Create the Rich console with no_color support
        self._console = Console(
            no_color=no_color,
            force_terminal=None,
            highlight=False,
        )

        # Create all sub-formatters - symbols first as others depend on it
        self._symbols = SymbolsFormatter(no_color=no_color)
        self._context = ContextFormatter(symbols=self._symbols)
        self._action = ActionFormatter(context_formatter=self._context)

    @property
    def console(self) -> Console:
        """Get the underlying Rich console."""
        return self._console

    @property
    def symbols(self) -> SymbolsFormatter:
        """Get the symbols formatter for emoji/ASCII symbol access.

        Usage:
            output.symbols.Check  # Returns "✅" or "+"
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

    def print(self, message: Union[str, Text]) -> None:
        """Print message using Rich console.

        The console handles no_color mode, so Rich Text objects with styling
        markers will have their styles stripped when no_color is True.

        Args:
            message: Message to print (string or Rich Text)
        """
        self._console.print(message, highlight=False)

    def print_raw(self, message: str) -> None:
        """Print message without any Rich processing.

        Use for output that requires exact formatting (e.g., GitHub Actions markers).

        Args:
            message: Raw message to print exactly as-is
        """
        print(message)

    def print_command(self, cmd: str) -> None:
        """Print a command string with appropriate styling.

        Args:
            cmd: Command string to print
        """
        line = Text()
        line.append("Command: ", style="dim")
        line.append(cmd)
        self._console.print(line, highlight=False)

    def print_warning(self, message: str) -> None:
        """Print a warning message.

        Args:
            message: Warning message to print
        """
        line = Text()
        line.append(f"{self._symbols.Warning} ", style="yellow")
        line.append("Warning: ", style="bold yellow")
        line.append(message)
        self._console.print(line, highlight=False, stderr=True)
