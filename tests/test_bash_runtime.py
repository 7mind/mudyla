"""Bash runtime exit status and typed output, including macOS system Bash."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from mudyla.executor.runtime_bash import BashRuntime


@pytest.mark.parametrize("body,exit_code,expected", [
    (":", 0, {}),
    ("exit 7", 7, {}),
    ('ret "answer:int=42"\nret "enabled:bool=true"\nret "message:string=hello world"', 0,
     {"answer": {"type": "int", "value": 42}, "enabled": {"type": "bool", "value": True},
      "message": {"type": "string", "value": "hello world"}}),
])
def test_bash_runtime_writes_outputs_without_changing_exit_status(tmp_path, body, exit_code, expected):
    output = tmp_path / "output.json"
    script = tmp_path / "script.sh"
    script.write_text('MDL_OUTPUT_JSON="$1"\nsource "$2"\n' + body + "\n", encoding="utf-8", newline="\n")
    command = BashRuntime().get_execution_command(script)
    if sys.platform == "darwin":
        command[0] = "/bin/bash"
    runtime = Path(__file__).resolve().parents[1] / "mudyla" / "runtime.sh"
    result = subprocess.run([*command, str(output), str(runtime)], capture_output=True, text=True, timeout=5)
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert json.loads(output.read_text()) == expected
