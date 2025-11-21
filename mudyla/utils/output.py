"""Output utilities for terminal display with emoji support detection."""

import sys
from typing import Optional

from .colors import ColorFormatter


class OutputFormatter:
    """Handles formatted output with emoji support detection."""

    def __init__(self, color: ColorFormatter):
        """Initialize output formatter.

        Args:
            color: Color formatter instance
        """
        self.color = color
        self.supports_emoji = self._detect_emoji_support()

    def _detect_emoji_support(self) -> bool:
        """Detect if terminal supports emoji display.

        Returns:
            True if emojis are supported, False otherwise
        """
        # If colors are disabled, don't use emojis
        if not self.color.enabled:
            return False

        # Check if stdout has encoding attribute
        if not hasattr(sys.stdout, 'encoding') or sys.stdout.encoding is None:
            return False

        encoding = sys.stdout.encoding.lower()

        # Common encodings that support emojis
        emoji_encodings = ['utf-8', 'utf8', 'utf-16', 'utf16']

        # Check if encoding supports Unicode
        return any(enc in encoding for enc in emoji_encodings)

    def emoji(self, emoji_char: str, fallback: str) -> str:
        """Return emoji or fallback based on terminal support.

        Args:
            emoji_char: The emoji character to display
            fallback: ASCII fallback character

        Returns:
            Emoji if supported, otherwise fallback
        """
        return emoji_char if self.supports_emoji else fallback

    def print(self, message: str) -> None:
        """Print message with proper encoding handling.

        Args:
            message: Message to print
        """
        try:
            print(message)
        except UnicodeEncodeError:
            # Fallback: encode as ASCII with replacement
            safe_message = message.encode('ascii', errors='replace').decode('ascii')
            print(safe_message)
