"""Wildcard expansion for axis values.

Supports:
- "*" matches all values for an axis
- "prefix*" matches all values starting with prefix
"""

from dataclasses import dataclass
from typing import Dict, List, Set, Union

from .ast.models import ParsedDocument, AxisDefinition
from .cli_args import ActionInvocation, ArgValue


def _make_hashable(value: ArgValue) -> Union[str, tuple]:
    """Convert an ArgValue to a hashable type (lists become tuples)."""
    if isinstance(value, list):
        return tuple(value)
    return value


def _make_args_hashable(args: Dict[str, ArgValue]) -> tuple:
    """Convert args dict to a hashable tuple, handling list values."""
    return tuple(sorted((k, _make_hashable(v)) for k, v in args.items()))


def matches_pattern(value: str, pattern: str) -> bool:
    """Check if an axis value matches a wildcard pattern.

    Args:
        value: The axis value to test
        pattern: The pattern to match against (supports "*" and "prefix*")

    Returns:
        True if the value matches the pattern

    Examples:
        >>> matches_pattern("2.13.0", "*")
        True
        >>> matches_pattern("2.13.0", "2.13*")
        True
        >>> matches_pattern("3.3.0", "2.13*")
        False
        >>> matches_pattern("jvm", "jvm")
        True
    """
    if pattern == "*":
        return True
    if pattern.endswith("*"):
        prefix = pattern[:-1]
        return value.startswith(prefix)
    return value == pattern


def expand_axis_pattern(
    axis_name: str, pattern: str, axis_def: AxisDefinition
) -> List[str]:
    """Expand a wildcard pattern to all matching axis values.

    Args:
        axis_name: Name of the axis
        pattern: Pattern to expand (e.g., "*", "2.13*", or exact value)
        axis_def: Axis definition containing all possible values

    Returns:
        List of matching axis values

    Raises:
        ValueError: If pattern matches no values
    """
    matching_values = [
        axis_value.value
        for axis_value in axis_def.values
        if matches_pattern(axis_value.value, pattern)
    ]

    if not matching_values:
        available = [av.value for av in axis_def.values]
        raise ValueError(
            f"Pattern '{pattern}' for axis '{axis_name}' matches no values. "
            f"Available values: {', '.join(available)}"
        )

    return matching_values


@dataclass(frozen=True)
class AxisCombination:
    """A specific combination of axis values."""

    values: Dict[str, str]
    """Map of axis name to concrete value"""

    def __hash__(self) -> int:
        return hash(tuple(sorted(self.values.items())))


def expand_invocation_wildcards(
    invocation: ActionInvocation, document: ParsedDocument
) -> List[ActionInvocation]:
    """Expand wildcards in an action invocation to generate all matching invocations.

    Args:
        invocation: Original invocation (may contain wildcards in axes)
        document: Parsed document containing axis definitions

    Returns:
        List of action invocations with concrete axis values (no wildcards)

    Examples:
        If invocation has axes={"scala": "2.13*"} and document defines
        scala with values ["2.13.0", "2.13.5", "3.3.0"], this returns
        two invocations: one with scala=2.13.0 and one with scala=2.13.5
    """
    # Separate wildcard axes from concrete axes
    wildcard_axes: Dict[str, str] = {}
    concrete_axes: Dict[str, str] = {}

    for axis_name, axis_pattern in invocation.axes.items():
        if "*" in axis_pattern:
            wildcard_axes[axis_name] = axis_pattern
        else:
            concrete_axes[axis_name] = axis_pattern

    # If no wildcards, return original invocation
    if not wildcard_axes:
        return [invocation]

    # Expand each wildcard axis to its matching values
    expanded_axes: Dict[str, List[str]] = {}
    for axis_name, pattern in wildcard_axes.items():
        if axis_name not in document.axis:
            raise ValueError(f"Unknown axis '{axis_name}'")
        axis_def = document.axis[axis_name]
        expanded_axes[axis_name] = expand_axis_pattern(axis_name, pattern, axis_def)

    # Generate all combinations
    combinations: List[Dict[str, str]] = [{}]
    for axis_name, values in expanded_axes.items():
        new_combinations: List[Dict[str, str]] = []
        for combo in combinations:
            for value in values:
                new_combo = dict(combo)
                new_combo[axis_name] = value
                new_combinations.append(new_combo)
        combinations = new_combinations

    # Create invocations for each combination
    result: List[ActionInvocation] = []
    for combo in combinations:
        # Merge concrete axes with this combination
        merged_axes = dict(concrete_axes)
        merged_axes.update(combo)

        result.append(
            ActionInvocation(
                action_name=invocation.action_name,
                args=invocation.args,
                flags=invocation.flags,
                axes=merged_axes,
            )
        )

    return result


