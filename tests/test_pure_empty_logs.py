"""Empty latest-output fields remain explicit without replacing real output."""

from tests.logger_fixtures import prepared_logger

from io import StringIO
import os
from pathlib import Path
import subprocess
import sys

import pytest
from rich.console import Console
from rich.text import Text

from mudyla.logging.terminal_logger_pure import PureTerminalLogger
from mudyla.logging.terminal_logger_table import TaskStatus
from mudyla.logging.formatters import OutputFormatter
from tests.test_plan_dag import crossing_graph


@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("status", list(TaskStatus))
def test_empty_latest_output_follows_duration_in_existing_action_row(connected, status):
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=False, compact=True,
                             console=Console(file=StringIO(), width=180, color_system="truecolor", force_terminal=True))
    logger = prepared_logger(PureTerminalLogger, keys, output, True, graph=graph if connected else None)
    task = logger.tasks[keys[0]]
    task.status = status
    task.duration = 1.25
    lines, anchors = logger._action_lines()
    row = Text.assemble(*[(segment.text, segment.style or "") for segment in lines[anchors[keys[0]]]])
    assert logger._format_duration(task.duration) + ": <empty>" in row.plain
    assert row.get_style_at_offset(output.console, row.plain.index("<empty>")).dim


@pytest.mark.parametrize("connected", [False, True])
def test_real_latest_error_payload_is_preserved(connected):
    graph, keys = crossing_graph()
    output = OutputFormatter(no_color=False, compact=True,
                             console=Console(file=StringIO(), width=180, color_system="truecolor", force_terminal=True))
    logger = prepared_logger(PureTerminalLogger, keys, output, True, graph=graph if connected else None)
    logger.tasks[keys[0]].latest = "[failure] exact payload"
    logger.tasks[keys[0]].stream = "stderr"
    lines, anchors = logger._action_lines()
    row = Text.assemble(*[(segment.text, segment.style or "") for segment in lines[anchors[keys[0]]]])
    assert "[failure] exact payload" in row.plain and "<empty>" not in row.plain
    assert row.get_style_at_offset(output.console, row.plain.index("[failure]")).color.name == "red"


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("fragment", [False, True])
@pytest.mark.parametrize("exit_code", [0, 7])
def test_redirected_pure_final_row_preserves_actual_latest_output(tmp_path, stream, fragment, exit_code):
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    final = f"{stream.upper()}_FINAL_PREVIEW"
    payload = f"{stream.upper()}_EARLY_LINE\n{final}" + ("" if fragment else "\n")
    (definitions / "actions.md").write_text(f'''# action: package
```python
import sys
sys.{stream}.write({payload!r})
sys.{stream}.flush()
raise SystemExit({exit_code})
```
''', encoding="utf-8")
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                   "TERM": "dumb", "NO_COLOR": "1", "COLUMNS": "180", "LINES": "40"}
    result = subprocess.run([sys.executable, "-u", "-m", "mudyla", "--without-nix", "--par",
                             "--logger", "pure", ":package"], cwd=tmp_path, env=environment,
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
    assert result.returncode == (0 if exit_code == 0 else 1), result.stdout + result.stderr
    prefix, final_sections = result.stdout.split("Actions:\n", 1)
    assert f"/ {stream}" in prefix and all(line in prefix for line in payload.splitlines())
    assert "RUN" in prefix and ("DONE" if exit_code == 0 else "FAIL") in prefix
    row = next(line for line in final_sections.splitlines() if "package" in line)
    assert final in row and "<empty>" not in row, result.stdout
