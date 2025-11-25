"""Context system for multi-invocation support.

Contexts allow multiple invocations of the same action with different
axis values, arguments, and flags. This is inspired by DIStage's
context-based dependency injection.
"""

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class ContextId:
    """Unique identifier for an execution context.

    A context is determined by all axis values. Actions with the same
    axis configuration share the same context. Context IDs are used
    to differentiate between multiple invocations of the same action.

    Example:
        platform:jvm+scala:2.12.5
        platform:jvm+scala:3.3.0
    """

    axis_values: tuple[tuple[str, str], ...]
    """Sorted tuple of (axis_name, axis_value) pairs"""

    def __str__(self) -> str:
        """Format as axis1:value1+axis2:value2."""
        if not self.axis_values:
            return "default"
        return "+".join(f"{name}:{value}" for name, value in self.axis_values)

    @classmethod
    def from_dict(cls, axis_dict: Dict[str, str]) -> "ContextId":
        """Create a ContextId from a dictionary of axis values.

        Args:
            axis_dict: Dictionary mapping axis names to values

        Returns:
            ContextId with sorted axis values
        """
        sorted_pairs = tuple(sorted(axis_dict.items()))
        return cls(axis_values=sorted_pairs)

    def to_dict(self) -> Dict[str, str]:
        """Convert to dictionary of axis values.

        Returns:
            Dictionary mapping axis names to values
        """
        return dict(self.axis_values)

    @classmethod
    def empty(cls) -> "ContextId":
        """Create an empty context (for default/no axes)."""
        return cls(axis_values=())

    def reduce_to_axes(self, axis_names: set[str]) -> "ContextId":
        """Create a reduced context with only specified axes.

        Args:
            axis_names: Set of axis names to keep

        Returns:
            New ContextId with only the specified axes
        """
        filtered = tuple((name, value) for name, value in self.axis_values if name in axis_names)
        return ContextId(axis_values=filtered)


@dataclass(frozen=True)
class ExecutionContext:
    """Full execution context for an action invocation.

    Includes all configuration needed to execute an action in a specific context.
    """

    context_id: ContextId
    """The context identifier based on axis values"""

    args: Dict[str, str]
    """Arguments for this context (global + per-action merged)"""

    flags: Dict[str, bool]
    """Flags for this context (global + per-action merged)"""

    @property
    def axis_values(self) -> Dict[str, str]:
        """Get axis values as a dictionary."""
        return self.context_id.to_dict()
