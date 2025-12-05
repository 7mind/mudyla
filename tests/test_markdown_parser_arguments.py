from pathlib import Path

import pytest

from mudyla.ast.types import ReturnType
from mudyla.parser.markdown_parser import MarkdownParser


def test_argument_block_parses_new_syntax(tmp_path: Path):
    content = """\
# arguments
- `args.output-dir`: Output directory for test results
  - type: `directory`
  - default: `"test-output"`
- `args.required-path`: Required build artifact location
  - type: `file`

# action: noop
```bash
ret ok:string=ok
```
"""
    file_path = tmp_path / "defs.md"
    file_path.write_text(content)

    document = MarkdownParser().parse_files([file_path])

    output_dir = document.get_argument("output-dir")
    assert output_dir.arg_type.element_type == ReturnType.DIRECTORY
    assert not output_dir.arg_type.is_array
    assert output_dir.default_value == "test-output"
    assert output_dir.description == "Output directory for test results"
    assert output_dir.location.line_number == 2

    required = document.get_argument("required-path")
    assert required.arg_type.element_type == ReturnType.FILE
    assert not required.arg_type.is_array
    assert required.default_value is None
    assert required.is_mandatory


def test_argument_without_type_fails(tmp_path: Path):
    content = """\
# arguments
- `args.invalid`: Missing type
  - default: `"oops"`
"""
    file_path = tmp_path / "defs.md"
    file_path.write_text(content)

    parser = MarkdownParser()

    with pytest.raises(ValueError):
        parser.parse_files([file_path])
