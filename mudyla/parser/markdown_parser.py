"""Markdown parser for Mudyla action definitions."""

import platform
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import mistune

from ..ast.models import (
    ActionDefinition,
    ActionVersion,
    ArgumentDefinition,
    AxisCondition,
    AxisDefinition,
    AxisValue,
    DocumentProperties,
    Condition,
    DependencyDeclaration,
    FlagDefinition,
    ParsedDocument,
    PlatformCondition,
    SourceLocation,
)
from ..ast.types import ArgumentType, ReturnType
from .dependency_parser import DependencyParser
from .expansion_parser import ExpansionParser
from .return_parser import ReturnParser
from .combinators import (
    parse_flag_definition,
    parse_axis_definition,
    parse_environment_definition,
    parse_passthrough_definition,
    parse_vars_definition,
)


@dataclass
class Section:
    """Represents a section in the markdown document."""

    title: str
    content: str
    line_number: int


class MarkdownParser:
    """Parser for markdown files containing action definitions."""

    # Pattern to match action header: action: action-name
    ACTION_HEADER_PATTERN = re.compile(r"^action:\s*([a-zA-Z][a-zA-Z0-9_-]*)$")

    # Pattern to match condition: definition when `conditions...`
    # Conditions can be axis-based or platform-based, separated by commas
    # Examples: `build-mode: release` or `build-mode: release, platform: windows`
    CONDITION_PATTERN = re.compile(r"^definition\s+when\s+`([^`]+)`$")

    @staticmethod
    def _detect_platform() -> str:
        """Detect current platform and return normalized name.

        Returns:
            Platform name: linux, darwin, or windows

        Raises:
            RuntimeError: If platform is not supported
        """
        system = platform.system().lower()
        if system == "linux":
            return "linux"
        elif system == "darwin":
            return "darwin"
        elif system == "windows":
            return "windows"
        else:
            raise RuntimeError(
                f"Unsupported platform: {system}. "
                "Mudyla only supports linux, darwin (macOS), and windows."
            )

    @classmethod
    def _create_builtin_platform_axis(cls) -> AxisDefinition:
        """Create built-in platform axis with current platform as default.

        Returns:
            AxisDefinition for platform axis
        """
        current_platform = cls._detect_platform()

        # Define all supported platforms with current one as default
        platform_values = []
        for p in ["linux", "darwin", "windows"]:
            platform_values.append(
                AxisValue(value=p, is_default=(p == current_platform))
            )

        return AxisDefinition(
            name="platform",
            values=platform_values,
            location=SourceLocation(
                file_path="<built-in>",
                line_number=0,
                section_name="platform (built-in axis)",
            ),
        )

    # Pattern for argument definition (new multi-line syntax):
    # - `args.name`: Description
    #   - type: `directory`
    #   - default: `"value"`
    ARG_HEADER_PATTERN = re.compile(
        r"^\s*-\s*`args\.([a-zA-Z][a-zA-Z0-9_-]*)`\s*:\s*(.+)$"
    )
    ARG_TYPE_PATTERN = re.compile(
        r"^\s*-\s*type:\s*`?([a-zA-Z]+(?:\[[a-zA-Z]+\])?)`?\s*$"
    )
    ARG_DEFAULT_PATTERN = re.compile(
        r"^\s*-\s*default:\s*`?(.+?)`?\s*$"
    )
    ARG_ALIAS_PATTERN = re.compile(
        r"^\s*-\s*alias:\s*`?([a-zA-Z][a-zA-Z0-9_-]*)`?\s*$"
    )

    # Pattern for flag definition: `flags.name`: description
    FLAG_PATTERN = re.compile(
        r"^\s*`flags\.([a-zA-Z][a-zA-Z0-9_-]*)`:\s*(.*)$"
    )

    # Pattern for axis definition: `axis-name`=`{value1|value2*|value3}`
    AXIS_PATTERN = re.compile(
        r"^\s*`([a-zA-Z][a-zA-Z0-9_-]*)`\s*=\s*`\{([^}]+)\}`\s*$"
    )

    # Pattern for passthrough env var: `VARIABLE_NAME`
    PASSTHROUGH_PATTERN = re.compile(r"^\s*`([A-Z_][A-Z0-9_]*)`\s*$")

    # Pattern for vars definition: `VARIABLE_NAME`: description
    VARS_PATTERN = re.compile(r"^\s*`([A-Z_][A-Z0-9_]*)`:\s*(.*)$")

    def parse_files(self, file_paths: list[Path]) -> ParsedDocument:
        """Parse multiple markdown files into a single document.

        Args:
            file_paths: List of markdown file paths

        Returns:
            Parsed document

        Raises:
            ValueError: If parsing fails
        """
        all_actions: dict[str, ActionDefinition] = {}
        all_arguments: dict[str, ArgumentDefinition] = {}
        all_flags: dict[str, FlagDefinition] = {}
        all_axis: dict[str, AxisDefinition] = {}
        all_environment: dict[str, str] = {}
        all_passthrough: list[str] = []
        all_required_env: list[str] = []
        properties = DocumentProperties()

        for file_path in file_paths:
            content = file_path.read_text()
            (
                actions,
                arguments,
                flags,
                axis,
                environment_vars,
                passthrough,
                required_env,
                file_properties,
            ) = self._parse_file(file_path, content)
            properties = properties.merge(file_properties)

            # Check for duplicate actions
            for action_name, action in actions.items():
                if action_name in all_actions:
                    existing = all_actions[action_name]
                    raise ValueError(
                        f"Duplicate action '{action_name}' found:\n"
                        f"  First: {existing.location}\n"
                        f"  Second: {action.location}"
                    )
                all_actions[action_name] = action

            # Merge other definitions (last one wins for arguments/flags/axis/environment)
            all_arguments.update(arguments)
            all_flags.update(flags)
            all_axis.update(axis)
            all_environment.update(environment_vars)
            all_passthrough.extend(passthrough)
            all_required_env.extend(required_env)

        # Remove duplicate passthrough vars
        all_passthrough = list(set(all_passthrough))
        all_required_env = list(set(all_required_env))

        # Add built-in platform axis if not already defined by user
        if "platform" not in all_axis:
            all_axis["platform"] = self._create_builtin_platform_axis()

        return ParsedDocument(
            actions=all_actions,
            arguments=all_arguments,
            flags=all_flags,
            axis=all_axis,
            environment_vars=all_environment,
            passthrough_env_vars=all_passthrough,
            required_env_vars=all_required_env,
            properties=properties,
        )

    def _parse_file(
        self, file_path: Path, content: str
    ) -> tuple[
        dict[str, ActionDefinition],
        dict[str, ArgumentDefinition],
        dict[str, FlagDefinition],
        dict[str, AxisDefinition],
        dict[str, str],
        list[str],
        list[str],
        DocumentProperties,
    ]:
        """Parse a single markdown file.

        Returns:
            Tuple of (actions, arguments, flags, axis, environment_vars, passthrough, required_env, properties)
        """
        sections = self._extract_sections(content)

        actions = {}
        arguments = {}
        flags = {}
        axis = {}
        environment_vars = {}
        passthrough = []
        required_env = []
        properties = DocumentProperties()

        for section in sections:
            title_lower = section.title.lower().strip()

            # Check for special sections
            if title_lower == "arguments":
                arguments = self._parse_arguments_section(section, file_path)
            elif title_lower == "flags":
                flags = self._parse_flags_section(section, file_path)
            elif title_lower == "axis":
                axis = self._parse_axis_section(section, file_path)
            elif title_lower == "environment":
                environment_vars, passthrough_from_env, required_from_env = self._parse_environment_section(section, file_path)
                passthrough.extend(passthrough_from_env)
                required_env.extend(required_from_env)
                environment_vars.update(environment_vars)

            elif title_lower == "passthrough":
                # Legacy support for top-level passthrough section
                passthrough.extend(self._parse_passthrough_section(section, file_path))
            elif title_lower == "required-env":
                required_env.extend(self._parse_required_env_section(section, file_path))
            elif title_lower == "properties":
                properties = self._parse_properties_section(section, file_path)
            else:
                # Check if it's an action
                action_match = self.ACTION_HEADER_PATTERN.match(section.title.strip())
                if action_match:
                    action_name = action_match.group(1)
                    action = self._parse_action(section, action_name, file_path)
                    actions[action_name] = action

        return actions, arguments, flags, axis, environment_vars, passthrough, required_env, properties

    def _extract_sections(self, content: str) -> list[Section]:
        """Extract all top-level (# ...) sections with accurate source lines."""
        sections: list[Section] = []
        current_title: Optional[str] = None
        current_lines: list[str] = []
        current_start_line = 0
        in_code_block = False

        for idx, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("```"):
                in_code_block = not in_code_block

            if not in_code_block and line.startswith("# "):
                if current_title is not None:
                    sections.append(
                        Section(
                            title=current_title,
                            content="\n".join(current_lines).strip() + "\n",
                            line_number=current_start_line,
                        )
                    )
                current_title = line[2:].strip()
                current_lines = []
                current_start_line = idx
                continue

            if current_title is not None:
                current_lines.append(line)

        if current_title is not None:
            sections.append(
                Section(
                    title=current_title,
                    content="\n".join(current_lines).strip() + "\n",
                    line_number=current_start_line,
                )
            )

        return sections

    def _extract_code_blocks_from_content(
        self, content: str, base_offset: int
    ) -> list[tuple[str, str, int]]:
        """Extract code blocks with line offsets relative to the full file.

        Args:
            content: Section content (without the top-level heading line).
            base_offset: Line offset of this content relative to the H1 line (0-based).

        Returns:
            List of tuples: (language, code, absolute_offset_from_h1)
        """
        blocks: list[tuple[str, str, int]] = []
        lines = content.splitlines()
        in_block = False
        block_lang = ""
        block_lines: list[str] = []
        block_start_offset = 0

        for idx, line in enumerate(lines):
            stripped = line.strip()
            if not in_block and stripped.startswith("```"):
                in_block = True
                block_lang = stripped[3:].strip() or "bash"
                block_lines = []
                block_start_offset = idx
                continue

            if in_block and stripped.startswith("```"):
                code = "\n".join(block_lines)
                blocks.append(
                    (block_lang, code, base_offset + block_start_offset)
                )
                in_block = False
                block_lang = ""
                block_lines = []
                continue

            if in_block:
                block_lines.append(line)

        return blocks

    def _parse_arguments_section(
        self, section: Section, file_path: Path
    ) -> dict[str, ArgumentDefinition]:
        """Parse arguments section using the new structured syntax."""
        arguments: dict[str, ArgumentDefinition] = {}
        current_block: dict[str, str | int | None] | None = None

        def finalize_current() -> None:
            nonlocal current_block
            if current_block is None:
                return

            if current_block.get("type") is None:
                raise ValueError(
                    f"{file_path}:{current_block['line_number']}: "
                    f"Argument 'args.{current_block['name']}' is missing a type declaration"
                )

            try:
                arg_type = ArgumentType.from_string(str(current_block["type"]))
            except ValueError as exc:
                type_line = current_block.get("type_line") or current_block["line_number"]
                raise ValueError(f"{file_path}:{type_line}: {exc}")

            name = str(current_block["name"])
            if name in arguments:
                existing = arguments[name]
                raise ValueError(
                    f"Duplicate argument 'args.{name}': first at {existing.location}, "
                    f"second at {file_path}:{current_block['line_number']}"
                )

            description = str(current_block.get("description") or "").strip()
            default_value = current_block.get("default")
            if isinstance(default_value, str):
                default_value = self._normalize_default_value(default_value)

            alias = current_block.get("alias")
            if isinstance(alias, str):
                alias = alias.strip()

            arguments[name] = ArgumentDefinition(
                name=name,
                arg_type=arg_type,
                default_value=default_value,  # Optional[str]
                description=description,
                location=SourceLocation(
                    file_path=str(file_path),
                    line_number=int(current_block["line_number"]),
                    section_name=section.title,
                ),
                alias=alias,
            )
            current_block = None

        for offset, raw_line in enumerate(section.content.splitlines(), start=section.line_number + 1):
            stripped = raw_line.strip()
            if not stripped:
                continue

            header_match = self.ARG_HEADER_PATTERN.match(stripped)
            if header_match:
                finalize_current()
                current_block = {
                    "name": header_match.group(1),
                    "description": header_match.group(2).strip(),
                    "type": None,
                    "type_line": None,
                    "default": None,
                    "alias": None,
                    "line_number": offset,
                }
                continue

            type_match = self.ARG_TYPE_PATTERN.match(stripped)
            default_match = self.ARG_DEFAULT_PATTERN.match(stripped)
            alias_match = self.ARG_ALIAS_PATTERN.match(stripped)

            if type_match:
                if current_block is None:
                    raise ValueError(
                        f"{file_path}:{offset}: Type declaration must follow an argument header"
                    )
                if current_block.get("type") is not None:
                    raise ValueError(
                        f"{file_path}:{offset}: Duplicate type for argument 'args.{current_block['name']}'"
                    )
                current_block["type"] = type_match.group(1)
                current_block["type_line"] = offset
                continue

            if default_match:
                if current_block is None:
                    raise ValueError(
                        f"{file_path}:{offset}: Default declaration must follow an argument header"
                    )
                if current_block.get("default") is not None:
                    raise ValueError(
                        f"{file_path}:{offset}: Duplicate default for argument 'args.{current_block['name']}'"
                    )
                current_block["default"] = default_match.group(1)
                continue

            if alias_match:
                if current_block is None:
                    raise ValueError(
                        f"{file_path}:{offset}: Alias declaration must follow an argument header"
                    )
                if current_block.get("alias") is not None:
                    raise ValueError(
                        f"{file_path}:{offset}: Duplicate alias for argument 'args.{current_block['name']}'"
                    )
                current_block["alias"] = alias_match.group(1)
                continue

            if stripped.startswith("-"):
                target = f"args.{current_block['name']}" if current_block else "arguments"
                raise ValueError(
                    f"{file_path}:{offset}: Unexpected arguments line '{stripped}' for {target}"
                )

        finalize_current()
        return arguments

    @staticmethod
    def _normalize_default_value(raw_value: str) -> str:
        """Strip Markdown/code fences and quotes from default values."""
        value = raw_value.strip()
        if value.startswith("`") and value.endswith("`") and len(value) >= 2:
            value = value[1:-1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        return value

    def _parse_flags_section(
        self, section: Section, file_path: Path
    ) -> dict[str, FlagDefinition]:
        """Parse flags section using parser combinators."""
        flags = {}
        for line_num, line in enumerate(section.content.split("\n")):
            stripped = line.strip()
            if not stripped:
                continue
            # Add leading "- " since markdown parser extracts list items without it
            if not stripped.startswith("-"):
                line = "- " + line
            parsed = parse_flag_definition(line)
            if parsed:
                flag_def = FlagDefinition(
                    name=parsed["name"],
                    description=parsed["description"],
                    location=SourceLocation(
                        file_path=str(file_path),
                        line_number=section.line_number + line_num,
                        section_name=section.title,
                    ),
                )
                flags[parsed["name"]] = flag_def
            else:
                # Fail fast: if line looks like a flag but doesn't parse, raise error
                raise ValueError(
                    f"Invalid flag definition '{stripped}' at {file_path}:{section.line_number + line_num}. "
                    f"Expected format: `flags.name`: description"
                )

        return flags

    def _parse_axis_section(
        self, section: Section, file_path: Path
    ) -> dict[str, AxisDefinition]:
        """Parse axis section using parser combinators."""
        axis = {}
        for line in section.content.split("\n"):
            # Add leading "- " since markdown parser extracts list items without it
            if line.strip() and not line.strip().startswith("-"):
                line = "- " + line
            parsed = parse_axis_definition(line)
            if parsed:
                values = [
                    AxisValue(value=v["value"], is_default=v["is_default"])
                    for v in parsed["values"]
                ]

                axis_def = AxisDefinition(
                    name=parsed["name"],
                    values=values,
                    location=SourceLocation(
                        file_path=str(file_path),
                        line_number=section.line_number,
                        section_name=section.title,
                    ),
                )

                # Validate default count
                default_count = sum(1 for v in values if v.is_default)
                if default_count > 1:
                    raise ValueError(
                        f"{axis_def.location}: Axis '{parsed['name']}' has {default_count} "
                        f"default values, but must have zero or exactly one"
                    )

                axis[parsed["name"]] = axis_def

        return axis

    def _parse_passthrough_section(
        self, section: Section, file_path: Path
    ) -> list[str]:
        """Parse passthrough section using parser combinators."""
        passthrough = []
        for line in section.content.split("\n"):
            # Add leading "- " since markdown parser extracts list items without it
            if line.strip() and not line.strip().startswith("-"):
                line = "- " + line
            var_name = parse_passthrough_definition(line)
            if var_name:
                passthrough.append(var_name)

        return passthrough

    def _parse_required_env_section(
        self, section: Section, file_path: Path
    ) -> list[str]:
        """Parse required-env section using parser combinators."""
        required = []
        for line in section.content.split("\n"):
            if line.strip() and not line.strip().startswith("-"):
                line = "- " + line
            var_name = parse_passthrough_definition(line)
            if var_name:
                required.append(var_name)
        return required

    def _parse_environment_section(
        self, section: Section, file_path: Path
    ) -> tuple[dict[str, str], list[str], list[str]]:
        """Parse environment section with environment vars and passthrough vars.

        Returns:
            Tuple of (environment_vars, passthrough_vars, required_env_vars)
        """
        environment_vars = {}
        passthrough_vars = []
        required_env_vars = []

        content_lines = section.content.split("\n")
        
        in_passthrough = False
        in_required_env = False

        for line in content_lines:
            stripped = line.strip()

            # Check for subsection headers
            if stripped.startswith("##"):
                header_lower = stripped.lower()
                if "passthrough" in header_lower:
                    in_passthrough = True
                    in_required_env = False
                    continue
                elif "required-env" in header_lower:
                    in_required_env = True
                    in_passthrough = False
                    continue

            if not stripped:
                continue

            # Add leading "- " if not present for parser
            if not stripped.startswith("-"):
                line_for_parser = "- " + stripped
            else:
                line_for_parser = stripped

            if in_passthrough:
                var_name = parse_passthrough_definition(line_for_parser)
                if var_name:
                    passthrough_vars.append(var_name)
            elif in_required_env:
                var_name = parse_passthrough_definition(line_for_parser)
                if var_name:
                    required_env_vars.append(var_name)
            else:
                # Try parsing as environment variable with value first
                env_def = parse_environment_definition(line_for_parser)
                if env_def:
                    environment_vars[env_def["var_name"]] = env_def["value"]
                # A line could be a passthrough/required var even if not in a specific subsection
                # but for now we keep the explicit subsection requirement.

        return environment_vars, passthrough_vars, required_env_vars
		
    def _parse_properties_section(
        self, section: Section, file_path: Path
    ) -> DocumentProperties:
        """Parse document-level properties."""
        sequential_default = False

        for offset, raw_line in enumerate(section.content.splitlines(), start=section.line_number + 1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            if not stripped.startswith("-"):
                raise ValueError(
                    f"{file_path}:{offset}: Invalid property declaration '{raw_line.strip()}'"
                )

            property_name = stripped[1:].strip()
            if property_name.startswith("`") and property_name.endswith("`") and len(property_name) >= 2:
                property_name = property_name[1:-1].strip()

            if property_name == "":
                raise ValueError(f"{file_path}:{offset}: Property name cannot be empty")

            normalized = property_name.lower()
            if normalized == "sequential":
                sequential_default = True
            else:
                raise ValueError(
                    f"{file_path}:{offset}: Unknown property '{property_name}'"
                )

        return DocumentProperties(sequential_execution_default=sequential_default)

    def _parse_action(
        self, section: Section, action_name: str, file_path: Path
    ) -> ActionDefinition:
        """Parse action section."""
        location = SourceLocation(
            file_path=str(file_path),
            line_number=section.line_number,
            section_name=section.title,
        )

        description = self._extract_action_description(section)

        # Parse vars subsection if present
        required_env_vars = self._parse_vars_subsection(section, file_path)

        # Find all bash code blocks and their conditions
        versions = self._parse_action_versions(section, action_name, file_path)

        if len(versions) == 0:
            raise ValueError(
                f"{location}: Action '{action_name}' has no code block (bash or python)"
            )

        # Collect environment variable dependencies from all versions
        # These come from dep env.VAR declarations
        for version in versions:
            for env_var in version.env_dependencies:
                if env_var not in required_env_vars:
                    required_env_vars[env_var] = f"Required by dep env.{env_var}"

        return ActionDefinition(
            name=action_name,
            versions=versions,
            required_env_vars=required_env_vars,
            location=location,
            description=description,
        )

    def _parse_vars_subsection(
        self, section: Section, file_path: Path
    ) -> dict[str, str]:
        """Parse vars subsection within an action using parser combinators."""
        vars_dict = {}

        # Search in the content for a vars section marker
        lines = section.content.split("\n")
        in_vars_section = False
        for line in lines:
            # Check if line is a header for vars
            if line.strip().lower() in ["## vars", "### vars", "#### vars"]:
                in_vars_section = True
                continue

            # Check if we hit another header
            if in_vars_section and line.strip().startswith("#"):
                in_vars_section = False
                continue

            if in_vars_section:
                # Add leading "- " since markdown parser extracts list items without it
                vars_line = line
                if line.strip() and not line.strip().startswith("-"):
                    vars_line = "- " + line
                parsed = parse_vars_definition(vars_line)
                if parsed:
                    vars_dict[parsed["var_name"]] = parsed["description"]

        return vars_dict

    def _extract_action_description(self, section: Section) -> str:
        """Extract description text before code blocks or subsections."""
        description_lines: list[str] = []
        for line in section.content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("```") or stripped.startswith("##"):
                break
            description_lines.append(line)

        return "\n".join(description_lines).strip()

    def _parse_action_versions(
        self, section: Section, action_name: str, file_path: Path
    ) -> list[ActionVersion]:
        """Parse all versions of an action (including conditional versions)."""
        versions = []

        # Check if there are any conditional definitions (## definition when ...)
        # Split by ## headers to handle conditional versions
        conditional_sections = []
        lines = section.content.split("\n")
        current_section_content = []
        current_conditions = []
        # Lines in section.content start immediately after the H1 line, so the
        # first content line is offset +1 from the section heading.
        current_line_offset = 1

        for i, line in enumerate(lines):
            if line.strip().startswith("##"):
                # Save previous section if it has content
                if current_section_content:
                    conditional_sections.append((
                        current_conditions,
                        "\n".join(current_section_content),
                        current_line_offset
                    ))

                # Check if this is a conditional definition
                header_text = line.strip().lstrip("#").strip()
                cond_match = self.CONDITION_PATTERN.match(header_text)
                if cond_match:
                    conditions_str = cond_match.group(1)
                    current_conditions = self._parse_conditions(conditions_str)
                else:
                    current_conditions = []

                current_section_content = []
                # Content after this header starts on the next line (+1), and
                # section.content starts right after the H1 (+1), so add 2.
                current_line_offset = i + 2
            else:
                current_section_content.append(line)

        # Save last section
        if current_section_content or not conditional_sections:
            conditional_sections.append((
                current_conditions,
                "\n".join(current_section_content),
                current_line_offset
            ))

        # Extract code blocks from each conditional section
        for conditions, content, line_offset in conditional_sections:
            code_blocks = self._extract_code_blocks_from_content(content, line_offset)

            # Create a version for each code block
            for language, code, block_offset in code_blocks:
                # Normalize language
                if language == "" or language == "sh":
                    language = "bash"

                # Skip unsupported languages
                if language not in ["bash", "python"]:
                    continue

                # Remove trailing newline if present
                code = code.rstrip('\n')

                version = self._create_action_version(
                    bash_script=code,
                    conditions=conditions,
                    action_name=action_name,
                    file_path=file_path,
                    line_number=section.line_number + block_offset,
                    language=language,
                )
                versions.append(version)

        return versions

    def _parse_conditions(self, conditions_str: str) -> list[Condition]:
        """Parse a comma-separated list of conditions.

        Args:
            conditions_str: String like "build-mode: release" or "build-mode: release, sys.platform: windows"

        Returns:
            List of Condition objects (AxisCondition or PlatformCondition)

        Raises:
            ValueError: If condition format is invalid
        """
        conditions = []

        # Split by comma to get individual conditions
        for cond_part in conditions_str.split(","):
            cond_part = cond_part.strip()

            # Parse "name: value" format
            if ":" not in cond_part:
                raise ValueError(f"Invalid condition format: '{cond_part}'. Expected 'name: value'")

            name, value = cond_part.split(":", 1)
            name = name.strip()
            value = value.strip()

            # Check if this is deprecated sys.platform syntax
            if name == "sys.platform":
                raise ValueError(
                    f"Deprecated syntax 'sys.platform: {value}'. "
                    f"Use 'platform: {value}' instead. The 'platform' axis is now built-in."
                )

            # All conditions are now axis conditions (including platform)
            conditions.append(AxisCondition(axis_name=name, axis_value=value))

        return conditions

    def _create_action_version(
        self,
        bash_script: str,
        conditions: list[Condition],
        action_name: str,
        file_path: Path,
        line_number: int,
        language: str = "bash",
    ) -> ActionVersion:
        """Create an action version from script and conditions."""
        location = SourceLocation(
            file_path=str(file_path),
            line_number=line_number,
            section_name=f"action: {action_name}",
        )

        # Parse expansions
        expansions = ExpansionParser.find_all_expansions(bash_script)

        # Parse return declarations
        return_declarations = ReturnParser.find_all_returns(bash_script, location)

        # Parse dependency declarations
        dependency_declarations, env_dependencies, args_dependencies = DependencyParser.find_all_dependencies(
            bash_script, location
        )

        return ActionVersion(
            bash_script=bash_script,
            expansions=expansions,
            return_declarations=return_declarations,
            dependency_declarations=dependency_declarations,
            env_dependencies=env_dependencies,
            args_dependencies=args_dependencies,
            conditions=conditions,
            location=location,
            language=language,
        )
