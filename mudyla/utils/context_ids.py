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


def _symbol_for_short_id(short_id: str) -> str:
    """Pick a stable emoji/ASCII symbol for the provided short ID."""
    symbols = _context_symbols()
    symbol_index = int(short_id[:2], 16) % len(symbols)
    return symbols[symbol_index]


def format_short_context_id(context_str: str) -> str:
    """Format a context as <symbol><short-hash> (e.g. 🔴79d776)."""
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
