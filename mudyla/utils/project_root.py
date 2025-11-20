"""Utility for finding project root."""

from pathlib import Path
from typing import Optional


def find_project_root(start_path: Optional[Path] = None) -> Path:
    """Find project root by looking for .git directory.

    Args:
        start_path: Starting directory (defaults to current directory)

    Returns:
        Path to project root

    Raises:
        ValueError: If no .git directory found
    """
    if start_path is None:
        start_path = Path.cwd()

    current = start_path.resolve()

    while True:
        git_dir = current / ".git"
        if git_dir.exists() and git_dir.is_dir():
            return current

        parent = current.parent
        if parent == current:
            # Reached filesystem root
            raise ValueError(
                f"Could not find .git directory in any parent of {start_path}"
            )

        current = parent
