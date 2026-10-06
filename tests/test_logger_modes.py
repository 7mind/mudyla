"""Logger selection and append-only output through the real CLI."""

import os
import json
from io import StringIO
from pathlib import Path
import re
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from mudyla.cli import CLI


@pytest.mark.parametrize("options,expected", [
    ([], "pure"), (["--logger", "pure"], "pure"),
    (["--logger", "raw"], "simple"), (["--logger", "table"], "table"),
    (["--it"], "pure"), (["--interactive"], "pure"), (["--force-interactive"], "pure"),
    (["--logger", "pure", "--it"], "pure"),
    (["--logger", "table", "--it"], "table"), (["--logger", "raw", "--it"], "simple"),
    (["--logger", "table", "--force-interactive"], "table"),
    (["--logger", "raw", "--force-interactive"], "simple"),
    (["--verbose", "--it"], "verbose"), (["--github-actions", "--interactive"], "github"),
    (["--simple-log"], "simple"), (["--verbose"], "verbose"), (["--github-actions"], "github"),
])
def test_logger_selection(options, expected):
    cli = CLI()
    args = cli.parser.parse_args(options)
    with patch("sys.stdout.isatty", return_value=True), patch("sys.stdin.isatty", return_value=True):
        cli._apply_platform_defaults(args, True)
    assert args.logger == expected


@pytest.mark.parametrize("options", [
    ["--logger", "pure", "--verbose"], ["--logger", "table", "--verbose"],
    ["--logger", "pure", "--github-actions"], ["--logger", "table", "--simple-log"],
])
def test_contradictory_logger_options_fail_before_execution(options):
    cli = CLI()
    with pytest.raises(SystemExit) as error:
        args = cli.parser.parse_args(options)
        cli._apply_platform_defaults(args, True)
    assert error.value.code == 2


