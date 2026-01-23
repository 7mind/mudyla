"""Context formatting utilities with Rich styling support.

Provides formatted representations of ContextId for display purposes.
All formatting methods return Rich Text objects with styling markers.
"""

from __future__ import annotations

import hashlib
import platform
from functools import lru_cache
from typing import TYPE_CHECKING

from rich.text import Text

from .names_generator import generate_name

if TYPE_CHECKING:
    from ..dag.context import ContextId


CONTEXT_EMOJIS: tuple[str, ...] = (
    "🔴", "🟠", "🟡", "🟢", "🔵", "🟣", "🟤", "⚫",
    "🟥", "🟧", "🟨", "🟩", "🟦", "🟪", "🟫", "⬛",
    "⭐", "🌟", "💫", "✨", "🔶", "🔷", "🔸", "🔹",
    "❤️", "🧡", "💛", "💚", "💙", "💜", "🖤", "🤍",
)

CONTEXT_SYMBOLS_ASCII: tuple[str, ...] = (
    "A", "B", "C", "D", "E", "F", "G", "H",
    "J", "K", "L", "M", "N", "P", "Q", "R",
    "S", "T", "U", "V", "W", "X", "Y", "Z",
    "1", "2", "3", "4", "5", "6", "7", "8",
)

# Colors for context identifiers - universally supported, visible on light and dark backgrounds
# Using basic ANSI colors (0-15) and common 256-color codes for maximum terminal compatibility
CONTEXT_COLORS: tuple[str, ...] = (
    # Basic ANSI colors - supported by all terminals
    "cyan",
    "green",
    "yellow",
    "magenta",
    "red",
    "blue",
    # Bright variants - widely supported
    "bright_cyan",
    "bright_green",
    "bright_yellow",
    "bright_magenta",
    "bright_red",
    "bright_blue",
    # Additional distinguishable colors from 256-color palette
    # These are chosen for visibility on both light and dark backgrounds
    "color(37)",   # white/light gray - neutral
    "color(73)",   # medium cyan
    "color(107)",  # olive/khaki
    "color(133)",  # medium magenta/purple
    "color(137)",  # tan/sand
    "color(167)",  # coral/salmon
    "color(68)",   # steel blue
    "color(71)",   # sea green
    "color(101)",  # olive
    "color(131)",  # indian red
    "color(139)",  # rosy brown
    "color(143)",  # khaki
    "color(67)",   # slate blue
    "color(103)",  # medium purple
    "color(109)",  # cadet blue
    "color(145)",  # gray
    "color(175)",  # plum
    "color(179)",  # gold
)

DEFAULT_CONTEXT_EMOJI = "🌍"
DEFAULT_CONTEXT_SYMBOL_ASCII = "*"
DEFAULT_CONTEXT_COLOR = "cyan"


