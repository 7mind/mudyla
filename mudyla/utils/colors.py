"""Color utilities for terminal output."""

import platform
import sys
from typing import Optional


class Colors:
    """Terminal color codes."""

    # ANSI color codes (work on *nix and modern Windows)
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    # Regular colors
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    # Bright colors
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"


class ColorFormatter:
    """Formatter for colored terminal output."""

    def __init__(self, no_color: bool = False):
        """Initialize the color formatter.

        Args:
            no_color: If True, disable all colors
        """
        self.enabled = not no_color and self._supports_color()

    def _supports_color(self) -> bool:
        """Check if the terminal supports color.

        Returns:
            True if color is supported
        """
        # Check if stdout is a TTY
        if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
            return False

        # On Windows, ANSI escape codes are supported on Windows 10+
        if platform.system() == "Windows":
            # Check Windows version
            try:
                version = sys.getwindowsversion()
                # Windows 10 is version 10.0
                return version.major >= 10
            except AttributeError:
                # If getwindowsversion is not available, assume no color support
                return False

        # On *nix systems, check TERM environment variable
        import os
        term = os.environ.get("TERM", "")
        if term == "dumb":
            return False

        return True

    def colorize(self, text: str, color: str, bold: bool = False) -> str:
        """Apply color to text.

        Args:
            text: Text to colorize
            color: Color code from Colors class
            bold: If True, make text bold

        Returns:
            Colored text (or plain text if colors are disabled)
        """
        if not self.enabled:
            return text

        prefix = color
        if bold:
            prefix = Colors.BOLD + color

        return f"{prefix}{text}{Colors.RESET}"

    def success(self, text: str) -> str:
        """Format text as success (green).

        Args:
            text: Text to format

        Returns:
            Formatted text
        """
        return self.colorize(text, Colors.GREEN, bold=True)

    def error(self, text: str) -> str:
        """Format text as error (red).

        Args:
            text: Text to format

        Returns:
            Formatted text
        """
        return self.colorize(text, Colors.RED, bold=True)

    def warning(self, text: str) -> str:
        """Format text as warning (yellow).

        Args:
            text: Text to format

        Returns:
            Formatted text
        """
        return self.colorize(text, Colors.YELLOW, bold=True)

    def info(self, text: str) -> str:
        """Format text as info (blue).

        Args:
            text: Text to format

        Returns:
            Formatted text
        """
        return self.colorize(text, Colors.BLUE)

    def highlight(self, text: str) -> str:
        """Highlight text (cyan, bold).

        Args:
            text: Text to format

        Returns:
            Formatted text
        """
        return self.colorize(text, Colors.CYAN, bold=True)

    def dim(self, text: str) -> str:
        """Dim text (gray).

        Args:
            text: Text to format

        Returns:
            Formatted text
        """
        if not self.enabled:
            return text
        return f"{Colors.DIM}{text}{Colors.RESET}"

    def bold(self, text: str) -> str:
        """Make text bold.

        Args:
            text: Text to format

        Returns:
            Formatted text
        """
        if not self.enabled:
            return text
        return f"{Colors.BOLD}{text}{Colors.RESET}"

    def format_context_string(self, context_str: str) -> str:
        """Format a context string with axis names and values in different colors.

        Args:
            context_str: Context string (format: "axis1:value1+axis2:value2")

        Returns:
            Formatted string with axis names (magenta) and values (cyan)
        """
        if not self.enabled:
            return context_str

        # Split by + to get individual axis=value pairs
        parts = context_str.split("+")
        formatted_parts = []

        for part in parts:
            if ":" in part:
                axis_name, axis_value = part.split(":", 1)
                # Format: magenta axis name + dim separator + cyan value
                axis_colored = self.colorize(axis_name, Colors.MAGENTA)
                separator = self.dim(":")
                value_colored = self.colorize(axis_value, Colors.CYAN)
                formatted_parts.append(f"{axis_colored}{separator}{value_colored}")
            else:
                formatted_parts.append(part)

        return self.dim("+").join(formatted_parts)

    def format_short_context_id(self, short_id_with_emoji: str) -> str:
        """Format a short context ID with emoji.

        Args:
            short_id_with_emoji: Context ID with emoji prefix (e.g., "🔴79d776")

        Returns:
            Formatted string with highlighted ID
        """
        if not self.enabled:
            return short_id_with_emoji

        # The emoji is already there, just highlight the hex part
        # Extract emoji (first character) and ID (rest)
        if len(short_id_with_emoji) > 1:
            emoji = short_id_with_emoji[0]
            hex_id = short_id_with_emoji[1:]
            return emoji + self.colorize(hex_id, Colors.CYAN, bold=True)
        return short_id_with_emoji

    def format_action_key(self, action_key_str: str) -> str:
        """Format an action key with context and action name in different colors.

        Args:
            action_key_str: Action key string (format: "name" or "context#name")

        Returns:
            Formatted string with context (colored) and action (cyan bold)
        """
        if "#" not in action_key_str:
            # No context - just highlight the action name
            return self.highlight(action_key_str)

        # Split context and action name
        context_str, action_name = action_key_str.split("#", 1)

        # Format context (could be short ID with emoji or full context)
        if not self.enabled:
            return action_key_str

        # Check if it's a short ID with emoji (starts with emoji character)
        # Emoji characters are typically in the range U+1F000 and above
        if context_str and ord(context_str[0]) > 0x1F000:
            # Short ID format: format the ID part
            context_colored = self.format_short_context_id(context_str)
        else:
            # Full context format: format axis:value pairs
            context_colored = self.format_context_string(context_str)

        separator = self.dim("#")
        action_colored = self.highlight(action_name)

        return f"{context_colored}{separator}{action_colored}"
