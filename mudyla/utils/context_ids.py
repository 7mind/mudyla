"""Utilities for deterministic context identifiers and display labels."""

from __future__ import annotations

import hashlib
import platform
from functools import lru_cache
from typing import Iterable

from ..dag.graph import ActionKey

# Preset of 32 distinctive emojis for context ID prefixes (non-Windows)
CONTEXT_EMOJIS: tuple[str, ...] = (
    "🔴", "🟠", "🟡", "🟢", "🔵", "🟣", "🟤", "⚫",
    "🟥", "🟧", "🟨", "🟩", "🟦", "🟪", "🟫", "⬛",
    "⭐", "🌟", "💫", "✨", "🔶", "🔷", "🔸", "🔹",
    "❤️", "🧡", "💛", "💚", "💙", "💜", "🖤", "🤍",
)

# Special emoji for the default/global context (no axes)
DEFAULT_CONTEXT_EMOJI = "🌍"
DEFAULT_CONTEXT_SYMBOL_ASCII = "*"

# ASCII-compatible symbols for context ID prefixes (Windows fallback)
CONTEXT_SYMBOLS_ASCII: tuple[str, ...] = (
    "A", "B", "C", "D", "E", "F", "G", "H",
    "J", "K", "L", "M", "N", "P", "Q", "R",
    "S", "T", "U", "V", "W", "X", "Y", "Z",
    "1", "2", "3", "4", "5", "6", "7", "8",
)


@lru_cache(maxsize=1)
def _context_symbols() -> tuple[str, ...]:
    """Return the platform-appropriate symbol set for contexts."""
    return CONTEXT_SYMBOLS_ASCII if platform.system() == "Windows" else CONTEXT_EMOJIS


def generate_short_context_id(context_str: str) -> str:
    """Return the deterministic 6-character hash for a context string."""
    digest = hashlib.sha256(context_str.encode("utf-8")).hexdigest()
    return digest[:6]


# Length of truncated hash suffix (similar to git's abbreviated commits)
TRUNCATED_HASH_LENGTH = 7
# Maximum length for directory names to avoid filesystem limits
MAX_DIRNAME_LENGTH = 64


def truncate_dirname(name: str, max_length: int = MAX_DIRNAME_LENGTH) -> str:
    """Truncate a directory name to max_length, adding a hash suffix if needed.

    If the name exceeds max_length, it is truncated and a short hash of the
    original name is appended, similar to git's abbreviated commit hashes.

    If the name contains '#' (indicating a context#action format), the action
    name suffix is preserved to allow finding actions by name.

    Args:
        name: The original directory name
        max_length: Maximum allowed length (default 64)

    Returns:
        The original name if within limit, otherwise truncated with hash suffix
    """
    if len(name) <= max_length:
        return name

    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
    short_hash = digest[:TRUNCATED_HASH_LENGTH]

    # Check if name has context#action format - preserve the action name
    if "#" in name:
        hash_pos = name.rfind("#")
        action_suffix = name[hash_pos:]  # includes the #
        context_prefix = name[:hash_pos]

        # Calculate available space for context prefix:
        # max_length - len(action_suffix) - len("...") - len(short_hash)
        available_for_prefix = max_length - len(action_suffix) - 3 - TRUNCATED_HASH_LENGTH

        if available_for_prefix > 0:
            truncated_prefix = context_prefix[:available_for_prefix]
            return f"{truncated_prefix}...{short_hash}{action_suffix}"

    # Fallback: simple truncation without preserving action suffix
    truncated_length = max_length - TRUNCATED_HASH_LENGTH - 3  # 3 for "..."
    truncated_name = name[:truncated_length]

    return f"{truncated_name}...{short_hash}"


def _symbol_for_short_id(short_id: str) -> str:
    """Pick a stable emoji/ASCII symbol for the provided short ID."""
    symbols = _context_symbols()
    symbol_index = int(short_id[:2], 16) % len(symbols)
    return symbols[symbol_index]


def format_short_context_id(context_str: str) -> str:
    """Format a context as <symbol><short-hash> (e.g. 🔴79d776).

    The default context (no axes) gets a special globe emoji.
    """
    if context_str == "default":
        prefix = DEFAULT_CONTEXT_SYMBOL_ASCII if platform.system() == "Windows" else DEFAULT_CONTEXT_EMOJI
        return f"{prefix}global"

    short_id = generate_short_context_id(context_str)
    prefix = _symbol_for_short_id(short_id)
    return f"{prefix}{short_id}"


def format_action_label(action_key: ActionKey, *, use_short_ids: bool) -> str:
    """Format an action key for display, optionally using short context IDs."""
    if not use_short_ids:
        return str(action_key)

    context_label = format_short_context_id(str(action_key.context_id))
    action_name = str(action_key.id)
    return f"{context_label}#{action_name}"


def build_context_mapping(action_keys: Iterable[ActionKey]) -> dict[str, str]:
    """Return mapping from formatted short IDs to full context strings."""
    mapping: dict[str, str] = {}
    seen_contexts: set[str] = set()

    for action_key in action_keys:
        context_str = str(action_key.context_id)
        if context_str in seen_contexts:
            continue
        seen_contexts.add(context_str)
        mapping[format_short_context_id(context_str)] = context_str

    return mapping
