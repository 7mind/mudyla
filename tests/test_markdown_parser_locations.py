from pathlib import Path

from mudyla.parser.markdown_parser import MarkdownParser


def test_markdown_parser_preserves_section_line_numbers(tmp_path: Path):
    content = """\
# action: sample
Intro line
```bash
ret value:string=ok
```

# action: second
## definition when `build-mode: release`
```bash
ret mode:string=release
```
"""

    file_path = tmp_path / "defs.md"
    file_path.write_text(content)

    parser = MarkdownParser()
    document = parser.parse_files([file_path])

    sample = document.get_action("sample")
    assert sample.location.line_number == 1
    assert sample.versions[0].location.line_number == 3  # code block start
    assert sample.versions[0].return_declarations[0].location.line_number == 3

    second = document.get_action("second")
    # Heading appears at line 7 (1-based)
    assert second.location.line_number == 7
    # Definition starts at the code block line (line 9)
    assert second.versions[0].location.line_number == 9
