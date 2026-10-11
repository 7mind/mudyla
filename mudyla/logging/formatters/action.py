"""Action formatting utilities with Rich styling support.

Provides formatted representations of ActionKey for display purposes.
All formatting methods return Rich Text objects with styling markers.

Styling is always applied - the Rich console handles no_color mode.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from typing import TYPE_CHECKING, Iterable, TypeAlias

from rich.text import Text

from .context import ContextFormatter

if TYPE_CHECKING:
    from ...dag.graph import ActionId, ActionKey


TRUNCATED_HASH_LENGTH = 7
MAX_DIRNAME_LENGTH = 64
ContextValue: TypeAlias = str | tuple[str, ...] | bool


def _single_line(value: str) -> str:
    return "".join(json.dumps(char)[1:-1] if unicodedata.category(char) in {"Cc", "Cs", "Zl", "Zp"}
                   else char for char in value)


def _group_value(value: ContextValue) -> str:
    if isinstance(value, str) and re.fullmatch(r"[\w./:+-]+", value):
        return value
    return _single_line(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _varying_fields(context_fields: Iterable[tuple[tuple[str, ContextValue], ...]]) -> list[str]:
    values = [dict(fields) for fields in context_fields]
    names = sorted({name for fields in values for name in fields})
    return [name for name in names if len({fields.get(name) for fields in values}) > 1]


def _named_group_value(name: str, values: dict[str, ContextValue]) -> str:
    if name not in values:
        return f"{name} is not set"
    return f"{name}={_group_value(values[name])}"


def _action_group_name(key: ActionKey, axes: list[str], arguments: list[str], flags: list[str],
                       named_axes: bool) -> str:
    name = _single_line(key.id.name)
    context = key.context_id
    if not context.axis_values and not context.args and not context.flags:
        return name + " for global"
    axis_values: dict[str, ContextValue] = dict(context.axis_values)
    axis_parts = [_named_group_value(axis, axis_values) if named_axes or axis not in axis_values
                  else _group_value(axis_values[axis]) for axis in axes]
    argument_values: dict[str, ContextValue] = dict(context.args)
    flag_values: dict[str, ContextValue] = dict(context.flags)
    fields = [_named_group_value(argument, argument_values) for argument in arguments]
    fields.extend(_named_group_value(flag, flag_values) for flag in flags)
    if axis_parts:
        name += " for " + ", ".join(axis_parts)
    if fields:
        name += (" with " if axis_parts else " where ") + ", ".join(fields)
    return name


def action_group_names(action_keys: Iterable[ActionKey]) -> dict[ActionKey, str]:
    groups: dict[ActionId, list[ActionKey]] = {}
    for key in set(action_keys):
        groups.setdefault(key.id, []).append(key)
    names: dict[ActionKey, str] = {}
    for keys in groups.values():
        if len(keys) == 1:
            names[keys[0]] = _single_line(keys[0].id.name)
            continue
        axes = _varying_fields(key.context_id.axis_values for key in keys)
        arguments = _varying_fields(key.context_id.args for key in keys)
        flags = _varying_fields(key.context_id.flags for key in keys)
        titles = {key: _action_group_name(key, axes, arguments, flags, False) for key in keys}
        counts = Counter(titles.values())
        for key, title in titles.items():
            names[key] = _action_group_name(key, axes, arguments, flags, True) if counts[title] > 1 else title
        assert len({names[key] for key in keys}) == len(keys), "CI group names must distinguish action contexts"
    return names


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

    if "#" in name:
        hash_pos = name.rfind("#")
        action_suffix = name[hash_pos:]
        context_prefix = name[:hash_pos]

        available_for_prefix = max_length - len(action_suffix) - 3 - TRUNCATED_HASH_LENGTH

        if available_for_prefix > 0:
            truncated_prefix = context_prefix[:available_for_prefix]
            return f"{truncated_prefix}...{short_hash}{action_suffix}"

    truncated_length = max_length - TRUNCATED_HASH_LENGTH - 3
    truncated_name = name[:truncated_length]

    return f"{truncated_name}...{short_hash}"


class ActionFormatter:
    """Formats action keys for display with Rich styling.

    All methods return Rich Text objects with styling. The no_color handling
    is delegated to the Rich console that prints these Text objects.
    """

    def __init__(self, context_formatter: ContextFormatter):
        """Initialize the action formatter.

        Args:
            context_formatter: The context formatter to use for context formatting
        """
        self._context_formatter = context_formatter

    @property
    def context(self) -> ContextFormatter:
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
        action_name = str(action_key.id)

        context_text = self._context_formatter.format_id_with_symbol(
            action_key.context_id, use_short_ids
        )

        result = Text()
        result.append_text(context_text)
        result.append("#", style="dim")
        result.append(action_name, style="bold cyan")

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
        result.append("#", style="dim")
        result.append(action_name, style="bold cyan")

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
