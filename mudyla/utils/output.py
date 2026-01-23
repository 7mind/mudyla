"""Output utilities for terminal display with Rich console and emoji support."""

from typing import Union

from rich.console import Console
from rich.text import Text

from .symbols import SymbolsFormatter


class OutputFormatter:
    """Handles formatted output using Rich console with emoji support detection."""

    def __init__(self, no_color: bool = False):
        """Initialize output formatter.

        Args:
            no_color: If True, disable all colors and styling
        """
        self._no_color = no_color
        self._console = Console(
            no_color=no_color,
            force_terminal=None,
            highlight=False,
        )
        self._symbols = SymbolsFormatter(no_color=no_color)

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
    def supports_emoji(self) -> bool:
        """Check if terminal supports emoji display."""
        return self._symbols._supports_emoji

    @property
    def no_color(self) -> bool:
        """Check if colors are disabled."""
        return self._no_color

    def print(self, message: Union[str, Text]) -> None:
        """Print message using Rich console.

        Args:
            message: Message to print (string or Rich Text)
        """
        self._console.print(message, highlight=False)
