"""Parser for dependency declarations in bash scripts."""

import re

from ..ast.models import DependencyDeclaration, SourceLocation


class DependencyParser:
    """Parser for dep, weak, and soft pseudo-commands in bash and python scripts."""

    # Pattern to match bash: dep action.action-name
    ACTION_DEP_PATTERN = re.compile(r"^\s*dep\s+action\.([a-zA-Z][a-zA-Z0-9_-]*)\s*$")

    # Pattern to match bash: weak action.action-name
    ACTION_WEAK_PATTERN = re.compile(r"^\s*weak\s+action\.([a-zA-Z][a-zA-Z0-9_-]*)\s*$")

    # Pattern to match bash: soft action.action-name retain.action.retainer-name
    ACTION_SOFT_PATTERN = re.compile(
        r"^\s*soft\s+action\.([a-zA-Z][a-zA-Z0-9_-]*)\s+retain\.action\.([a-zA-Z][a-zA-Z0-9_-]*)\s*$"
    )

    # Pattern to match bash: dep env.VARIABLE_NAME
    ENV_DEP_PATTERN = re.compile(r"^\s*dep\s+env\.([A-Z_][A-Z0-9_]*)\s*$")

    # Pattern to match bash: use args.argument-name
    ARGS_USE_PATTERN = re.compile(r"^\s*use\s+args\.([a-zA-Z][a-zA-Z0-9_-]*)\s*$")

    # Pattern to match python: mdl.dep("action.action-name")
    ACTION_DEP_PYTHON_PATTERN = re.compile(r'^\s*mdl\.dep\s*\(\s*["\']action\.([a-zA-Z][a-zA-Z0-9_-]*)["\']')

    # Pattern to match python: mdl.weak("action.action-name")
    ACTION_WEAK_PYTHON_PATTERN = re.compile(r'^\s*mdl\.weak\s*\(\s*["\']action\.([a-zA-Z][a-zA-Z0-9_-]*)["\']')

    # Pattern to match python: mdl.soft("action.action-name", "action.retainer-name")
    ACTION_SOFT_PYTHON_PATTERN = re.compile(
        r'^\s*mdl\.soft\s*\(\s*["\']action\.([a-zA-Z][a-zA-Z0-9_-]*)["\']\s*,\s*["\']action\.([a-zA-Z][a-zA-Z0-9_-]*)["\']'
    )

    # Pattern to match python: mdl.dep("env.VARIABLE_NAME")
    ENV_DEP_PYTHON_PATTERN = re.compile(r'^\s*mdl\.dep\s*\(\s*["\']env\.([A-Z_][A-Z0-9_]*)["\']')

    # Pattern to match python: mdl.use("args.argument-name")
    ARGS_USE_PYTHON_PATTERN = re.compile(r'^\s*mdl\.use\s*\(\s*["\']args\.([a-zA-Z][a-zA-Z0-9_-]*)["\']')

    @classmethod
    def find_all_dependencies(
        cls, script: str, base_location: SourceLocation
    ) -> tuple[list[DependencyDeclaration], list[str], list[str]]:
        """Find all dependency declarations in a bash script.

        Args:
            script: Bash script content
            base_location: Base source location for the script

        Returns:
            Tuple of (action_dependencies, env_var_dependencies, args_dependencies)

        Raises:
            ValueError: If dependency format is invalid
        """
        action_dependencies = []
        env_dependencies = []
        args_dependencies = []
        lines = script.split("\n")

        for i, line in enumerate(lines):
            # Skip comments
            stripped = line.strip()
            if stripped.startswith("#"):
                continue

            # Try bash action dependency: dep action.name
            action_match = cls.ACTION_DEP_PATTERN.match(line)
            if action_match:
                action_name = action_match.group(1)
                location = SourceLocation(
                    file_path=base_location.file_path,
                    line_number=base_location.line_number + i,
                    section_name=base_location.section_name,
                )
                action_dependencies.append(
                    DependencyDeclaration(action_name=action_name, location=location, weak=False)
                )
                continue

            # Try bash weak action dependency: weak action.name
            weak_match = cls.ACTION_WEAK_PATTERN.match(line)
            if weak_match:
                action_name = weak_match.group(1)
                location = SourceLocation(
                    file_path=base_location.file_path,
                    line_number=base_location.line_number + i,
                    section_name=base_location.section_name,
                )
                action_dependencies.append(
                    DependencyDeclaration(action_name=action_name, location=location, weak=True)
                )
                continue

            # Try bash soft action dependency: soft action.name retain.action.retainer
            soft_match = cls.ACTION_SOFT_PATTERN.match(line)
            if soft_match:
                action_name = soft_match.group(1)
                retainer_action = soft_match.group(2)
                location = SourceLocation(
                    file_path=base_location.file_path,
                    line_number=base_location.line_number + i,
                    section_name=base_location.section_name,
                )
                action_dependencies.append(
                    DependencyDeclaration(
                        action_name=action_name,
                        location=location,
                        soft=True,
                        retainer_action=retainer_action,
                    )
                )
                continue

            # Try python action dependency: mdl.dep("action.name")
            action_python_match = cls.ACTION_DEP_PYTHON_PATTERN.match(line)
            if action_python_match:
                action_name = action_python_match.group(1)
                location = SourceLocation(
                    file_path=base_location.file_path,
                    line_number=base_location.line_number + i,
                    section_name=base_location.section_name,
                )
                action_dependencies.append(
                    DependencyDeclaration(action_name=action_name, location=location, weak=False)
                )
                continue

            # Try python weak action dependency: mdl.weak("action.name")
            weak_python_match = cls.ACTION_WEAK_PYTHON_PATTERN.match(line)
            if weak_python_match:
                action_name = weak_python_match.group(1)
                location = SourceLocation(
                    file_path=base_location.file_path,
                    line_number=base_location.line_number + i,
                    section_name=base_location.section_name,
                )
                action_dependencies.append(
                    DependencyDeclaration(action_name=action_name, location=location, weak=True)
                )
                continue

            # Try python soft action dependency: mdl.soft("action.name", "action.retainer")
            soft_python_match = cls.ACTION_SOFT_PYTHON_PATTERN.match(line)
            if soft_python_match:
                action_name = soft_python_match.group(1)
                retainer_action = soft_python_match.group(2)
                location = SourceLocation(
                    file_path=base_location.file_path,
                    line_number=base_location.line_number + i,
                    section_name=base_location.section_name,
                )
                action_dependencies.append(
                    DependencyDeclaration(
                        action_name=action_name,
                        location=location,
                        soft=True,
                        retainer_action=retainer_action,
                    )
                )
                continue

            # Try bash environment variable dependency: dep env.VAR
            env_match = cls.ENV_DEP_PATTERN.match(line)
            if env_match:
                env_var = env_match.group(1)
                env_dependencies.append(env_var)
                continue

            # Try python environment variable dependency: mdl.dep("env.VAR")
            env_python_match = cls.ENV_DEP_PYTHON_PATTERN.match(line)
            if env_python_match:
                env_var = env_python_match.group(1)
                env_dependencies.append(env_var)
                continue

            # Try bash args declaration: use args.name
            args_match = cls.ARGS_USE_PATTERN.match(line)
            if args_match:
                arg_name = args_match.group(1)
                args_dependencies.append(arg_name)
                continue

            # Try python args declaration: mdl.use("args.name")
            args_python_match = cls.ARGS_USE_PYTHON_PATTERN.match(line)
            if args_python_match:
                arg_name = args_python_match.group(1)
                args_dependencies.append(arg_name)

        return action_dependencies, env_dependencies, args_dependencies
