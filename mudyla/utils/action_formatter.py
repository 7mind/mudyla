"""Action formatting utilities with Rich styling support.

Provides formatted representations of ActionKey for display purposes.
All formatting methods return Rich Text objects with styling markers.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Iterable

from rich.text import Text

from .context_formatter import ContextFormatter
from .string_utils import MAX_DIRNAME_LENGTH, TRUNCATED_HASH_LENGTH, truncate_dirname

if TYPE_CHECKING:
    from ..dag.graph import ActionKey


class ActionFormatter:
    """Formats action keys for display with Rich styling."""

    def __init__(self, no_color: bool = False):
        """Initialize the action formatter.

        Args:
            no_color: If True, disable all styling
        """
        self._no_color = no_color
        self._context_formatter = ContextFormatter(no_color=no_color)

    @property
    def context_formatter(self) -> ContextFormatter:
        """Get the underlying context formatter."""
        return self._context_formatter

    def format_label(self, action_key: ActionKey, use_short_ids: bool) -> Text:
        """Format an action key for display.

        Args:
            action_key: The action key to format
            use_short_ids: If True, use short context IDs; if False, use full context

        Returns:
            Rich Text with styled action label
        """
        context_str = str(action_key.context_id)
        action_name = str(action_key.id)

        context_text = self._context_formatter.format_id_with_symbol(
            action_key.context_id, use_short_ids
        )

        result = Text()
        result.append_text(context_text)
        result.append("#", style="" if self._no_color else "dim")
        result.append(action_name, style="" if self._no_color else "bold cyan")

        return result

    def format_label_plain(self, action_key: ActionKey, use_short_ids: bool) -> str:
        """Format an action key as plain string without styling.

        Args:
            action_key: The action key to format
            use_short_ids: If True, use short context IDs; if False, use full context

        Returns:
            Plain string representation
        """
        return self.format_label(action_key, use_short_ids).plain

    def format_full(self, action_key: ActionKey) -> Text:
        """Format an action key with full context representation.

        Args:
            action_key: The action key to format

        Returns:
            Rich Text with styled full action representation
        """
        action_name = str(action_key.id)

        context_text = self._context_formatter.format_full(action_key.context_id)

        result = Text()
        result.append_text(context_text)
        result.append("#", style="" if self._no_color else "dim")
        result.append(action_name, style="" if self._no_color else "bold cyan")

        return result

    def build_context_mapping(
        self, action_keys: Iterable[ActionKey], use_short_ids: bool
    ) -> dict[str, str]:
        """Build mapping from formatted short IDs to full context strings.

        Args:
            action_keys: Action keys to build mapping from
            use_short_ids: If True, use short context IDs

        Returns:
            Dictionary mapping formatted short IDs to full context strings
        """
        mapping: dict[str, str] = {}
        seen_contexts: set[str] = set()

        for action_key in action_keys:
            context_str = str(action_key.context_id)
            if context_str in seen_contexts or context_str == "default":
                continue
            seen_contexts.add(context_str)

            formatted_id = self._context_formatter.format_id_with_symbol(
                action_key.context_id, use_short_ids
            )
            mapping[formatted_id.plain] = context_str

        return mapping

    @staticmethod
    def truncate_dirname(name: str, max_length: int = MAX_DIRNAME_LENGTH) -> str:
        """Truncate a directory name to max_length, adding a hash suffix if needed.

        Delegates to string_utils.truncate_dirname.
        """
        return truncate_dirname(name, max_length)


@lru_cache(maxsize=1)
def get_default_formatter(no_color: bool = False) -> ActionFormatter:
    """Get a cached ActionFormatter instance."""
    return ActionFormatter(no_color=no_color)
