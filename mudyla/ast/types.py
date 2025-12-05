"""Type definitions for Mudyla AST."""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


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


@dataclass(frozen=True)
class ArgumentType:
    """Type for command-line arguments, supporting both scalar and array types.

    Examples:
        - string -> ArgumentType(element_type=ReturnType.STRING, is_array=False)
        - array[string] -> ArgumentType(element_type=ReturnType.STRING, is_array=True)
        - array[int] -> ArgumentType(element_type=ReturnType.INT, is_array=True)
    """

    element_type: ReturnType
    """The base type (for arrays, this is the element type)"""

    is_array: bool = False
    """Whether this is an array type"""

    # Pattern for array type syntax: array[type]
    _ARRAY_PATTERN = re.compile(r"^array\[(\w+)\]$", re.IGNORECASE)

    def __str__(self) -> str:
        if self.is_array:
            return f"array[{self.element_type.value}]"
        return self.element_type.value

    @classmethod
    def from_string(cls, type_str: str) -> "ArgumentType":
        """Parse argument type from string.

        Supports both scalar types (string, int, etc.) and array types (array[string]).

        Args:
            type_str: Type string (case-insensitive)

        Returns:
            ArgumentType instance

        Raises:
            ValueError: If type string is not valid
        """
        normalized = type_str.strip()

        # Check for array type
        array_match = cls._ARRAY_PATTERN.match(normalized)
        if array_match:
            element_type_str = array_match.group(1)
            try:
                element_type = ReturnType.from_string(element_type_str)
                return cls(element_type=element_type, is_array=True)
            except ValueError:
                valid_types = ", ".join(t.value for t in ReturnType)
                raise ValueError(
                    f"Invalid array element type '{element_type_str}'. "
                    f"Valid types: {valid_types}"
                )

        # Scalar type
        try:
            element_type = ReturnType.from_string(normalized)
            return cls(element_type=element_type, is_array=False)
        except ValueError:
            valid_types = ", ".join(t.value for t in ReturnType)
            raise ValueError(
                f"Invalid type '{type_str}'. Valid types: {valid_types}, "
                f"or array[type] for arrays"
            )


class ExpansionType(Enum):
    """Types of expansions in bash scripts."""

    SYSTEM = "sys"
    ACTION = "action"
    ENV = "env"
    ARGS = "args"
    FLAGS = "flags"
    RETAINED = "retained"
