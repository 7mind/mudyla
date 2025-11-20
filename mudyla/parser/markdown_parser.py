"""Markdown parser for Mudyla action definitions."""

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
    Condition,
    DependencyDeclaration,
    FlagDefinition,
    ParsedDocument,
    PlatformCondition,
    SourceLocation,
)
from ..ast.types import ReturnType
from .dependency_parser import DependencyParser
from .expansion_parser import ExpansionParser
from .return_parser import ReturnParser
from .combinators import (
    parse_argument_definition,
    parse_flag_definition,
    parse_axis_definition,
    parse_passthrough_definition,
    parse_vars_definition,
)


@dataclass
class Section:
    """Represents a section in the markdown document."""

    level: int
    title: str
    content: str
    line_number: int
    subsections: list["Section"]


class MarkdownParser:
    """Parser for markdown files containing action definitions."""

    # Pattern to match action header: action: action-name
    ACTION_HEADER_PATTERN = re.compile(r"^action:\s*([a-zA-Z][a-zA-Z0-9_-]*)$")

    # Pattern to match condition: definition when `conditions...`
    # Conditions can be axis-based or platform-based, separated by commas
    # Examples: `build-mode: release` or `build-mode: release, sys.platform: windows`
    CONDITION_PATTERN = re.compile(r"^definition\s+when\s+`([^`]+)`$")

    # Pattern for argument definition: `args.name`: type="value"; description
    # Note: List items are extracted without the leading "- "
    ARG_PATTERN = re.compile(
        r"^\s*`args\.([a-zA-Z][a-zA-Z0-9_-]*)`:\s*([a-zA-Z]+)(?:=\"([^\"]*)\")?\s*;\s*(.*)$"
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

    def __init__(self):
        self.markdown_parser = mistune.create_markdown(renderer=None)

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
        all_passthrough: list[str] = []

        for file_path in file_paths:
            content = file_path.read_text()
            actions, arguments, flags, axis, passthrough = self._parse_file(
                file_path, content
            )

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

            # Merge other definitions (last one wins for arguments/flags/axis)
            all_arguments.update(arguments)
            all_flags.update(flags)
            all_axis.update(axis)
            all_passthrough.extend(passthrough)

        # Remove duplicate passthrough vars
        all_passthrough = list(set(all_passthrough))

        return ParsedDocument(
            actions=all_actions,
            arguments=all_arguments,
            flags=all_flags,
            axis=all_axis,
            passthrough_env_vars=all_passthrough,
        )

    def _parse_file(
        self, file_path: Path, content: str
    ) -> tuple[
        dict[str, ActionDefinition],
        dict[str, ArgumentDefinition],
        dict[str, FlagDefinition],
        dict[str, AxisDefinition],
        list[str],
    ]:
        """Parse a single markdown file.

        Returns:
            Tuple of (actions, arguments, flags, axis, passthrough)
        """
        sections = self._extract_sections(content)

        actions = {}
        arguments = {}
        flags = {}
        axis = {}
        passthrough = []

        for section in sections:
            title_lower = section.title.lower().strip()

            # Check for special sections
            if title_lower == "arguments":
                arguments = self._parse_arguments_section(section, file_path)
            elif title_lower == "flags":
                flags = self._parse_flags_section(section, file_path)
            elif title_lower == "axis":
                axis = self._parse_axis_section(section, file_path)
            elif title_lower == "passthrough":
                passthrough = self._parse_passthrough_section(section, file_path)
            else:
                # Check if it's an action
                action_match = self.ACTION_HEADER_PATTERN.match(section.title.strip())
                if action_match:
                    action_name = action_match.group(1)
                    action = self._parse_action(section, action_name, file_path)
                    actions[action_name] = action

        return actions, arguments, flags, axis, passthrough

    def _extract_sections(self, content: str) -> list[Section]:
        """Extract all top-level sections from markdown content."""
        tokens = self.markdown_parser(content)
        sections = []
        current_section = None
        current_content = []
        line_counter = 1

        for token in tokens:
            if token["type"] == "heading":
                level = token["attrs"]["level"]
                title = self._extract_text_from_token(token)

                # Only level 1 headings start new sections
                if level == 1:
                    # Save previous section
                    if current_section is not None:
                        current_section.content = "\n".join(current_content)
                        sections.append(current_section)
                        current_content = []

                    # Start new section
                    current_section = Section(
                        level=level,
                        title=title,
                        content="",
                        line_number=line_counter,
                        subsections=[],
                    )
                elif current_section is not None:
                    # Subsection - add to current section content
                    current_content.append(f"{'#' * level} {title}")

            elif current_section is not None:
                # Add to current section content
                if token["type"] == "paragraph":
                    text = self._extract_text_from_token(token)
                    current_content.append(text)
                elif token["type"] == "list":
                    list_text = self._extract_list_items(token)
                    current_content.extend(list_text)
                elif token["type"] == "block_code":
                    code = token["raw"]
                    lang = token.get("attrs", {}).get("info", "")
                    current_content.append(f"```{lang}\n{code}\n```")

            # Count lines
            if "raw" in token:
                line_counter += token["raw"].count("\n")

        # Save last section
        if current_section is not None:
            current_section.content = "\n".join(current_content)
            sections.append(current_section)

        return sections

    def _extract_text_from_token(self, token: dict) -> str:
        """Extract text content from a token."""
        if "children" in token:
            texts = []
            for child in token["children"]:
                if child["type"] == "text":
                    texts.append(child["raw"])
                elif child["type"] == "codespan":
                    texts.append(f"`{child['raw']}`")
                elif child["type"] == "block_text":
                    # Recursively extract from block_text
                    texts.append(self._extract_text_from_token(child))
            return "".join(texts)
        return token.get("raw", "")

    def _extract_list_items(self, list_token: dict) -> list[str]:
        """Extract list items as strings."""
        items = []
        for item in list_token.get("children", []):
            if item["type"] == "list_item":
                text = self._extract_text_from_token(item)
                items.append(text)
        return items

    def _parse_arguments_section(
        self, section: Section, file_path: Path
    ) -> dict[str, ArgumentDefinition]:
        """Parse arguments section using parser combinators."""
        arguments = {}
        for line in section.content.split("\n"):
            # Add leading "- " since markdown parser extracts list items without it
            if line.strip() and not line.strip().startswith("-"):
                line = "- " + line
            parsed = parse_argument_definition(line)
            if parsed:
                try:
                    arg_type = ReturnType.from_string(parsed["type"])
                except ValueError as e:
                    raise ValueError(
                        f"{file_path}:{section.line_number}: {e}"
                    )

                arg_def = ArgumentDefinition(
                    name=parsed["name"],
                    arg_type=arg_type,
                    default_value=parsed["default"],
                    description=parsed["description"],
                    location=SourceLocation(
                        file_path=str(file_path),
                        line_number=section.line_number,
                        section_name=section.title,
                    ),
                )
                arguments[parsed["name"]] = arg_def

        return arguments

    def _parse_flags_section(
        self, section: Section, file_path: Path
    ) -> dict[str, FlagDefinition]:
        """Parse flags section using parser combinators."""
        flags = {}
        for line in section.content.split("\n"):
            # Add leading "- " since markdown parser extracts list items without it
            if line.strip() and not line.strip().startswith("-"):
                line = "- " + line
            parsed = parse_flag_definition(line)
            if parsed:
                flag_def = FlagDefinition(
                    name=parsed["name"],
                    description=parsed["description"],
                    location=SourceLocation(
                        file_path=str(file_path),
                        line_number=section.line_number,
                        section_name=section.title,
                    ),
                )
                flags[parsed["name"]] = flag_def

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

    def _parse_action(
        self, section: Section, action_name: str, file_path: Path
    ) -> ActionDefinition:
        """Parse action section."""
        location = SourceLocation(
            file_path=str(file_path),
            line_number=section.line_number,
            section_name=section.title,
        )

        # Parse vars subsection if present
        required_env_vars = self._parse_vars_subsection(section, file_path)

        # Find all bash code blocks and their conditions
        versions = self._parse_action_versions(section, action_name, file_path)

        if len(versions) == 0:
            raise ValueError(
                f"{location}: Action '{action_name}' has no bash code block"
            )

        return ActionDefinition(
            name=action_name,
            versions=versions,
            required_env_vars=required_env_vars,
            location=location,
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

    def _parse_action_versions(
        self, section: Section, action_name: str, file_path: Path
    ) -> list[ActionVersion]:
        """Parse all versions of an action (including conditional versions)."""
        versions = []

        # Split content by subsection headers to find conditional definitions
        lines = section.content.split("\n")
        current_conditions: list[AxisCondition] = []
        current_content = []
        in_bash_block = False
        bash_content = []

        for i, line in enumerate(lines):
            # Check for conditional definition header
            if line.strip().startswith("##"):
                # Save previous version if exists
                if bash_content:
                    bash_script = "\n".join(bash_content)
                    version = self._create_action_version(
                        bash_script,
                        current_conditions,
                        action_name,
                        file_path,
                        section.line_number + i,
                    )
                    versions.append(version)
                    bash_content = []

                # Check if this is a conditional definition
                header_text = line.strip().lstrip("#").strip()
                cond_match = self.CONDITION_PATTERN.match(header_text)
                if cond_match:
                    conditions_str = cond_match.group(1)
                    current_conditions = self._parse_conditions(conditions_str)
                else:
                    current_conditions = []

                continue

            # Track bash code blocks
            if line.strip().startswith("```bash"):
                in_bash_block = True
                continue
            elif line.strip() == "```" and in_bash_block:
                in_bash_block = False
                continue

            if in_bash_block:
                bash_content.append(line)

        # Save last version
        if bash_content:
            bash_script = "\n".join(bash_content)
            version = self._create_action_version(
                bash_script,
                current_conditions,
                action_name,
                file_path,
                section.line_number,
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

            # Determine if this is a platform condition or axis condition
            if name == "sys.platform":
                # Validate platform value
                valid_platforms = ["windows", "linux", "macos"]
                if value not in valid_platforms:
                    raise ValueError(
                        f"Invalid platform value: '{value}'. "
                        f"Valid values: {', '.join(valid_platforms)}"
                    )
                conditions.append(PlatformCondition(platform_value=value))
            else:
                # Assume it's an axis condition
                conditions.append(AxisCondition(axis_name=name, axis_value=value))

        return conditions

    def _create_action_version(
        self,
        bash_script: str,
        conditions: list[Condition],
        action_name: str,
        file_path: Path,
        line_number: int,
    ) -> ActionVersion:
        """Create an action version from bash script and conditions."""
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
        dependency_declarations = DependencyParser.find_all_dependencies(
            bash_script, location
        )

        return ActionVersion(
            bash_script=bash_script,
            expansions=expansions,
            return_declarations=return_declarations,
            dependency_declarations=dependency_declarations,
            conditions=conditions,
            location=location,
        )
