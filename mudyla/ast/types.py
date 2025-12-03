"""Type definitions for Mudyla AST."""

from enum import Enum


class ReturnType(Enum):
    """Types for action return values."""

    INT = "int"
    STRING = "string"
    BOOL = "bool"
    FILE = "file"
    DIRECTORY = "directory"

    @classmethod
    def from_string(cls, type_str: str) -> "ReturnType":
        """Parse return type from string.

        Args:
            type_str: Type string (case-insensitive)

        Returns:
            ReturnType enum value

        Raises:
            ValueError: If type string is not valid
        """
        normalized = type_str.lower().strip()
        try:
            return cls(normalized)
        except ValueError:
            valid_types = ", ".join(t.value for t in cls)
            raise ValueError(
                f"Invalid return type '{type_str}'. Valid types: {valid_types}"
            )


class ExpansionType(Enum):
    """Types of expansions in bash scripts."""

    SYSTEM = "sys"
    ACTION = "action"
    ENV = "env"
    ARGS = "args"
    FLAGS = "flags"
    RETAINED = "retained"
