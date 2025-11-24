from pathlib import Path

from mudyla.parser.markdown_parser import MarkdownParser


def test_markdown_parser_handles_conditional_sections_and_offsets(tmp_path: Path):
    content = """\
# action: conditional
Intro
## definition when `axis-a: foo`
```bash
ret value:string=foo
```
## definition when `axis-a: bar, platform: linux`
```bash
ret value:string=bar
```
"""
    file_path = tmp_path / "defs.md"
    file_path.write_text(content)

    parser = MarkdownParser()
    doc = parser.parse_files([file_path])

    action = doc.get_action("conditional")
    assert action.is_multi_version
    assert len(action.versions) == 2

    first_version, second_version = action.versions
    assert first_version.location.line_number == 4  # first code block start
    assert second_version.location.line_number == 8  # second code block start

    assert len(first_version.conditions) == 1
    assert len(second_version.conditions) == 2