def run_project(tmp_path: Path, options: list[str], script: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / ".git").mkdir(exist_ok=True)
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True, exist_ok=True)
    (definitions / "actions.md").write_text(f"# action: hello\n\n```python\n{script}\n```\n", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    return subprocess.run([sys.executable, "-m", "mudyla", "--without-nix", "--no-color", *options, ":hello"],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10)


def test_default_pure_streams_literal_output_without_terminal_controls(tmp_path):
    result = run_project(tmp_path, [], 'print("payload [red]literal[/red]\\x1b[2J")\nmdl.ret("ok", True, "bool")')
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RUN" in result.stdout
    assert "DONE" in result.stdout
    assert "payload [red]literal[/red]" in result.stdout
    assert "\x1b" not in result.stdout


def test_pure_preparation_sections_are_separated_from_notices(tmp_path):
    result = run_project(tmp_path, ["--dry-run"], 'mdl.ret("ok", True, "bool")')
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    for label in ["Contexts:", "Plan:"]:
        assert lines[lines.index(label) - 1] == "", result.stdout


def test_pure_preparation_has_exactly_one_blank_line_between_sections(tmp_path):
    result = run_project(tmp_path, ["--dry-run"], 'mdl.ret("ok", True, "bool")')
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.rstrip("\n").splitlines()
    assert not any(not first.strip() and not second.strip() for first, second in zip(lines, lines[1:])), result.stdout
    tree = lines.index("Plan:")
    assert lines[tree - 1] == "" and lines[tree - 2].strip(), result.stdout


@pytest.mark.parametrize("options,script,expected", [([], 'mdl.ret("ok", True, "bool")', 0),
                                                     ([], 'raise SystemExit(7)', 1),
                                                     (["--dry-run"], 'mdl.ret("ok", True, "bool")', 0),
                                                     (["--continue", "--keep-run-dir"], 'mdl.ret("ok", True, "bool")', 0)])
def test_pure_transcript_prints_one_final_tree_or_one_dry_run_plan(tmp_path, options, script, expected):
    if "--continue" in options:
        previous = run_project(tmp_path, ["--keep-run-dir"], script)
        assert previous.returncode == 0
    result = run_project(tmp_path, options, script)
    assert result.returncode == expected, result.stdout + result.stderr
    assert result.stdout.count("Plan:") == 1, result.stdout
    tree = result.stdout.index("Plan:")
    assert "Run info:" in result.stdout[:tree] and "Contexts:" in result.stdout[:tree] and "Goals:" in result.stdout[:tree]
    if "--dry-run" not in options:
        assert tree < result.stdout.index("Actions:\n") < result.stdout.index("Result:\n")
        assert "○ hello" not in result.stdout


def test_pure_default_axes_share_one_field_and_preserve_explicit_axes(tmp_path, monkeypatch, capsys):
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (definitions / "actions.md").write_text('# Axis\n\n- `mode`=`{fast*|slow}`\n- `target`=`{local*|remote}`\n\n'
                                            '# action: work\n\n## definition when `target:remote`\n\n```python\nprint("ok")\n```\n')
    monkeypatch.chdir(tmp_path)
    assert CLI().run(["--without-nix", "--no-color", "--dry-run", "--axis", "target:remote", ":work"]) == 0
    rendered = capsys.readouterr().out
    text, contexts = rendered.split("Contexts:", 1)
    assert text.count("Using default axes:") == 1, text
    assert "Using default axis value" not in text
    assert "mode:fast, target:local, platform:" in text
    assert "target:remote" in contexts and "target:local" not in contexts


def test_default_and_context_axes_use_the_same_foreground_colors(tmp_path, monkeypatch):
    from rich.color import Color
    from rich.console import Console
    from rich.text import Text
    from mudyla.logging.formatters import OutputFormatter

    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (definitions / "actions.md").write_text('# Axis\n\n- `version`=`{2.13*|3}`\n\n'
                                            '# action: work\n\n## definition when `version:2.13`\n\n```python\nprint("ok")\n```\n')
    monkeypatch.chdir(tmp_path)
    output = OutputFormatter(no_color=False, compact=True)
    stream = StringIO()
    output._console = Console(file=stream, width=160, force_terminal=True, color_system="truecolor", no_color=False)
    monkeypatch.setattr(CLI, "_build_formatters", lambda *args: output)
    assert CLI().run(["--without-nix", "--dry-run", ":work"]) == 0
    rendered = Text.from_ansi(stream.getvalue())
    occurrences = [match.start() for match in re.finditer("version:2.13", rendered.plain)]
    assert len(occurrences) == 2, rendered.plain
    for offset in [0, len("version:")]:
        styles = [rendered.get_style_at_offset(output.console, start + offset) for start in occurrences]
        colors = [style.color for style in styles]
        expected = Color.parse("green" if offset else "blue")
        assert all(color is not None and color.number == expected.number for color in colors), colors
        assert all(not style.dim and not style.bold for style in styles), styles
        assert colors[0] == colors[1], colors


@pytest.mark.parametrize("color_system,no_color,width,keep_run_dir", [
    ("standard", False, 40, False), ("256", False, 80, False), ("truecolor", False, 120, True),
    ("standard", True, 80, False), (None, False, 80, False),
])
def test_pure_run_information_emphasizes_quantities_without_restyling_identifiers(
    tmp_path, monkeypatch, color_system, no_color, width, keep_run_dir,
):
    from rich.color import Color
    from rich.console import Console
    from rich.text import Text
    from mudyla.executor.engine import ExecutionEngine
    from mudyla.logging.formatters import OutputFormatter

    project = tmp_path / "project2026"
    (project / ".git").mkdir(parents=True)
    definitions = project / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    for name in ["work", "unused"]:
        (definitions / f"{name}.md").write_text(f'# action: {name}\n\n```python\nprint("DONE")\n```\n')
    monkeypatch.chdir(project)
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    stream = StringIO()
    output = OutputFormatter(no_color=no_color, compact=True)
    console_options = dict(width=width, force_terminal=color_system is not None,
                           color_system=color_system, no_color=no_color, highlight=False)
    output._console = Console(file=stream, **console_options)
    monkeypatch.setattr(CLI, "_build_formatters", lambda *args: output)
    execute = ExecutionEngine.execute_all
    snapshots = []

    def capture_run_info(engine):
        snapshot = StringIO()
        Console(file=snapshot, **console_options).print(engine.run_info, highlight=False)
        snapshots.append(snapshot.getvalue())
        return execute(engine)

    monkeypatch.setattr(ExecutionEngine, "execute_all", capture_run_info)
    with patch("mudyla.cli.time.perf_counter", side_effect=[1.0, 1.012345]):
        assert CLI().run(["--without-nix", *(["--keep-run-dir"] if keep_run_dir else []), ":work"]) == 0
    assert len(snapshots) == 1 and stream.getvalue().startswith(snapshots[0])
    rendered = Text.from_ansi(stream.getvalue())
    quantities = [r"Definitions:\s+(2)", r"with\s+(2)\s+actions",
                  r"graph:\s+(1)\s+required", r"planning\s+took\s+(12ms)",
                  r"wall\s+time:\s+(\d+(?:\.\d+)?\s*(?:ms|s))"]
    labels = [r"(Using Nix:)", r"(Definitions:)", r"(Run ID:)", r"(Logs:)"]
    values = [r"Using Nix:\s+(No)", r"Execution mode:\s+(parallel)", r"Run ID:\s+(\d+)",
              r"(p\s*r\s*o\s*j\s*e\s*c\s*t\s*2\s*0\s*2\s*6)",
              r"(disabled)\s+with", r"(definition)\s+file", r"(required)\s+action", r"Logs:\s+(\S)"]
    for patterns, dim, cyan in [(quantities, False, True), (labels, True, False), (values, False, False)]:
        for pattern in patterns:
            match = re.search(pattern, rendered.plain)
            assert match, (pattern, rendered.plain)
            style = rendered.get_style_at_offset(output.console, match.start(1))
            assert not style.bold, (pattern, style)
            if color_system is not None:
                assert bool(style.dim) == dim, (pattern, style)
                if cyan and not no_color:
                    assert style.color is not None
                    assert style.color.number == Color.parse("cyan").number, (pattern, style)
            if color_system is None or no_color:
                assert style.color is None, (pattern, style)
            assert style.bgcolor is None
    if color_system is None:
        assert "\x1b" not in stream.getvalue()
    if no_color:
        assert all(style is None or style.color is None for _, style, _ in output.console.render(rendered))


@pytest.mark.parametrize("options,exit_code", [([], 0), (["--dry-run"], 0), (["--list-actions"], 0),
                                              ([":missing"], 1), (["--defs", "missing.md"], 1)])
def test_pure_run_information_is_grouped_and_flushed_on_every_preparation_exit(tmp_path, options, exit_code):
    result = run_project(tmp_path, options, 'mdl.ret("ok", True, "bool")')
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert result.stdout.startswith("Run info:\n"), result.stdout
    assert result.stdout.count("Run info:") == 1
    assert "Using Nix:" in result.stdout and "Project root:" in result.stdout
    if exit_code:
        assert "Error" in result.stdout or "error" in result.stdout
    if not options:
        assert result.stdout.count("Result:") == 1
        result_text = result.stdout.split("Result:")[1]
        assert "Outcome:" in result_text and "Total wall time:" in result_text and "Logs:" in result_text
    else:
        assert "Result:" not in result.stdout


def test_pure_parse_error_flushes_available_preparation_once(tmp_path):
    result = run_project(tmp_path, ["--axis", "invalid-axis-format"], 'mdl.ret("ok", True, "bool")')
    assert result.returncode == 1
    assert result.stdout.startswith("Run info:\n") and result.stdout.count("Run info:") == 1
    assert "Using Nix:" in result.stdout and "Error:" in result.stdout
    assert "Run ID:" not in result.stdout and "Result:" not in result.stdout


@pytest.mark.parametrize("stage", ["retainer", "validation"])
def test_pure_preparation_failure_flushes_existing_sections_once(tmp_path, monkeypatch, capsys, stage):
    from mudyla.dag.validator import DAGValidator
    from mudyla.executor.retainer_executor import RetainerExecutor

    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (definitions / "actions.md").write_text('# action: work\n\n```python\nprint("UNREACHABLE_ACTION")\n```\n')
    monkeypatch.chdir(tmp_path)

    def fail(*args, **kwargs):
        raise ValueError("PREPARATION_FAILURE")

    if stage == "retainer":
        monkeypatch.setattr(RetainerExecutor, "execute_retainers", fail)
    else:
        monkeypatch.setattr(DAGValidator, "validate_all", fail)
    assert CLI().run(["--without-nix", "--no-color", ":work"]) == 1
    text = capsys.readouterr().out
    assert text.startswith("Run info:\n")
    assert all(text.count(label) == 1 for label in ["Run info:", "Contexts:", "Goals:", "PREPARATION_FAILURE"])
    assert "UNREACHABLE_ACTION" not in text and "Result:" not in text


def test_raw_alias_uses_simple_progress(tmp_path):
    result = run_project(tmp_path, ["--logger", "raw"], 'mdl.ret("ok", True, "bool")')
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Running command" in result.stdout
    assert "Finished" in result.stdout
    assert "\x1b" not in result.stdout


@pytest.mark.parametrize("mode", ["pure", "table", "raw"])
def test_output_presentation_preserves_declared_types_and_saved_json(tmp_path, mode):
    script = ('from pathlib import Path\nfile = Path.cwd() / "value.txt"\nfile.write_text("payload")\n'
              'mdl.ret("file", str(file), "file")\nmdl.ret("directory", str(file.parent), "directory")\n'
              'mdl.ret("text", str(file), "string")\nmdl.ret("empty", "", "string")\n'
              'mdl.ret("zero", 0, "int")\nmdl.ret("disabled", False, "bool")')
    saved = tmp_path / "saved.json"
    options = ["--logger", mode, "--out", str(saved)] + (["--force-interactive"] if mode == "table" else [])
    result = run_project(tmp_path, options, script)
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(saved.read_text())
    values = data["hello"]
    assert values["file"] == values["text"]
    assert values["zero"] == 0 and values["disabled"] is False and values["empty"] == ""
    assert "success" not in values and isinstance(values["directory"], str)
    assert not list((tmp_path / ".mdl" / "runs").iterdir())
    if mode in {"pure", "raw"}:
        assert "hello @global" in result.stdout
        assert "success:" not in result.stdout
        for name, declared in [("file", "file"), ("directory", "directory"), ("text", "string"),
                               ("empty", "string"), ("zero", "int"), ("disabled", "bool")]:
            assert re.search(rf"  {name}:\s+{declared}\s+=", result.stdout), result.stdout
    else:
        assert '"hello"' in result.stdout and '"disabled": false' in result.stdout


def test_restored_outputs_retain_declared_types_without_rewriting_artifacts(tmp_path):
    script = 'mdl.ret("answer", 0, "int")\nmdl.ret("enabled", False, "bool")'
    first = run_project(tmp_path, ["--keep-run-dir"], script)
    assert first.returncode == 0
    artifacts = {path: path.read_bytes() for path in (tmp_path / ".mdl" / "runs").rglob("*.json")}
    restored = run_project(tmp_path, ["--continue"], script)
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert "RESTORED" in restored.stdout
    assert re.search(r"answer:\s+int\s+= 0", restored.stdout)
    assert re.search(r"enabled:\s+bool\s+= false", restored.stdout)
    assert all(path.read_bytes() == content for path, content in artifacts.items())


def test_pure_failure_keeps_status_and_literal_stderr(tmp_path):
    result = run_project(tmp_path, [], 'import sys\nprint("failure-detail\\x1b[2J", file=sys.stderr)\nsys.exit(7)')
    assert result.returncode == 1
    assert "FAIL" in result.stdout
    assert "failure-detail" in result.stdout
    assert "\x1b" not in result.stdout


def test_pure_honors_failure_output_suppression(tmp_path):
    result = run_project(tmp_path, ["--no-out-on-fail"], 'import sys\nprint("HIDDEN_OUT")\nprint("HIDDEN_ERR", file=sys.stderr)\nsys.exit(7)')
    assert result.returncode == 1
    assert "HIDDEN_OUT" not in result.stdout
    assert "HIDDEN_ERR" not in result.stdout
    assert "Output suppressed" in result.stdout


def test_parallel_failure_reports_finished_sibling(tmp_path):
    script = ('import time,sys\ntime.sleep(.05)\nsys.exit(7)\n```\n'
              '# action: later\n\n```python\nimport time\ntime.sleep(.2)\n'
              'print("LATER_SUCCEEDED")\nmdl.ret("ok", True, "bool")')
    result = run_project(tmp_path, ["--par", ":later"], script)
    assert result.returncode == 1
    assert "LATER_SUCCEEDED" in result.stdout
    assert re.search(r"DONE\s+\S*later", result.stdout)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY")
def test_pure_displays_flushed_prompt_before_reading_input(tmp_path):
    pexpect = pytest.importorskip("pexpect")
    run_project(tmp_path, [], 'mdl.ret("ok", True, "bool")')
    (tmp_path / ".mdl" / "defs" / "actions.md").write_text(
        '# action: hello\n\n```python\nprint("TYPE_VALUE: ", end="", flush=True)\n'
        'answer = input()\nprint("ANSWER=" + answer)\n```\n', encoding="utf-8")
    env = os.environ.copy()
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]), TERM="xterm-256color")
    child = pexpect.spawn(sys.executable, ["-m", "mudyla", "--without-nix", ":hello"],
                          cwd=str(tmp_path), env=env, encoding="utf-8", timeout=2)
    try:
        child.expect("TYPE_VALUE:")
        child.send("ihello\n")
        child.expect("ANSWER=hello")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.sendcontrol("c")
        child.close(force=True)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY")
