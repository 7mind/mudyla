"""AST model classes for Mudyla."""

from dataclasses import dataclass, field
from typing import Optional

from .expansions import Expansion
from .types import ReturnType


@dataclass(frozen=True)
class SourceLocation:
    """Source location in a markdown file."""

    file_path: str
    line_number: int
    section_name: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line_number} (in '{self.section_name}')"


@dataclass(frozen=True)
class ReturnDeclaration:
    """Return value declaration in an action."""

    name: str
    return_type: ReturnType
    value_expression: str
    location: SourceLocation

    def __str__(self) -> str:
        return f"{self.name}:{self.return_type.value}={self.value_expression}"


@dataclass(frozen=True)
class ArgumentDefinition:
    """Command-line argument definition."""

    name: str
    """Argument name (without 'args.' prefix)"""

    arg_type: ReturnType
    """Argument type"""

    default_value: Optional[str]
    """Default value, None if mandatory"""

    description: str
    """Argument description"""

    location: SourceLocation

    @property
    def is_mandatory(self) -> bool:
        """Check if argument is mandatory (no default value)."""
        return self.default_value is None

    @property
    def full_name(self) -> str:
        """Get full argument name with 'args.' prefix."""
        return f"args.{self.name}"


@dataclass(frozen=True)
class FlagDefinition:
    """Command-line flag definition."""

    name: str
    """Flag name (without 'flags.' prefix)"""

    description: str
    """Flag description"""

    location: SourceLocation

    @property
    def full_name(self) -> str:
        """Get full flag name with 'flags.' prefix."""
        return f"flags.{self.name}"


@dataclass(frozen=True)
class AxisValue:
    """Single value in an axis definition."""

    value: str
    is_default: bool


@dataclass(frozen=True)
class AxisDefinition:
    """Axis definition for multi-version actions."""

    name: str
    """Axis name"""

    values: list[AxisValue]
    """Possible values"""

    location: SourceLocation

    def get_default_value(self) -> Optional[str]:
        """Get the default value if one exists."""
        defaults = [v.value for v in self.values if v.is_default]
        if len(defaults) == 0:
            return None
        if len(defaults) > 1:
            raise ValueError(
                f"Axis '{self.name}' has multiple default values: {', '.join(defaults)}"
            )
        return defaults[0]

    def validate_value(self, value: str) -> None:
        """Validate that a value is valid for this axis.

        Args:
            value: Value to validate

        Raises:
            ValueError: If value is not valid
        """
        valid_values = [v.value for v in self.values]
        if value not in valid_values:
            raise ValueError(
                f"Invalid value '{value}' for axis '{self.name}'. "
                f"Valid values: {', '.join(valid_values)}"
            )


@dataclass(frozen=True)
class AxisCondition:
    """Condition for an action version based on axis value."""

    axis_name: str
    axis_value: str

    def matches(self, axis_values: dict[str, str]) -> bool:
        """Check if this condition matches the given axis values."""
        return axis_values.get(self.axis_name) == self.axis_value


@dataclass(frozen=True)
class ActionVersion:
    """Single version of an action (for multi-version actions)."""

    bash_script: str
    """The bash script content"""

    expansions: list[Expansion]
    """All expansions found in the script"""

    return_declarations: list[ReturnDeclaration]
    """Return value declarations"""

    conditions: list[AxisCondition]
    """Axis conditions that must be met for this version"""

    location: SourceLocation

    def matches_axis_values(self, axis_values: dict[str, str]) -> bool:
        """Check if all conditions match the given axis values."""
        return all(cond.matches(axis_values) for cond in self.conditions)


@dataclass
class ActionDefinition:
    """Complete action definition."""

    name: str
    """Action name"""

    versions: list[ActionVersion]
    """Action versions (one for simple actions, multiple for axis-based)"""

    required_env_vars: dict[str, str]
    """Required environment variables (name -> description)"""

    location: SourceLocation

    _dependency_cache: Optional[set[str]] = field(default=None, init=False, repr=False)
    """Cached set of action dependencies"""

    @property
    def is_multi_version(self) -> bool:
        """Check if this action has multiple versions."""
        return len(self.versions) > 1

    def get_version(self, axis_values: dict[str, str]) -> ActionVersion:
        """Get the appropriate version for the given axis values.

        Args:
            axis_values: Current axis values

        Returns:
            Matching action version

        Raises:
            ValueError: If no version matches or multiple versions match
        """
        if not self.is_multi_version:
            assert len(self.versions) == 1
            return self.versions[0]

        matching = [v for v in self.versions if v.matches_axis_values(axis_values)]

        if len(matching) == 0:
            raise ValueError(
                f"No version of action '{self.name}' matches axis values: {axis_values}"
            )
        if len(matching) > 1:
            raise ValueError(
                f"Multiple versions of action '{self.name}' match axis values: {axis_values}"
            )

        return matching[0]

    def get_required_axis(self) -> set[str]:
        """Get the set of axis names required by this action."""
        axis_names = set()
        for version in self.versions:
            for condition in version.conditions:
                axis_names.add(condition.axis_name)
        return axis_names

    def get_all_expansions(self) -> list[Expansion]:
        """Get all expansions from all versions."""
        expansions = []
        for version in self.versions:
            expansions.extend(version.expansions)
        return expansions

    def get_action_dependencies(self) -> set[str]:
        """Get all action dependencies (cached).

        Returns:
            Set of action names this action depends on
        """
        if self._dependency_cache is not None:
            return self._dependency_cache

        deps = set()
        for expansion in self.get_all_expansions():
            from .expansions import ActionExpansion

            if isinstance(expansion, ActionExpansion):
                deps.add(expansion.get_dependency_action())

        self._dependency_cache = deps
        return deps


@dataclass(frozen=True)
class ParsedDocument:
    """Complete parsed document representing all markdown files."""

    actions: dict[str, ActionDefinition]
    """All actions indexed by name"""

    arguments: dict[str, ArgumentDefinition]
    """All argument definitions indexed by name (without 'args.' prefix)"""

    flags: dict[str, FlagDefinition]
    """All flag definitions indexed by name (without 'flags.' prefix)"""

    axis: dict[str, AxisDefinition]
    """All axis definitions indexed by name"""

    passthrough_env_vars: list[str]
    """Environment variables to pass through to Nix"""

    def get_action(self, name: str) -> ActionDefinition:
        """Get action by name.

        Args:
            name: Action name

        Returns:
            Action definition

        Raises:
            KeyError: If action not found
        """
        if name not in self.actions:
            raise KeyError(f"Action '{name}' not found")
        return self.actions[name]

    def get_argument(self, name: str) -> ArgumentDefinition:
        """Get argument by name (without 'args.' prefix).

        Args:
            name: Argument name

        Returns:
            Argument definition

        Raises:
            KeyError: If argument not found
        """
        if name not in self.arguments:
            raise KeyError(f"Argument '{name}' not found")
        return self.arguments[name]

    def get_flag(self, name: str) -> FlagDefinition:
        """Get flag by name (without 'flags.' prefix).

        Args:
            name: Flag name

        Returns:
            Flag definition

        Raises:
            KeyError: If flag not found
        """
        if name not in self.flags:
            raise KeyError(f"Flag '{name}' not found")
        return self.flags[name]

    def get_axis(self, name: str) -> AxisDefinition:
        """Get axis by name.

        Args:
            name: Axis name

        Returns:
            Axis definition

        Raises:
            KeyError: If axis not found
        """
        if name not in self.axis:
            raise KeyError(f"Axis '{name}' not found")
        return self.axis[name]
