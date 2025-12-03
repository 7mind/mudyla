"""Parser for expansions in bash scripts."""

import re
from typing import Optional

from ..ast.expansions import (
    Expansion,
    SystemExpansion,
    ActionExpansion,
    WeakActionExpansion,
    EnvExpansion,
    ArgsExpansion,
    FlagsExpansion,
    RetainedExpansion,
)


class ExpansionParser:
    """Parser for ${...} expansions in bash scripts."""

    # Pattern to match ${dot.separated.expression}
    EXPANSION_PATTERN = re.compile(r"\$\{([a-zA-Z][a-zA-Z0-9._-]*)\}")

    @classmethod
    def find_all_expansions(cls, script: str) -> list[Expansion]:
        """Find all expansions in a bash script.

        Args:
            script: Bash script content

        Returns:
            List of parsed expansions

        Raises:
            ValueError: If expansion format is invalid
        """
        expansions = []
        for match in cls.EXPANSION_PATTERN.finditer(script):
            original_text = match.group(0)
            expression = match.group(1)

            # Skip bash variable expansions (no dot) - only parse Mudyla expansions
            # Mudyla expansions always have format: ${prefix.rest}
            # Bash variables are just: ${variable}
            if '.' not in expression:
                continue

            expansion = cls._parse_expansion(original_text, expression)
            expansions.append(expansion)
        return expansions

    @classmethod
    def _parse_expansion(cls, original_text: str, expression: str) -> Expansion:
        """Parse a single expansion expression.

        Args:
            original_text: Original text including ${}
            expression: The dot-separated expression

        Returns:
            Parsed expansion

        Raises:
            ValueError: If expansion format is invalid
        """
        parts = expression.split(".", 1)
        if len(parts) < 1:
            raise ValueError(f"Invalid expansion: {original_text}")

        prefix = parts[0]

        if prefix == "sys":
            return cls._parse_system_expansion(original_text, parts)
        elif prefix == "action":
            return cls._parse_action_expansion(original_text, parts)
        elif prefix == "env":
            return cls._parse_env_expansion(original_text, parts)
        elif prefix == "args":
            return cls._parse_args_expansion(original_text, parts)
        elif prefix == "flags":
            return cls._parse_flags_expansion(original_text, parts)
        elif prefix == "retained":
            return cls._parse_retained_expansion(original_text, parts)
        else:
            raise ValueError(
                f"Unknown expansion prefix '{prefix}' in: {original_text}. "
                f"Valid prefixes: sys, action, env, args, flags"
            )

    @classmethod
    def _parse_system_expansion(
        cls, original_text: str, parts: list[str]
    ) -> SystemExpansion:
        """Parse system expansion: ${sys.variable-name}"""
        if len(parts) != 2:
            raise ValueError(
                f"Invalid system expansion: {original_text}. "
                "Expected format: ${{sys.variable-name}}"
            )
        variable_name = parts[1]
        return SystemExpansion(
            original_text=original_text, variable_name=variable_name
        )

    @classmethod
    def _parse_action_expansion(
        cls, original_text: str, parts: list[str]
    ) -> ActionExpansion | WeakActionExpansion:
        """Parse action expansion: ${action.action-name.variable-name} or ${action.weak.action-name.variable-name}"""
        if len(parts) != 2:
            raise ValueError(
                f"Invalid action expansion: {original_text}. "
                "Expected format: ${{action.action-name.variable-name}} or ${{action.weak.action-name.variable-name}}"
            )

        rest_parts = parts[1].split(".", 1)

        # Check if this is a weak dependency: ${action.weak.action-name.variable-name}
        if len(rest_parts) >= 1 and rest_parts[0] == "weak":
            if len(rest_parts) != 2:
                raise ValueError(
                    f"Invalid weak action expansion: {original_text}. "
                    "Expected format: ${{action.weak.action-name.variable-name}}"
                )

            # Parse the rest: action-name.variable-name
            weak_rest_parts = rest_parts[1].split(".", 1)
            if len(weak_rest_parts) != 2:
                raise ValueError(
                    f"Invalid weak action expansion: {original_text}. "
                    "Expected format: ${{action.weak.action-name.variable-name}}"
                )

            action_name = weak_rest_parts[0]
            variable_name = weak_rest_parts[1]

            return WeakActionExpansion(
                original_text=original_text,
                action_name=action_name,
                variable_name=variable_name,
            )

        # Regular strong dependency: ${action.action-name.variable-name}
        if len(rest_parts) != 2:
            raise ValueError(
                f"Invalid action expansion: {original_text}. "
                "Expected format: ${{action.action-name.variable-name}}"
            )

        action_name = rest_parts[0]
        variable_name = rest_parts[1]

        return ActionExpansion(
            original_text=original_text,
            action_name=action_name,
            variable_name=variable_name,
        )

    @classmethod
    def _parse_env_expansion(cls, original_text: str, parts: list[str]) -> EnvExpansion:
        """Parse environment variable expansion: ${env.VARIABLE_NAME}"""
        if len(parts) != 2:
            raise ValueError(
                f"Invalid env expansion: {original_text}. "
                "Expected format: ${{env.VARIABLE_NAME}}"
            )
        variable_name = parts[1]
        return EnvExpansion(original_text=original_text, variable_name=variable_name)

    @classmethod
    def _parse_args_expansion(
        cls, original_text: str, parts: list[str]
    ) -> ArgsExpansion:
        """Parse argument expansion: ${args.argument-name}"""
        if len(parts) != 2:
            raise ValueError(
                f"Invalid args expansion: {original_text}. "
                "Expected format: ${{args.argument-name}}"
            )
        argument_name = parts[1]
        return ArgsExpansion(original_text=original_text, argument_name=argument_name)

    @classmethod
    def _parse_flags_expansion(
        cls, original_text: str, parts: list[str]
    ) -> FlagsExpansion:
        """Parse flag expansion: ${flags.flag-name}"""
        if len(parts) != 2:
            raise ValueError(
                f"Invalid flags expansion: {original_text}. "
                "Expected format: ${{flags.flag-name}}"
            )
        flag_name = parts[1]
        return FlagsExpansion(original_text=original_text, flag_name=flag_name)

    @classmethod
    def _parse_retained_expansion(
        cls, original_text: str, parts: list[str]
    ) -> RetainedExpansion:
        """Parse retained expansion: ${retained.weak.action-name} or ${retained.soft.action-name}"""
        if len(parts) != 2:
            raise ValueError(
                f"Invalid retained expansion: {original_text}. "
                "Expected format: ${{retained.weak.action-name}} or ${{retained.soft.action-name}}"
            )
        
        rest_parts = parts[1].split(".", 1)
        if len(rest_parts) != 2:
             raise ValueError(
                f"Invalid retained expansion: {original_text}. "
                "Expected format: ${{retained.weak.action-name}} or ${{retained.soft.action-name}}"
            )
            
        type_qualifier = rest_parts[0]
        action_name = rest_parts[1]
        
        if type_qualifier not in ("weak", "soft"):
             raise ValueError(
                f"Invalid retained expansion: {original_text}. "
                "Expected 'weak' or 'soft' qualifier."
            )

        return RetainedExpansion(original_text=original_text, action_name=action_name)