@pytest.mark.parametrize("mode", ["pure", "table"])
@pytest.mark.parametrize("environment,explicit,forced", [
    (None, False, False), ("", False, False), ("1", False, False),
    ("0", False, False), ("1", False, True), (None, True, True),
])
def test_terminal_color_policy_honors_environment_and_explicit_flag(tmp_path, mode, environment, explicit, forced):
    pexpect = pytest.importorskip("pexpect")
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (definitions / "actions.md").write_text('# action: hello\n\n```python\nprint("PAYLOAD")\n```\n')
    env = os.environ.copy()
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]), TERM="xterm-256color")
    if environment is None:
        env.pop("NO_COLOR", None)
    else:
        env["NO_COLOR"] = environment
    options = ["--no-color"] if explicit else []
    if forced:
        options.append("--force-interactive")
    capture = StringIO()
    child = pexpect.spawn(sys.executable, ["-m", "mudyla", "--without-nix", "--logger", mode,
                                          "--it", *options, ":hello"],
                          cwd=str(tmp_path), env=env, encoding="utf-8", timeout=5)
    child.logfile_read = capture
    try:
        child.expect_exact("q close")
        child.send("q")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        child.close(force=True)
    text = capture.getvalue()
    color_codes = {*range(30, 39), *range(40, 49), *range(90, 98), *range(100, 108)}
    colored = [sequence for sequence in re.findall(r"\x1b\[([0-9;]*)m", text)
               if any(int(code) in color_codes for code in sequence.split(";") if code)]
    assert bool(colored) == (not explicit and not environment), colored[:5]
    assert text.count("\x1b[?1049h") == text.count("\x1b[?1049l") == 1