class ContextFormatter:
    """Formats context identifiers for display with Rich styling."""

    def __init__(self, no_color: bool = False):
        """Initialize the context formatter.

        Args:
            no_color: If True, disable all styling
        """
        self._no_color = no_color
        self._use_ascii = platform.system() == "Windows" or no_color

    def format_id(self, context: ContextId, use_short_ids: bool) -> Text:
        """Format a context identifier for display.

        Args:
            context: The context to format
            use_short_ids: If True, use deterministic name; if False, use full sha hash

        Returns:
            Rich Text with styled context identifier
        """
        context_str = str(context)

        if context_str == "default":
            return self._format_default_context()

        hex_hash = self._compute_hash(context_str)
        color = self._get_color_for_hash(hex_hash)

        if use_short_ids:
            return self._format_deterministic_name(hex_hash, color)
        else:
            return self._format_hash_id(hex_hash, color)

    def format_full(self, context: ContextId) -> Text:
        """Format a context with full string representation.

        Args:
            context: The context to format

        Returns:
            Rich Text with styled axis:value pairs
        """
        context_str = str(context)

        if context_str == "default":
            return Text("default", style="" if self._no_color else "dim")

        return self._format_context_string(context_str)

    def format_id_with_symbol(self, context: ContextId, use_short_ids: bool) -> Text:
        """Format a context identifier with a leading symbol/emoji.

        Args:
            context: The context to format
            use_short_ids: If True, use deterministic name; if False, use hash

        Returns:
            Rich Text with symbol prefix and styled identifier
        """
        context_str = str(context)

        if context_str == "default":
            return self._format_default_context_with_symbol()

        hex_hash = self._compute_hash(context_str)
        symbol = self._get_symbol_for_hash(hex_hash)
        color = self._get_color_for_hash(hex_hash)

        if use_short_ids:
            name = generate_name(hex_hash)
            result = Text(symbol)
            result.append(name, style="" if self._no_color else f"bold {color}")
            return result
        else:
            result = Text(symbol)
            result.append(hex_hash[:6], style="" if self._no_color else f"bold {color}")
            return result

    def get_context_mapping(self, contexts: list[ContextId], use_short_ids: bool) -> dict[str, str]:
        """Build a mapping from formatted short IDs to full context strings.

        Args:
            contexts: List of contexts to map
            use_short_ids: If True, use deterministic names; if False, use hashes

        Returns:
            Dictionary mapping formatted ID strings to full context strings
        """
        mapping: dict[str, str] = {}

        for context in contexts:
            context_str = str(context)
            if context_str == "default":
                continue

            formatted_id = self.format_id_with_symbol(context, use_short_ids)
            mapping[formatted_id.plain] = context_str

        return mapping

    @staticmethod
    @lru_cache(maxsize=1024)
    def _compute_hash(context_str: str) -> str:
        """Compute SHA256 hash for a context string."""
        return hashlib.sha256(context_str.encode("utf-8")).hexdigest()

    def _format_default_context(self) -> Text:
        """Format the default (empty) context."""
        if self._use_ascii:
            symbol = f"{DEFAULT_CONTEXT_SYMBOL_ASCII}-"
        else:
            symbol = DEFAULT_CONTEXT_EMOJI
        result = Text(symbol)
        result.append("global", style="" if self._no_color else f"bold {DEFAULT_CONTEXT_COLOR}")
        return result

    def _format_default_context_with_symbol(self) -> Text:
        """Format the default context with symbol prefix."""
        return self._format_default_context()

    def _format_deterministic_name(self, hex_hash: str, color: str) -> Text:
        """Format a deterministic name from hash."""
        name = generate_name(hex_hash)
        return Text(name, style="" if self._no_color else f"bold {color}")

    def _format_hash_id(self, hex_hash: str, color: str) -> Text:
        """Format a short hash identifier."""
        return Text(hex_hash[:6], style="" if self._no_color else f"bold {color}")

    def _get_symbol_for_hash(self, hex_hash: str) -> str:
        """Get a deterministic symbol/emoji for a hash.

        Returns symbol with separator: emoji directly or ASCII letter followed by '-'.
        """
        symbols = CONTEXT_SYMBOLS_ASCII if self._use_ascii else CONTEXT_EMOJIS
        symbol_index = int(hex_hash[8:10], 16) % len(symbols)
        symbol = symbols[symbol_index]
        return f"{symbol}-" if self._use_ascii else symbol

    def _get_color_for_hash(self, hex_hash: str) -> str:
        """Get a deterministic color for a hash."""
        color_index = int(hex_hash[10:12], 16) % len(CONTEXT_COLORS)
        return CONTEXT_COLORS[color_index]

    def _format_context_string(self, context_str: str) -> Text:
        """Format axis:value pairs with styling."""
        result = Text()
        parts = context_str.split("+")

        for i, part in enumerate(parts):
            if i > 0:
                result.append("+", style="" if self._no_color else "dim")

            if ":" in part:
                axis_name, axis_value = part.split(":", 1)
                result.append(axis_name, style="" if self._no_color else "magenta")
                result.append(":", style="" if self._no_color else "dim")
                result.append(axis_value, style="" if self._no_color else "yellow")
            else:
                result.append(part)

        return result


@lru_cache(maxsize=1)
def get_default_formatter(no_color: bool = False) -> ContextFormatter:
    """Get a cached ContextFormatter instance."""
    return ContextFormatter(no_color=no_color)
