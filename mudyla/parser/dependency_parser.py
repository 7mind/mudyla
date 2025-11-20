"""Parser for dependency declarations in bash scripts."""

import re

from ..ast.models import DependencyDeclaration, SourceLocation


class DependencyParser:
    """Parser for dep pseudo-command in bash scripts."""

    # Pattern to match: dep action-name
    DEP_PATTERN = re.compile(r"^\s*dep\s+([a-zA-Z][a-zA-Z0-9_-]*)\s*$")

    @classmethod
    def find_all_dependencies(
        cls, script: str, base_location: SourceLocation
    ) -> list[DependencyDeclaration]:
        """Find all dependency declarations in a bash script.

        Args:
            script: Bash script content
            base_location: Base source location for the script

        Returns:
            List of dependency declarations

        Raises:
            ValueError: If dependency format is invalid
        """
        dependencies = []
        lines = script.split("\n")

        for i, line in enumerate(lines):
            # Skip comments
            stripped = line.strip()
            if stripped.startswith("#"):
                continue

            match = cls.DEP_PATTERN.match(line)
            if match:
                action_name = match.group(1)
                location = SourceLocation(
                    file_path=base_location.file_path,
                    line_number=base_location.line_number + i,
                    section_name=base_location.section_name,
                )
                dependencies.append(
                    DependencyDeclaration(action_name=action_name, location=location)
                )

        return dependencies