def test_no_color_preserves_raw_action_terminal_sequences(tmp_path):
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (definitions / "actions.md").write_text(
        '# action: hello\n\n```python\nprint("\\x1b[31mPAYLOAD\\x1b[0m")\n```\n')
    env = os.environ.copy()
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]), NO_COLOR="1")
    result = subprocess.run([sys.executable, "-m", "mudyla", "--without-nix", "--logger", "raw",
                             "--verbose", ":hello"], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "\x1b[31mPAYLOAD\x1b[0m" in result.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY")
def test_simple_no_color_terminal_output_contains_no_color_or_cursor_controls(tmp_path):
    pexpect = pytest.importorskip("pexpect")
    run_project(tmp_path, [], 'mdl.ret("ok", True, "bool")')
    env = os.environ.copy()
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]), TERM="xterm-256color")
    child = pexpect.spawn(sys.executable, ["-m", "mudyla", "--without-nix", "--logger", "raw", "--no-color", ":hello"],
                          cwd=str(tmp_path), env=env, encoding="utf-8", timeout=3)
    try:
        child.expect(pexpect.EOF)
        assert not re.search(r"\x1b\[[0-9;?]*[A-HJKSTfhln]", child.before)
        assert not re.search(r"\x1b\[(?:[0-9;]*;)?(?:3[0-8]|4[0-8]|9[0-7]|10[0-7])m", child.before)
        child.close()
        assert child.exitstatus == 0
    finally:
        child.close(force=True)


