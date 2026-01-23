"""String utilities for formatting and manipulation."""

from __future__ import annotations

import hashlib


TRUNCATED_HASH_LENGTH = 7
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
