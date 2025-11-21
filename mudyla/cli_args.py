"""Structured parsing helpers for CLI inputs."""

from dataclasses import dataclass
from typing import Dict, List


class CLIParseError(Exception):
    """Raised when CLI inputs cannot be parsed."""


@dataclass(frozen=True)
class ParsedCLIInputs:
    """Structured result for custom CLI inputs."""

    goals: List[str]
    custom_args: Dict[str, str]
    custom_flags: Dict[str, bool]
    axis_values: Dict[str, str]
    goal_warnings: List[str]


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
    Parse custom args/flags/axis and goals from positional and unknown tokens.

    Args:
        positional_goals: Positional arguments captured by argparse.
        unknown_tokens: Tokens not recognized by argparse.

    Returns:
        ParsedCLIInputs with structured data.

    Raises:
        CLIParseError: If any token is malformed (fail fast).
    """
    custom_args: Dict[str, str] = {}
    custom_flags: Dict[str, bool] = {}
    axis_values: Dict[str, str] = {}
    goals: List[str] = []
    goal_warnings: List[str] = []

    tokens = list(unknown_tokens) + list(positional_goals)
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]

        if token.startswith(AXIS_OPTION):
            remainder = token[len(AXIS_OPTION) :]
            if remainder.startswith(ASSIGNMENT_SEPARATOR):
                remainder = remainder[1:]
            if remainder:
                axis_name, axis_value = _split_axis_assignment(remainder)
                axis_values[axis_name] = axis_value
            else:
                if idx + 1 >= len(tokens):
                    raise CLIParseError("Expected name=value after --axis")
                axis_name, axis_value = _split_axis_assignment(tokens[idx + 1])
                axis_values[axis_name] = axis_value
                idx += 1  # Skip the consumed token

        elif token.startswith(OPTION_PREFIX):
            stripped = token[len(OPTION_PREFIX) :]
            if ASSIGNMENT_SEPARATOR in stripped:
                name, value = stripped.split(ASSIGNMENT_SEPARATOR, 1)
                if name.strip() == "":
                    raise CLIParseError(f"Malformed argument '{token}'")
                custom_args[name] = value
            else:
                if stripped.strip() == "":
                    raise CLIParseError(f"Malformed flag '{token}'")
                custom_flags[stripped] = True

        elif token.startswith(GOAL_PREFIX):
            goal_name = token[len(GOAL_PREFIX) :].strip()
            if goal_name == "":
                raise CLIParseError("Goal name cannot be empty")
            goals.append(goal_name)

        elif ASSIGNMENT_SEPARATOR in token:
            axis_name, axis_value = _split_axis_assignment(token)
            axis_values[axis_name] = axis_value

        else:
            goals.append(token)
            goal_warnings.append(
                f"Goal should start with ':', got: {token}"
            )

        idx += 1

    return ParsedCLIInputs(
        goals=goals,
        custom_args=custom_args,
        custom_flags=custom_flags,
        axis_values=axis_values,
        goal_warnings=goal_warnings,
    )