def test_cancellation_during_process_registration_stops_new_child(tmp_path, monkeypatch):
    import mudyla.cli as cli_module
    import mudyla.executor.engine as engine_module

    run_project(tmp_path, [], 'mdl.ret("ok", True, "bool")')
    marker = tmp_path / "survived-cancellation"
    (tmp_path / ".mdl" / "defs" / "actions.md").write_text(
        '# action: hello\n\n```python\nfrom pathlib import Path\nimport time\n'
        'time.sleep(.2)\nPath("survived-cancellation").touch()\n```\n', encoding="utf-8")

    class CancelBeforeRegistration(engine_module.ExecutionEngine):
        def _execute_subprocess(self, prepared, logger):
            original = engine_module.subprocess.Popen

            def create(*args, **kwargs):
                process = original(*args, **kwargs)
                self._request_kill()
                return process

            with patch.object(engine_module.subprocess, "Popen", create):
                return super()._execute_subprocess(prepared, logger)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_module, "ExecutionEngine", CancelBeforeRegistration)
    assert CLI().run(["--without-nix", "--no-color", ":hello"]) == 1
    assert not marker.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX SIGINT and process groups")
def test_sigint_before_process_registration_stops_spawned_tree(tmp_path):
    import signal

    run_project(tmp_path, [], 'print("ready")')
    descendant = 'import time; from pathlib import Path; time.sleep(.3); Path("survived").touch()'
    (tmp_path / ".mdl/defs/actions.md").write_text(
        '# action: slow\n\n```python\nimport subprocess, sys, time\nfrom pathlib import Path\n'
        f'subprocess.Popen([sys.executable, "-c", {descendant!r}])\n'
        'Path("started").touch()\ntime.sleep(30)\n```\n')
    wrapper = tmp_path / "interrupt_registration.py"
    wrapper.write_text('''import os, signal, threading, time
from pathlib import Path
import mudyla.cli as cli
import mudyla.executor.engine as module
class InterruptRegistration:
    def __init__(self):
        self.lock = threading.Lock()
        self.armed = False
    def __enter__(self):
        if self.armed:
            self.armed = False
            os.kill(os.getpid(), signal.SIGINT)
        return self.lock.__enter__()
    def __exit__(self, *args):
        return self.lock.__exit__(*args)
class Engine(module.ExecutionEngine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._processes_lock = InterruptRegistration()
    def _execute_subprocess(self, prepared, logger):
        original = module.subprocess.Popen
        def create(*args, **kwargs):
            process = original(*args, **kwargs)
            Path("spawned.pid").write_text(str(process.pid))
            deadline = time.monotonic() + 2
            while not Path("started").exists() and time.monotonic() < deadline:
                time.sleep(.005)
            assert Path("started").exists()
            self._processes_lock.armed = True
            return process
        module.subprocess.Popen = create
        try:
            return super()._execute_subprocess(prepared, logger)
        finally:
            module.subprocess.Popen = original
cli.ExecutionEngine = Engine
raise SystemExit(cli.main())
''')
    try:
        result = subprocess.run([sys.executable, str(wrapper), "--without-nix", "--seq", ":slow"],
                                cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                                capture_output=True, text=True, timeout=3)
        assert result.returncode == 130, result.stdout + result.stderr
        time.sleep(.4)
        assert not (tmp_path / "survived").exists()
    finally:
        if (tmp_path / "spawned.pid").exists():
            try:
                os.killpg(int((tmp_path / "spawned.pid").read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_direct_windows_python_uses_installed_interpreter(tmp_path, monkeypatch):
    from mudyla.executor.runtime_python import PythonRuntime

    script = tmp_path / "hello.py"
    script.write_text('print("PYTHON_ACTION_OK")', encoding="utf-8")
    runtime = PythonRuntime()
    with monkeypatch.context() as platform_patch:
        platform_patch.setattr(sys, "platform", "win32")
        command = runtime.get_direct_execution_command(script)
        assert command[0] == sys.executable
        assert runtime.get_execution_command(script)[0] == "python3"
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=3)
    assert result.returncode == 0
    assert result.stdout.strip() == "PYTHON_ACTION_OK"


@pytest.mark.parametrize("chunks,expected", [
    (["before\x1b", "[31mRED\x1b", "[0m after\n"], "beforeRED after"),
    (["before\x1b]", "52;c;SECRET", "\x1b", "\\after\n"], "beforeafter"),
    (["before\x1b]52;", "c;SECRET\x07after\n"], "beforeafter"),
    (["before\x1bP", "SECRET\x1b", "\\after\n"], "beforeafter"),
], ids=["split-csi", "split-osc-terminator", "osc-bell", "split-dcs"])
def test_pure_decodes_control_sequences_across_output_chunks(chunks, expected):
    from mudyla.dag.context import ContextId
    from mudyla.dag.graph import ActionId, ActionKey
    from mudyla.logging.action_logger_pure import ActionLoggerPure
    from mudyla.logging.formatters import OutputFormatter

    key = ActionKey(ActionId("build"), ContextId(()))
    output = OutputFormatter(no_color=True)
    stream = StringIO()
    output.console.file = stream
    logger = ActionLoggerPure([key], output, True)
    for chunk in chunks:
        logger.write_output(key, chunk, "stdout")
    assert stream.getvalue() == f"    build @global / stdout  {expected}\n"


def test_pure_stdout_style_does_not_depend_on_transport_chunks():
    from rich.console import Console
    from rich.text import Text
    from mudyla.dag.context import ContextId
    from mudyla.dag.graph import ActionId, ActionKey
    from mudyla.logging.action_logger_pure import ActionLoggerPure
    from mudyla.logging.formatters import OutputFormatter

    key = ActionKey(ActionId("build"), ContextId(()))
    styles = []
    for chunks in [["payload\n"], ["pay", "load\n"]]:
        output = OutputFormatter(no_color=False)
        stream = StringIO()
        output._console = Console(file=stream, force_terminal=True)
        logger = ActionLoggerPure([key], output, True)
        logger._interactive = False
        for chunk in chunks:
            logger.write_output(key, chunk, "stdout")
        rendered = Text.from_ansi(stream.getvalue())
        styles.append([rendered.get_style_at_offset(output.console, offset) for offset in range(len(rendered))])
    assert styles[0] == styles[1]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group cleanup")
@pytest.mark.parametrize("options,started", [([], "  RUN"), (["--logger", "raw", "--verbose"], "Running command")])
@pytest.mark.parametrize("mode", ["--seq", "--par"])
def test_closed_output_pipe_stops_running_action(tmp_path, options, started, mode):
    import signal

    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (definitions / "actions.md").write_text(
        '# action: flood\n\n```python\nimport os,time\nfrom pathlib import Path\n'
        'Path("action.pid").write_text(str(os.getpid()))\ntime.sleep(.2)\nprint("x"*2000000,flush=True)\n```\n'
        '# action: sleeper\n\n```python\nimport os,time\nfrom pathlib import Path\n'
        'Path("sleeper.pid").write_text(str(os.getpid()))\ntime.sleep(30)\n```\n')
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    process = subprocess.Popen([sys.executable, "-u", "-m", "mudyla", "--without-nix", "--no-color", *options, mode, ":flood", ":sleeper"],
                               cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert process.stdout is not None and process.stderr is not None
    pid_paths = [tmp_path / "action.pid"]
    if mode == "--par":
        pid_paths.append(tmp_path / "sleeper.pid")
    try:
        while True:
            line = process.stdout.readline()
            assert line, "CLI exited before starting the action"
            if started in line and ("sleeper" if mode == "--par" else "flood") in line:
                break
        deadline = time.monotonic() + 3
        while not all(path.exists() for path in pid_paths) and time.monotonic() < deadline:
            time.sleep(.01)
        assert all(path.exists() for path in pid_paths)
        process.stdout.close()
        assert process.wait(timeout=3) != 0
        for pid_path in pid_paths:
            with pytest.raises(ProcessLookupError):
                os.kill(int(pid_path.read_text()), 0)
    finally:
        for pid_path in pid_paths:
            if pid_path.exists():
                try:
                    os.killpg(int(pid_path.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
        process.stderr.close()
