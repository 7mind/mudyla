"""Parser for dependency declarations in bash scripts."""

import re

from ..ast.models import DependencyDeclaration, SourceLocation


class DependencyParser:
    """Parser for dep pseudo-command in bash scripts."""

    # Pattern to match: dep action.action-name
    ACTION_DEP_PATTERN = re.compile(r"^\s*dep\s+action\.([a-zA-Z][a-zA-Z0-9_-]*)\s*$")

    # Pattern to match: dep env.VARIABLE_NAME
    ENV_DEP_PATTERN = re.compile(r"^\s*dep\s+env\.([A-Z_][A-Z0-9_]*)\s*$")

    @classmethod
    def find_all_dependencies(
        cls, script: str, base_location: SourceLocation
    ) -> tuple[list[DependencyDeclaration], list[str]]:
        """Find all dependency declarations in a bash script.

        Args:
            script: Bash script content
            base_location: Base source location for the script

        Returns:
            Tuple of (action_dependencies, env_var_dependencies)

        Raises:
            ValueError: If dependency format is invalid
        """
        action_dependencies = []
        env_dependencies = []
        lines = script.split("\n")

        for i, line in enumerate(lines):
            # Skip comments
            stripped = line.strip()
            if stripped.startswith("#"):
                continue

            # Try action dependency
            action_match = cls.ACTION_DEP_PATTERN.match(line)
            if action_match:
                action_name = action_match.group(1)
                location = SourceLocation(
                    file_path=base_location.file_path,
                    line_number=base_location.line_number + i,
                    section_name=base_location.section_name,
                )
                action_dependencies.append(
                    DependencyDeclaration(action_name=action_name, location=location)
                )
                continue

            # Try environment variable dependency
            env_match = cls.ENV_DEP_PATTERN.match(line)
            if env_match:
                env_var = env_match.group(1)
                env_dependencies.append(env_var)

        return action_dependencies, env_dependencies
