"""Context formatting utilities with Rich styling support.

Provides formatted representations of ContextId for display purposes.
All formatting methods return Rich Text objects with styling markers.

Styling is always applied - the Rich console handles no_color mode.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import TYPE_CHECKING

from rich.text import Text

from .names_generator import generate_name
from .symbols import SymbolsFormatter

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

CONTEXT_COLORS: tuple[str, ...] = (
    "cyan",
    "green",
    "yellow",
    "magenta",
    "red",
    "blue",
    "bright_cyan",
    "bright_green",
    "bright_yellow",
    "bright_magenta",
    "bright_red",
    "bright_blue",
    "color(37)",
    "color(73)",
    "color(107)",
    "color(133)",
    "color(137)",
    "color(167)",
    "color(68)",
    "color(71)",
    "color(101)",
    "color(131)",
    "color(139)",
    "color(143)",
    "color(67)",
    "color(103)",
    "color(109)",
    "color(145)",
    "color(175)",
    "color(179)",
)

DEFAULT_CONTEXT_COLOR = "cyan"


class ContextFormatter:
    """Formats context identifiers for display with Rich styling.

    All methods return Rich Text objects with styling. The no_color handling
    is delegated to the Rich console that prints these Text objects.
    """

    def __init__(self, symbols: SymbolsFormatter):
        """Initialize the context formatter.

        Args:
            symbols: SymbolsFormatter for emoji/ASCII symbol resolution
        """
        self._symbols = symbols

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
            return Text("default", style="dim")

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
            result.append(name, style=f"bold {color}")
            return result
        else:
            result = Text(symbol)
            result.append(hex_hash[:6], style=f"bold {color}")
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
        symbol = self._get_default_symbol()
        result = Text(symbol)
        result.append("global", style=f"bold {DEFAULT_CONTEXT_COLOR}")
        return result

    def _format_default_context_with_symbol(self) -> Text:
        """Format the default context with symbol prefix."""
        return self._format_default_context()

    def _format_deterministic_name(self, hex_hash: str, color: str) -> Text:
        """Format a deterministic name from hash."""
        name = generate_name(hex_hash)
        return Text(name, style=f"bold {color}")

    def _format_hash_id(self, hex_hash: str, color: str) -> Text:
        """Format a short hash identifier."""
        return Text(hex_hash[:6], style=f"bold {color}")

    def _get_default_symbol(self) -> str:
        """Get the default context symbol (emoji or ASCII)."""
        if self._symbols.supports_emoji:
            return self._symbols.Globe
        else:
            return f"{self._symbols.Globe}-"

    def _get_symbol_for_hash(self, hex_hash: str) -> str:
        """Get a deterministic symbol/emoji for a hash.

        Returns symbol with separator: emoji directly or ASCII letter followed by '-'.
        """
        use_ascii = not self._symbols.supports_emoji
        symbols = CONTEXT_SYMBOLS_ASCII if use_ascii else CONTEXT_EMOJIS
        symbol_index = int(hex_hash[8:10], 16) % len(symbols)
        symbol = symbols[symbol_index]
        return f"{symbol}-" if use_ascii else symbol

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
                result.append("+", style="dim")

            if ":" in part:
                axis_name, axis_value = part.split(":", 1)
                result.append(axis_name, style="magenta")
                result.append(":", style="dim")
                result.append(axis_value, style="yellow")
            else:
                result.append(part)

        return result
