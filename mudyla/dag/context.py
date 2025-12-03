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

    A context is determined by axis values, arguments, and flags.
    Actions with the same configuration share the same context.
    Context IDs are used to differentiate between multiple invocations
    of the same action.

    Example:
        platform:jvm+scala:2.12.5
        platform:jvm+scala:3.3.0+args.mode:prod
    """

    axis_values: tuple[tuple[str, str], ...]
    """Sorted tuple of (axis_name, axis_value) pairs"""

    args: tuple[tuple[str, str], ...] = ()
    """Sorted tuple of (arg_name, arg_value) pairs"""

    flags: tuple[tuple[str, bool], ...] = ()
    """Sorted tuple of (flag_name, flag_value) pairs"""

    def __str__(self) -> str:
        """Format as axis:val+arg:val+flag:val."""
        parts = []
        
        for name, value in self.axis_values:
            parts.append(f"{name}:{value}")
            
        for name, value in self.args:
            parts.append(f"args.{name}:{value}")
            
        for name, value in self.flags:
            # Only include true flags to keep IDs shorter? 
            # Or explicit false too? Let's include all for uniqueness correctness.
            # Actually, flags are booleans.
            parts.append(f"flags.{name}:{str(value).lower()}")
            
        if not parts:
            return "default"
            
        return "+".join(parts)

    @classmethod
    def from_dict(
        cls, 
        axis_dict: Dict[str, str], 
        args: Dict[str, str] | None = None,
        flags: Dict[str, bool] | None = None
    ) -> "ContextId":
        """Create a ContextId from dictionaries of values.

        Args:
            axis_dict: Dictionary mapping axis names to values
            args: Dictionary mapping argument names to values
            flags: Dictionary mapping flag names to values

        Returns:
            ContextId with sorted values
        """
        sorted_axes = tuple(sorted(axis_dict.items()))
        sorted_args = tuple(sorted(args.items())) if args else ()
        sorted_flags = tuple(sorted(flags.items())) if flags else ()
        
        return cls(
            axis_values=sorted_axes,
            args=sorted_args,
            flags=sorted_flags
        )

    def to_dict(self) -> Dict[str, str]:
        """Convert axis values to dictionary.

        Returns:
            Dictionary mapping axis names to values
        """
        return dict(self.axis_values)
        
    def get_args_dict(self) -> Dict[str, str]:
        """Get arguments as dictionary."""
        return dict(self.args)
        
    def get_flags_dict(self) -> Dict[str, bool]:
        """Get flags as dictionary."""
        return dict(self.flags)

    @classmethod
    def empty(cls) -> "ContextId":
        """Create an empty context (for default/no axes)."""
        return cls(axis_values=())

    def reduce(
        self, 
        axis_names: set[str], 
        arg_names: set[str], 
        flag_names: set[str]
    ) -> "ContextId":
        """Create a reduced context with only specified axes, args, and flags.

        Args:
            axis_names: Set of axis names to keep
            arg_names: Set of argument names to keep
            flag_names: Set of flag names to keep

        Returns:
            New ContextId with only the specified components
        """
        filtered_axes = tuple((n, v) for n, v in self.axis_values if n in axis_names)
        filtered_args = tuple((n, v) for n, v in self.args if n in arg_names)
        filtered_flags = tuple((n, v) for n, v in self.flags if n in flag_names)
        
        return ContextId(
            axis_values=filtered_axes,
            args=filtered_args,
            flags=filtered_flags
        )


@dataclass(frozen=True)
class ExecutionContext:
    """Full execution context for an action invocation.

    Includes all configuration needed to execute an action in a specific context.
    """

    context_id: ContextId
    """The context identifier based on axis values, args, and flags"""

    @property
    def axis_values(self) -> Dict[str, str]:
        """Get axis values as a dictionary."""
        return self.context_id.to_dict()
        
    @property
    def args(self) -> Dict[str, str]:
        """Get arguments as a dictionary."""
        return self.context_id.get_args_dict()

    @property
    def flags(self) -> Dict[str, bool]:
        """Get flags as a dictionary."""
        return self.context_id.get_flags_dict()
