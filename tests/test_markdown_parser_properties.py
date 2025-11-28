from pathlib import Path

import pytest

from mudyla.parser.markdown_parser import MarkdownParser


def test_sequential_property_sets_default(tmp_path: Path):
    content = """\
# properties
- `sequential`

# action: noop
```bash
echo "ok"
```
"""
    file_path = tmp_path / "defs.md"
    file_path.write_text(content)

    document = MarkdownParser().parse_files([file_path])

    assert document.properties.sequential_execution_default is True


def test_unknown_property_is_rejected(tmp_path: Path):
    content = """\
# properties
- `unknown`
"""
    file_path = tmp_path / "defs.md"
    file_path.write_text(content)

    parser = MarkdownParser()

    with pytest.raises(ValueError):
        parser.parse_files([file_path])


def test_properties_merge_across_files(tmp_path: Path):
    content1 = """\
# properties
- `sequential`
"""
    content2 = """\
# action: noop
```bash
echo ok
```
"""
    file1 = tmp_path / "defs1.md"
    file2 = tmp_path / "defs2.md"
    file1.write_text(content1)
    file2.write_text(content2)

    document = MarkdownParser().parse_files([file2, file1])

    assert document.properties.sequential_execution_default is True
