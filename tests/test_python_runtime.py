"""Python actions bootstrap their owned runtime in an isolated interpreter."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from mudyla.ast.models import ActionVersion, SourceLocation
from mudyla.executor.language_runtime import ExecutionContext
from mudyla.executor.runtime_python import PythonRuntime


@pytest.mark.parametrize("installed", [False, True], ids=["editable", "installed-with-spaces"])
@pytest.mark.parametrize("exit_code", [0, 7])
def test_python_action_owns_runtime_context_and_preserves_exit(tmp_path, monkeypatch, installed, exit_code):
    unrelated = tmp_path / "parent-only import"
    unrelated.mkdir()
    (unrelated / "unrelated_parent_module.py").write_text("value = 1", encoding="utf-8")
    monkeypatch.syspath_prepend(str(unrelated))
    if installed:
        import mudyla.executor.runtime_python as module

        package = tmp_path / "installed package" / "mudyla"
        package.mkdir(parents=True)
        source = Path(__file__).resolve().parents[1] / "mudyla"
        for name in ("__init__.py", "runtime.py"):
            shutil.copyfile(source / name, package / name)
        monkeypatch.setattr(module.resources, "files", lambda name: package)
    working = tmp_path / "action with spaces"
    working.mkdir()
    output = working / "output.json"
    context = ExecutionContext({"project-root": "/project", "nix": False}, {"platform": "linux"},
                               {"VISIBLE": "value"}, {"VISIBLE": "value"}, {"count": 3},
                               {"enabled": True}, {"source": {"answer": 42}})
    body = f'''import os
import importlib.util
from mudyla.runtime import mdl
assert importlib.util.find_spec("unrelated_parent_module") is None
assert mdl.sys["project-root"] == "/project"
assert mdl.axis_value("platform") == "linux"
assert mdl.env["VISIBLE"] == os.environ["VISIBLE"] == "value"
assert mdl.args["count"] == 3 and mdl.flags["enabled"]
assert mdl.actions["source"]["answer"] == 42
mdl.ret("answer", 42, "int")
mdl.ret("enabled", True, "bool")
mdl.retain("action.source")
print("PYTHON_ACTION_EXECUTED")
raise SystemExit({exit_code})
'''
    version = ActionVersion(body, [], [], [], [], [], [], SourceLocation("fixture", 1, "source"), language="python")
    rendered = PythonRuntime().prepare_script(version, context, output, working)
    script = working / "script.py"
    script.write_text(rendered.content, encoding="utf-8")
    environment = {**os.environ, **rendered.environment, "PYTHONNOUSERSITE": "1"}
    environment.pop("PYTHONPATH", None)
    signal = working / "retain.signal"
    environment["MDL_RETAIN_SIGNAL_FILE"] = str(signal)
    result = subprocess.run([sys.executable, "-I", "-S", str(script)], cwd=working, env=environment,
                            capture_output=True, text=True, timeout=3)
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert result.stdout.strip() == "PYTHON_ACTION_EXECUTED"
    assert signal.read_text() == "source\n"
    assert json.loads(output.read_text()) == {"answer": {"type": "int", "value": 42},
                                             "enabled": {"type": "bool", "value": True}}
