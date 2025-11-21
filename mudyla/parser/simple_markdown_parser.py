"""Simplified markdown parser using regex for section splitting."""

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MarkdownSection:
    """A section in markdown (everything under a level-1 heading)."""

    title: str
    content: str
    line_number: int


def parse_sections(markdown_content: str) -> list[MarkdownSection]:
    """
    Parse markdown into sections based on level-1 headings (# ...).

    This is much simpler than using mistune - we just split on `# ` at the
    beginning of lines and extract the content between them.

    Args:
        markdown_content: Raw markdown string

    Returns:
        List of MarkdownSection objects
    """
    sections = []
    lines = markdown_content.split('\n')

    current_section_title = None
    current_section_content = []
    current_section_line = 0
    in_code_block = False

    for line_num, line in enumerate(lines, start=1):
        # Track code block state
        if line.strip().startswith('```'):
            in_code_block = not in_code_block
            if current_section_title is not None:
                current_section_content.append(line)
            continue

        # Check if this is a level-1 heading (but not if we're in a code block)
        if not in_code_block and line.startswith('# ') and not line.startswith('## '):
            # Save previous section if exists
            if current_section_title is not None:
                sections.append(MarkdownSection(
                    title=current_section_title,
                    content='\n'.join(current_section_content),
                    line_number=current_section_line
                ))

            # Start new section
            current_section_title = line[2:].strip()  # Remove '# ' prefix
            current_section_content = []
            current_section_line = line_num
        elif current_section_title is not None:
            # Add line to current section content
            current_section_content.append(line)

    # Save last section
    if current_section_title is not None:
        sections.append(MarkdownSection(
            title=current_section_title,
            content='\n'.join(current_section_content),
            line_number=current_section_line
        ))

    return sections


def extract_code_blocks(content: str, language: str | None = None) -> list[tuple[str, str]]:
    """
    Extract code blocks from markdown content.

    Args:
        content: Markdown content
        language: If specified, only extract blocks with this language

    Returns:
        List of (language, code) tuples
    """
    code_blocks = []

    # Pattern to match fenced code blocks
    # Matches ```lang\ncode\n``` or ```\ncode\n```
    pattern = r'^```(\w*)\n(.*?)^```'

    for match in re.finditer(pattern, content, re.MULTILINE | re.DOTALL):
        block_lang = match.group(1) or 'bash'  # Default to bash if no language
        block_code = match.group(2)

        if language is None or block_lang == language:
            code_blocks.append((block_lang, block_code))

    return code_blocks