def expand_all_wildcards(
    cli_inputs, document: ParsedDocument
):
    """Expand wildcards in both global axes and per-action axes.

    This generates all necessary action invocations by expanding:
    1. Global axis wildcards (apply to all actions)
    2. Per-action axis wildcards (apply to specific action)

    Args:
        cli_inputs: Parsed CLI inputs (may contain wildcards)
        document: Parsed document containing axis definitions

    Returns:
        New ParsedCLIInputs with all wildcards expanded into concrete invocations

    Raises:
        ValueError: If any wildcard pattern matches no values
    """
    from .cli_args import ParsedCLIInputs

    # Expand global axis wildcards
    global_wildcard_axes: Dict[str, str] = {}
    global_concrete_axes: Dict[str, str] = {}

    for axis_name, axis_pattern in cli_inputs.global_axes.items():
        if "*" in axis_pattern:
            global_wildcard_axes[axis_name] = axis_pattern
        else:
            global_concrete_axes[axis_name] = axis_pattern

    # Generate all global axis combinations
    global_combinations: List[Dict[str, str]] = [{}]
    for axis_name, pattern in global_wildcard_axes.items():
        if axis_name not in document.axis:
            raise ValueError(f"Unknown axis '{axis_name}'")
        axis_def = document.axis[axis_name]
        matching_values = expand_axis_pattern(axis_name, pattern, axis_def)

        new_combinations: List[Dict[str, str]] = []
        for combo in global_combinations:
            for value in matching_values:
                new_combo = dict(combo)
                new_combo[axis_name] = value
                new_combinations.append(new_combo)
        global_combinations = new_combinations

    # Merge concrete global axes into each combination
    for combo in global_combinations:
        combo.update(global_concrete_axes)

    # If no global wildcards, we have just one combination (the concrete axes)
    if not global_wildcard_axes:
        global_combinations = [dict(global_concrete_axes)]

    # Expand per-action wildcards for each action invocation and global combination
    expanded_invocations: List[ActionInvocation] = []
    seen_combinations: Set[tuple] = set()  # To deduplicate

    for invocation in cli_inputs.action_invocations:
        # Expand this invocation's wildcards
        per_action_expanded = expand_invocation_wildcards(invocation, document)

        # For each global combination, create an invocation
        for global_combo in global_combinations:
            for per_action_inv in per_action_expanded:
                # Merge global and per-action axes
                merged_axes = dict(global_combo)
                merged_axes.update(per_action_inv.axes)

                # Create unique key for deduplication
                combo_key = (
                    invocation.action_name,
                    tuple(sorted(merged_axes.items())),
                    _make_args_hashable(per_action_inv.args),
                    tuple(sorted(per_action_inv.flags.items())),
                )

                if combo_key not in seen_combinations:
                    seen_combinations.add(combo_key)
                    expanded_invocations.append(
                        ActionInvocation(
                            action_name=invocation.action_name,
                            args=per_action_inv.args,
                            flags=per_action_inv.flags,
                            axes=merged_axes,
                        )
                    )

    # Return new ParsedCLIInputs with expanded invocations
    # Global axes become empty since they're now merged into each invocation
    return ParsedCLIInputs(
        global_args=cli_inputs.global_args,
        global_flags=cli_inputs.global_flags,
        global_axes={},  # Expanded into invocations
        action_invocations=expanded_invocations,
        goal_warnings=cli_inputs.goal_warnings,
    )
