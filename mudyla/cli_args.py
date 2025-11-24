"""Structured parsing helpers for CLI inputs."""

from dataclasses import dataclass
from typing import Dict, List


class CLIParseError(Exception):
    """Raised when CLI inputs cannot be parsed."""


@dataclass(frozen=True)
class ActionInvocation:
    """Represents a single action invocation with its specific configuration."""

    action_name: str
    args: Dict[str, str]
    flags: Dict[str, bool]
    axes: Dict[str, str]


@dataclass(frozen=True)
class ParsedCLIInputs:
    """Structured result for custom CLI inputs with per-action configurations."""

    global_args: Dict[str, str]
    global_flags: Dict[str, bool]
    global_axes: Dict[str, str]
    action_invocations: List[ActionInvocation]
    goal_warnings: List[str]

    @property
    def goals(self) -> List[str]:
        """Legacy property for compatibility - returns list of action names."""
        return [inv.action_name for inv in self.action_invocations]

    @property
    def custom_args(self) -> Dict[str, str]:
        """Legacy property - returns global args."""
        return self.global_args

    @property
    def custom_flags(self) -> Dict[str, bool]:
        """Legacy property - returns global flags."""
        return self.global_flags

    @property
    def axis_values(self) -> Dict[str, str]:
        """Legacy property - returns global axes."""
        return self.global_axes


AXIS_OPTION = "--axis"
OPTION_PREFIX = "--"
GOAL_PREFIX = ":"
ASSIGNMENT_SEPARATOR = "="


def _split_axis_assignment(token: str) -> tuple[str, str]:
    """Split an axis assignment of the form name=value."""
    if ASSIGNMENT_SEPARATOR not in token:
        raise CLIParseError(
            f"Axis specification '{token}' is invalid. Expected format name=value."
        )
    name, value = token.split(ASSIGNMENT_SEPARATOR, 1)
    if name.strip() == "" or value.strip() == "":
        raise CLIParseError(
            f"Axis specification '{token}' is invalid. Both name and value must be non-empty."
        )
    return name.strip(), value.strip()


def parse_custom_inputs(
    positional_goals: List[str], unknown_tokens: List[str]
) -> ParsedCLIInputs:
    """
    Parse custom args/flags/axis and action invocations from tokens.

    Supports both global and per-action configurations:
    - Tokens before first :action are global
    - Tokens after :action belong to that action until next :action

    Example:
        mdl --axis platform=jvm :build --axis scala=2.12.5 --out-dir ./out1 :build --axis scala=3.3.0

    Args:
        positional_goals: Positional arguments captured by argparse.
        unknown_tokens: Tokens not recognized by argparse.

    Returns:
        ParsedCLIInputs with structured data including action invocations.

    Raises:
        CLIParseError: If any token is malformed (fail fast).
    """
    global_args: Dict[str, str] = {}
    global_flags: Dict[str, bool] = {}
    global_axes: Dict[str, str] = {}
    action_invocations: List[ActionInvocation] = []
    goal_warnings: List[str] = []

    # Current action being configured (None means we're in global scope)
    current_action_name: str | None = None
    current_args: Dict[str, str] = {}
    current_flags: Dict[str, bool] = {}
    current_axes: Dict[str, str] = {}

    def finalize_current_action() -> None:
        """Finalize the current action invocation if one is active."""
        nonlocal current_action_name, current_args, current_flags, current_axes
        if current_action_name is not None:
            action_invocations.append(
                ActionInvocation(
                    action_name=current_action_name,
                    args=dict(current_args),
                    flags=dict(current_flags),
                    axes=dict(current_axes),
                )
            )
            current_action_name = None
            current_args = {}
            current_flags = {}
            current_axes = {}

    tokens = list(unknown_tokens) + list(positional_goals)
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]

        # Handle action goals (start new action context)
        if token.startswith(GOAL_PREFIX):
            goal_name = token[len(GOAL_PREFIX) :].strip()
            if goal_name == "":
                raise CLIParseError("Goal name cannot be empty")

            # Finalize previous action if any
            finalize_current_action()

            # Start new action context
            current_action_name = goal_name

        # Handle axis
        elif token.startswith(AXIS_OPTION):
            remainder = token[len(AXIS_OPTION) :]
            if remainder.startswith(ASSIGNMENT_SEPARATOR):
                remainder = remainder[1:]
            if remainder:
                axis_name, axis_value = _split_axis_assignment(remainder)
            else:
                if idx + 1 >= len(tokens):
                    raise CLIParseError("Expected name=value after --axis")
                axis_name, axis_value = _split_axis_assignment(tokens[idx + 1])
                idx += 1  # Skip the consumed token

            # Add to current context (global or action-specific)
            if current_action_name is None:
                global_axes[axis_name] = axis_value
            else:
                current_axes[axis_name] = axis_value

        # Handle arguments and flags
        elif token.startswith(OPTION_PREFIX):
            stripped = token[len(OPTION_PREFIX) :]
            if ASSIGNMENT_SEPARATOR in stripped:
                name, value = stripped.split(ASSIGNMENT_SEPARATOR, 1)
                if name.strip() == "":
                    raise CLIParseError(f"Malformed argument '{token}'")

                # Add to current context
                if current_action_name is None:
                    global_args[name] = value
                else:
                    current_args[name] = value
            else:
                if stripped.strip() == "":
                    raise CLIParseError(f"Malformed flag '{token}'")

                # Add to current context
                if current_action_name is None:
                    global_flags[stripped] = True
                else:
                    current_flags[stripped] = True

        # Handle shorthand axis notation (name=value without --axis prefix)
        elif ASSIGNMENT_SEPARATOR in token:
            axis_name, axis_value = _split_axis_assignment(token)

            # Add to current context
            if current_action_name is None:
                global_axes[axis_name] = axis_value
            else:
                current_axes[axis_name] = axis_value

        # Handle unprefixed goals (legacy support with warning)
        else:
            # Finalize previous action if any
            finalize_current_action()

            # Start new action context with warning
            current_action_name = token
            goal_warnings.append(f"Goal should start with ':', got: {token}")

        idx += 1

    # Finalize last action if any
    finalize_current_action()

    # Validate: detect contradictory axis values (global vs per-action)
    for invocation in action_invocations:
        for axis_name, action_value in invocation.axes.items():
            if axis_name in global_axes:
                global_value = global_axes[axis_name]
                if global_value != action_value:
                    raise CLIParseError(
                        f"Contradictory axis values for '{axis_name}': "
                        f"global={global_value}, action '{invocation.action_name}'={action_value}. "
                        f"Global and per-action axes must not conflict."
                    )

    return ParsedCLIInputs(
        global_args=global_args,
        global_flags=global_flags,
        global_axes=global_axes,
        action_invocations=action_invocations,
        goal_warnings=goal_warnings,
    )
