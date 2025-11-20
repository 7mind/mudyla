"""Parser for return declarations in bash scripts."""

import re
from typing import Optional

from ..ast.models import ReturnDeclaration, SourceLocation
from ..ast.types import ReturnType


class ReturnParser:
    """Parser for 'ret' declarations in bash scripts."""

    # Pattern to match: ret name:type=value
    # The value can contain anything including spaces, so we capture to end of line
    RETURN_PATTERN = re.compile(
        r"^\s*ret\s+([a-zA-Z][a-zA-Z0-9_-]*):([a-zA-Z]+)=(.+?)\s*$", re.MULTILINE
    )

    @classmethod
    def find_all_returns(
        cls, script: str, location: SourceLocation
    ) -> list[ReturnDeclaration]:
        """Find all return declarations in a bash script.

        Args:
            script: Bash script content
            location: Source location for error reporting

        Returns:
            List of parsed return declarations

        Raises:
            ValueError: If return format is invalid
        """
        returns = []
        for match in cls.RETURN_PATTERN.finditer(script):
            name = match.group(1)
            type_str = match.group(2)
            value_expression = match.group(3)

            try:
                return_type = ReturnType.from_string(type_str)
            except ValueError as e:
                raise ValueError(f"{location}: {e}")

            ret_decl = ReturnDeclaration(
                name=name,
                return_type=return_type,
                value_expression=value_expression,
                location=location,
            )
            returns.append(ret_decl)

        return returns
