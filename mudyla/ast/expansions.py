"""Expansion classes for Mudyla."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from .types import ExpansionType


@dataclass(frozen=True)
class Expansion(ABC):
    """Base class for all expansions."""

    original_text: str
    """Original expansion text including ${}"""

    @abstractmethod
    def get_type(self) -> ExpansionType:
        """Get the expansion type."""
        pass

    @abstractmethod
    def resolve(self, context: dict[str, Any]) -> str:
        """Resolve the expansion to a concrete value.

        Args:
            context: Resolution context containing all necessary values

        Returns:
            Resolved string value

        Raises:
            ValueError: If expansion cannot be resolved
        """
        pass


@dataclass(frozen=True)
class SystemExpansion(Expansion):
    """System variable expansion: ${sys.variable-name}"""

    variable_name: str

    def get_type(self) -> ExpansionType:
        return ExpansionType.SYSTEM

    def resolve(self, context: dict[str, Any]) -> str:
        if self.variable_name.startswith("axis."):
            axis_values = context.get("axis")
            axis_name = self.variable_name[len("axis.") :]
            if axis_values is None or axis_name == "":
                raise ValueError(
                    f"Axis values not available when resolving '{self.variable_name}'"
                )
            if axis_name not in axis_values:
                raise ValueError(
                    f"Axis '{axis_name}' not found in context for expansion '{self.original_text}'"
                )
            return str(axis_values[axis_name])

        sys_vars = context.get("sys", {})
        if self.variable_name not in sys_vars:
            raise ValueError(
                f"System variable '{self.variable_name}' not found in context"
            )
        return str(sys_vars[self.variable_name])


@dataclass(frozen=True)
class ActionExpansion(Expansion):
    """Action output expansion: ${action.action-name.variable-name}"""

    action_name: str
    variable_name: str

    def get_type(self) -> ExpansionType:
        return ExpansionType.ACTION

    def resolve(self, context: dict[str, Any]) -> str:
        actions = context.get("actions", {})
        if self.action_name not in actions:
            raise ValueError(
                f"Action '{self.action_name}' output not found in context"
            )

        action_outputs = actions[self.action_name]
        if self.variable_name not in action_outputs:
            raise ValueError(
                f"Variable '{self.variable_name}' not found in action '{self.action_name}' outputs"
            )

        return str(action_outputs[self.variable_name])

    def get_dependency_action(self) -> str:
        """Get the action name this expansion depends on."""
        return self.action_name

    def is_weak(self) -> bool:
        """Return False to indicate this is a strong dependency."""
        return False


@dataclass(frozen=True)
class WeakActionExpansion(Expansion):
    """Weak action output expansion: ${action.weak.action-name.variable-name}

    Unlike ActionExpansion, if the action is not available (was pruned due to
    being only weakly depended on), this returns an empty string instead of
    raising an error.
    """

    action_name: str
    variable_name: str

    def get_type(self) -> ExpansionType:
        return ExpansionType.ACTION

    def resolve(self, context: dict[str, Any]) -> str:
        actions = context.get("actions", {})

        # If action was pruned (not retained), return empty string
        if self.action_name not in actions:
            return ""

        action_outputs = actions[self.action_name]

        # If variable not provided, return empty string (graceful degradation)
        if self.variable_name not in action_outputs:
            return ""

        value = action_outputs[self.variable_name]

        # Handle None values (return empty string)
        if value is None:
            return ""

        return str(value)

    def get_dependency_action(self) -> str:
        """Get the action name this expansion depends on."""
        return self.action_name

    def is_weak(self) -> bool:
        """Return True to indicate this is a weak dependency."""
        return True


@dataclass(frozen=True)
class EnvExpansion(Expansion):
    """Environment variable expansion: ${env.VARIABLE_NAME}"""

    variable_name: str

    def get_type(self) -> ExpansionType:
        return ExpansionType.ENV

    def resolve(self, context: dict[str, Any]) -> str:
        env_vars = context.get("env", {})
        if self.variable_name not in env_vars:
            raise ValueError(
                f"Environment variable '{self.variable_name}' not found in context"
            )
        return str(env_vars[self.variable_name])


@dataclass(frozen=True)
class ArgsExpansion(Expansion):
    """Command-line argument expansion: ${args.argument-name}"""

    argument_name: str

    def get_type(self) -> ExpansionType:
        return ExpansionType.ARGS

    def resolve(self, context: dict[str, Any]) -> str:
        args = context.get("args", {})
        if self.argument_name not in args:
            raise ValueError(f"Argument '{self.argument_name}' not found in context")
        return str(args[self.argument_name])


@dataclass(frozen=True)
class FlagsExpansion(Expansion):
    """Command-line flag expansion: ${flags.flag-name}"""

    flag_name: str

    def get_type(self) -> ExpansionType:
        return ExpansionType.FLAGS

    def resolve(self, context: dict[str, Any]) -> str:
        flags = context.get("flags", {})
        if self.flag_name not in flags:
            raise ValueError(f"Flag '{self.flag_name}' not found in context")
        # Flags are always 0 or 1
        return "1" if flags[self.flag_name] else "0"
