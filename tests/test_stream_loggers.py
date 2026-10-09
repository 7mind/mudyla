"""Append-only mode selection, presentation and exact child delivery."""

import json
import os
from pathlib import Path
import subprocess
import sys
from io import StringIO

import pytest

from tests.terminal_capture import terminal_text

from mudyla.cli import CLI


@pytest.mark.parametrize("options,mode,verbose", [
    (["--logger", "simple"], "simple", False),
    (["--logger", "verbose"], "verbose", True),
    (["--logger", "github"], "github", False),
    (["--simple-log"], "simple", False),
    (["--logger", "raw"], "simple", False),
    (["--logger", "raw", "--verbose"], "verbose", True),
    (["--logger", "raw", "--github-actions"], "github", False),
    (["--simple-log", "--verbose"], "verbose", True),
    (["--verbose", "--github-actions"], "github", False),
    (["--github-actions", "--verbose"], "github", False),
    (["--verbose", "--logger", "github"], "github", False),
    (["--logger", "github", "--verbose"], "github", False),
    (["--logger", "verbose", "--verbose"], "verbose", True),
    (["--logger", "github", "--github-actions", "--verbose"], "github", False),
    (["--simple-log", "--github-actions", "--verbose"], "github", False),
])
def test_canonical_modes_and_legacy_precedence(options, mode, verbose):
    cli = CLI()
    args = cli.parser.parse_args(options)
    cli._apply_platform_defaults(args, True)
    assert args.logger == mode
    assert args.verbose == verbose


def run_project(path: Path, options: list[str], script: str, *, columns: int = 300) -> subprocess.CompletedProcess[str]:
    (path / ".git").mkdir(exist_ok=True)
    definitions = path / ".mdl" / "defs"
    definitions.mkdir(parents=True, exist_ok=True)
    (definitions / "actions.md").write_text(
        '# action: seed\n\n```python\nmdl.ret("seed", 1, "int")\n```\n\n'
        '# action: work\n\n```python\nmdl.dep("action.seed")\n' + script + '\n```\n', encoding="utf-8")
    env = os.environ.copy()
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]), NO_COLOR="1", COLUMNS=str(columns))
    return subprocess.run([sys.executable, "-m", "mudyla", "--without-nix", *options, ":work"],
                          cwd=path, env=env, capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize("options", [["--simple-log"], ["--verbose"], ["--logger", "raw"]])
@pytest.mark.parametrize("dry", [False, True])
def test_append_only_modes_share_compact_sections_and_one_initial_plan(tmp_path, options, dry):
    result = run_project(tmp_path, options + (["--dry-run"] if dry else ["--out", "result.json"]),
                         'mdl.ret("count", 0, "int")\nmdl.ret("enabled", False, "bool")')
    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.count("Plan:") == 1, result.stdout
    assert "Execution plan:" not in result.stdout
    assert result.stdout.index("Run info:") < result.stdout.index("Contexts:") < result.stdout.index("Goals:") < result.stdout.index("Plan:")
    assert "\x1b" not in result.stdout
    if not dry:
        if options == ["--logger", "raw"]:
            assert "Running command" in result.stdout and "Finished" in result.stdout
        assert result.stdout.index("Plan:") < result.stdout.index("Result:") < result.stdout.index("Outputs:")
        assert "count:" in result.stdout and "enabled:" in result.stdout
        data = json.loads((tmp_path / "result.json").read_text())
        assert "count" in str(data) and "False" in str(data)


@pytest.mark.parametrize("options,suppressed", [(["--simple-log"], False),
    (["--simple-log", "--no-out-on-fail"], True), (["--verbose"], False),
    (["--verbose", "--no-out-on-fail"], False)])
def test_failure_payload_is_present_once_without_changing_capture_files(tmp_path, options, suppressed):
    result = run_project(tmp_path, options,
        'import sys\nsys.stdout.write("\\x1b[35mOUT_TOKEN\\x1b[0m"); sys.stdout.flush()\n'
        'print("ERR_TOKEN", file=sys.stderr, flush=True)\nraise SystemExit(7)')
    assert result.returncode == 1
    payload = result.stdout + result.stderr
    assert payload.count("OUT_TOKEN") == (0 if suppressed else 1), payload
    assert payload.count("ERR_TOKEN") == (0 if suppressed else 1), payload
    if not suppressed:
        assert "\x1b[35mOUT_TOKEN\x1b[0m" in payload
    stdout = next(path for path in (tmp_path / ".mdl" / "runs").rglob("stdout.log") if "OUT_TOKEN" in path.read_text())
    assert stdout.read_text().count("ERR_TOKEN") == 1
    assert stdout.with_name("stderr.log").read_text().count("ERR_TOKEN") == 1
    assert str(stdout) in payload and str(stdout.with_name("stderr.log")) in payload


def test_github_group_end_starts_on_its_own_line_without_changing_fragments(tmp_path):
    result = run_project(tmp_path, ["--logger", "github", "--keep-run-dir"],
                         'import sys\nsys.stdout.write("FINAL_FRAGMENT"); sys.stdout.flush()')
    assert result.returncode == 0, result.stderr
    assert "FINAL_FRAGMENT\nwork@global: Finished (" in result.stdout, result.stdout
    assert result.stdout.count("::group::") == result.stdout.count("::endgroup::") == 2
    captures = list((tmp_path / ".mdl" / "runs").rglob("stdout.log"))
    assert any(path.read_text() == "FINAL_FRAGMENT" for path in captures)


def test_lifecycle_markers_wrap_complete_long_names():
    from rich.console import Console
    from mudyla.dag.context import ContextId
    from mudyla.dag.graph import ActionId, ActionKey
    from mudyla.logging.action_logger_simple import ActionLoggerSimple
    from mudyla.logging.formatters import OutputFormatter

    keys = [ActionKey(ActionId(name), ContextId(axis_values=())) for name in ["x" * 60 + "TAIL", "short"]]
    capture = StringIO()
    output = OutputFormatter(no_color=True, compact=True, console=Console(file=capture, width=30, color_system=None))
    logger = ActionLoggerSimple(keys, output)
    for key in keys:
        logger.mark_done(key, 1.0)
    lines = capture.getvalue().splitlines()
    assert "x" * 60 + "TAIL@global:" in "".join(lines), lines
    assert "TAIL" in capture.getvalue()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY")
@pytest.mark.parametrize("mode", ["verbose", "github"])
def test_streaming_modes_flush_partial_prompts_with_suppression_and_inherited_input(tmp_path, mode):
    pexpect = pytest.importorskip("pexpect")
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (definitions / "actions.md").write_text('# action: ask\n\n```python\nimport sys\n'
        'sys.stdout.write("\\x1b[35mPROMPT>\\x1b[0m"); sys.stdout.flush()\n'
        'answer = input()\nprint("ANSWER=" + answer, flush=True)\n'
        'print("ERR_FRAGMENT", file=sys.stderr, end="", flush=True)\n```\n')
    env = os.environ.copy()
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]), NO_COLOR="1", TERM="xterm-256color")
    capture = StringIO()
    child = pexpect.spawn(sys.executable, ["-m", "mudyla", "--without-nix", "--logger", mode,
                          "--no-out-on-fail", "--keep-run-dir", ":ask"], cwd=str(tmp_path), env=env,
                          encoding="utf-8", timeout=5)
    child.logfile_read = capture
    try:
        child.expect_exact("\x1b[35mPROMPT>\x1b[0m")
        child.sendline("héllo界")
        child.expect_exact("ANSWER=héllo界")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        child.close(force=True)
    rendered = capture.getvalue()
    for token in ["ANSWER=héllo界", "ERR_FRAGMENT"]:
        assert rendered.count(token) == 1
        assert rendered.index(token) < rendered.index("ask@global: Finished (")
    assert "\r\nask@global: Finished (" in rendered
    assert "\x1b[?1049" not in rendered and "\x1b[?1000" not in rendered
    stderr = next((tmp_path / ".mdl" / "runs").rglob("stderr.log"))
    assert stderr.read_text() == "ERR_FRAGMENT"
    combined = stderr.with_name("stdout.log").read_text()
    assert combined.count("ERR_FRAGMENT") == 1
    assert combined.replace("ERR_FRAGMENT", "") == "\x1b[35mPROMPT>\x1b[0mANSWER=héllo界\n"


@pytest.mark.parametrize("mode", ["verbose", "github"])
@pytest.mark.parametrize("stderr_first", [False, True])
def test_partial_stream_fragments_preserve_callback_order_and_marker_boundary(monkeypatch, mode, stderr_first):
    from rich.console import Console
    from mudyla.dag.graph import ActionKey
    from mudyla.logging.action_logger_github import ActionLoggerGitHub
    from mudyla.logging.action_logger_verbose import ActionLoggerVerbose
    from mudyla.logging.formatters import OutputFormatter

    capture = StringIO()
    monkeypatch.setattr(sys, "stdout", capture)
    monkeypatch.setattr(sys, "stderr", capture)
    output = OutputFormatter(no_color=True, compact=True, console=Console(file=capture, width=100, color_system=None))
    key = ActionKey.from_name("ask")
    logger = (ActionLoggerGitHub([key], output) if mode == "github"
              else ActionLoggerVerbose([key], output, parallel=False))
    fragments = [("ANSWER=héllo界\n", "stdout"), ("ERR_FRAGMENT", "stderr")]
    if stderr_first:
        fragments.reverse()
    for payload, stream in fragments:
        logger.write_output(key, payload, stream)
    payload = "".join(text for text, _ in fragments)
    assert capture.getvalue() == payload
    logger.mark_done(key, 1.0)
    boundary = "" if payload.endswith("\n") else "\n"
    assert capture.getvalue().startswith(payload + boundary + "ask@global: Finished (1.0 s)\n")


@pytest.mark.parametrize("mode,parallel", [("simple", False), ("verbose", False), ("github", False), ("verbose", True)])
def test_stream_modes_preserve_scheduling_defaults_and_parallel_override(tmp_path, mode, parallel):
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    script = ('from pathlib import Path\nimport time\n'
              'Path("NAME.start").write_text(str(time.monotonic()))\ntime.sleep(.15)\n'
              'Path("NAME.end").write_text(str(time.monotonic()))')
    (definitions / "actions.md").write_text("\n\n".join(
        f'# action: {name}\n\n```python\n{script.replace("NAME", name)}\n```' for name in ["left", "right"]))
    env = os.environ.copy()
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]), NO_COLOR="1")
    result = subprocess.run([sys.executable, "-m", "mudyla", "--without-nix", "--logger", mode,
                             *(["--par"] if parallel else []), ":left", ":right"],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    starts = [float((tmp_path / (name + ".start")).read_text()) for name in ["left", "right"]]
    ends = [float((tmp_path / (name + ".end")).read_text()) for name in ["left", "right"]]
    assert (max(starts) < min(ends)) == (parallel or mode == "simple")


@pytest.mark.parametrize("mode", ["simple", "verbose", "github"])
def test_stream_modes_share_sections_and_exact_action_markers(tmp_path, mode):
    result = run_project(tmp_path, ["--logger", mode], 'mdl.ret("value", 0, "int")')
    assert result.returncode == 0, result.stdout + result.stderr
    for heading in ["Run info:", "Contexts:", "Goals:", "Plan:", "Actions:", "Result:", "Outputs:"]:
        assert result.stdout.count(heading) == 1, result.stdout
    assert result.stdout.count("work@global: Running command `") == 1, result.stdout
    assert result.stdout.count("work@global: Finished (") == 1, result.stdout
    assert "Command:" not in result.stdout and "RUN " not in result.stdout


def test_parallel_verbose_prefixes_one_active_action_without_changing_capture(tmp_path, monkeypatch):
    startup = tmp_path / "startup"
    startup.mkdir()
    (startup / "sitecustomize.py").write_text('''import builtins, sys
from pathlib import Path
for stream in (sys.stdout, sys.stderr):
    stream.reconfigure(newline="\\r\\n")
original_open = builtins.open
def windows_open(*args, **kwargs):
    mode = args[1] if len(args) > 1 else kwargs.get("mode", "r")
    if (str(args[0]).endswith(("stdout.log", "stderr.log")) and "b" not in mode
            and any(flag in mode for flag in "wax+") and len(args) < 6 and kwargs.get("newline") is None):
        kwargs["newline"] = "\\r\\n"
    return original_open(*args, **kwargs)
builtins.open = windows_open
original_write = Path.write_text
def windows_write(path, *args, **kwargs):
    if path.name == "script.py" and len(args) < 4 and kwargs.get("newline") is None:
        kwargs["newline"] = "\\r\\n"
    return original_write(path, *args, **kwargs)
Path.write_text = windows_write
''', encoding="utf-8")
    original_popen = subprocess.Popen

    def spawn(*args, **kwargs):
        env = kwargs["env"].copy()
        env["PYTHONPATH"] = str(startup) + os.pathsep + env["PYTHONPATH"]
        return original_popen(*args, **{**kwargs, "env": env})

    monkeypatch.setattr(subprocess, "Popen", spawn)
    result = run_project(tmp_path, ["--logger", "verbose", "--par", "--keep-run-dir"],
        'import sys\nsys.stdout.write("FIRST_FRAGMENT\\nLAST"); sys.stdout.flush()')
    assert result.returncode == 0, result.stdout + result.stderr
    assert "work@global: FIRST_FRAGMENT\nwork@global: LAST\n" in result.stdout, result.stdout
    assert any(path.read_text() == "FIRST_FRAGMENT\nLAST" for path in (tmp_path / ".mdl" / "runs").rglob("stdout.log"))
    assert b"\r\n" not in next((tmp_path / ".mdl" / "runs").rglob("script.py")).read_bytes()


def test_action_marker_phrases_have_explicit_normal_intensity_colors():
    from rich.console import Console
    from rich.text import Text
    from mudyla.dag.context import ContextId
    from mudyla.dag.graph import ActionId, ActionKey
    from mudyla.logging.action_logger_simple import ActionLoggerSimple
    from mudyla.logging.formatters import OutputFormatter

    capture = StringIO()
    output = OutputFormatter(no_color=False, compact=True, console=Console(file=capture, width=120, force_terminal=True, color_system="standard", no_color=False))
    key = ActionKey(ActionId("work"), ContextId(axis_values=()))
    logger = ActionLoggerSimple([key], output)
    logger.begin_action(key, ["python3", "script.py"])
    logger.mark_done(key, 1.2)
    logger.mark_failed(key, 2.3)
    rendered = Text.from_ansi(capture.getvalue())
    for text, color in [("Running command", 3), ("python3 script.py", 4), ("Finished (1.2 s)", 2), ("Failed (2.3 s)", 1)]:
        assert text in rendered.plain, rendered.plain
        for index in range(rendered.plain.index(text), rendered.plain.index(text) + len(text)):
            style = rendered.get_style_at_offset(output.console, index)
            assert style.color is not None and style.color.number == color
            assert style.dim is not True and style.bold is not True


@pytest.mark.parametrize("mode", ["verbose", "github"])
@pytest.mark.parametrize("fragment", ["\x1b[31", "\x1b]0;OPEN", "\x1bPOPEN"])
def test_redirected_incomplete_controls_preserve_payload_without_terminal_repair(tmp_path, mode, fragment):
    result = run_project(tmp_path, ["--logger", mode, "--keep-run-dir"],
                         f'import sys\nsys.stdout.write({fragment!r}); sys.stdout.flush()')
    assert result.returncode == 0, result.stderr
    assert fragment in result.stdout and "\x18" not in result.stdout
    assert "work@global: Finished (" in result.stdout
    assert any(path.read_text() == fragment for path in (tmp_path / ".mdl" / "runs").rglob("stdout.log"))


@pytest.mark.parametrize("fragment", ["\x1b[31", "\x1b]0;OPEN", "\x1bPOPEN"])
def test_terminal_record_boundary_cancels_only_incomplete_controls(monkeypatch, fragment):
    from rich.console import Console
    from mudyla.dag.context import ContextId
    from mudyla.dag.graph import ActionId, ActionKey
    from mudyla.logging.action_logger_verbose import ActionLoggerVerbose
    from mudyla.logging.formatters import OutputFormatter

    class TerminalCapture(StringIO):
        def isatty(self):
            return True

    capture = TerminalCapture()
    monkeypatch.setattr(sys, "stdout", capture)
    monkeypatch.setenv("TERM", "xterm-256color")
    output = OutputFormatter(no_color=True, plain=True, compact=True, console=Console(file=capture, width=100, color_system=None))
    key = ActionKey(ActionId("work"), ContextId(axis_values=()))
    logger = ActionLoggerVerbose([key], output, parallel=False)
    logger.write_output(key, fragment, "stdout")
    assert capture.getvalue() == fragment
    logger.mark_done(key, 1.0)
    assert fragment + "\x18\n" in capture.getvalue()
    assert logger._streams["stdout"].control == "text"
    if fragment == "\x1b[31":
        pyte = pytest.importorskip("pyte")
        screen = pyte.Screen(100, 5)
        pyte.Stream(screen).feed(capture.getvalue().replace("\n", "\r\n"))
        assert "work@global: Finished (1.0 s)" in "\n".join(screen.display)


def test_parallel_prefixes_preserve_split_controls_crlf_and_stream_ownership(monkeypatch):
    from mudyla.dag.context import ContextId
    from mudyla.dag.graph import ActionId, ActionKey
    from mudyla.logging.action_logger_verbose import ActionLoggerVerbose
    from mudyla.logging.formatters import OutputFormatter

    out, err = StringIO(), StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    first, second = [ActionKey(ActionId(name), ContextId(axis_values=())) for name in ["first", "second"]]
    logger = ActionLoggerVerbose([first, second], OutputFormatter(no_color=True, compact=True), parallel=True)
    logger.write_output(first, "\x1b[0m", "stdout")
    assert out.getvalue() == "\x1b[0m", "A style-only fragment must not create an empty action record"
    out.seek(0)
    out.truncate()
    logger.write_output(first, "\x1b[3", "stdout")
    assert out.getvalue() == "\x1b[3"
    logger.write_output(first, "1mHELLO", "stdout")
    logger.write_output(second, "OTHER", "stdout")
    logger.write_output(first, "TAIL\n\n10%\r", "stdout")
    logger.write_output(first, "20%\r", "stdout")
    logger.write_output(first, "\n", "stdout")
    logger.write_output(second, "ERR", "stderr")
    assert out.getvalue() == ("\x1b[31mfirst@global: HELLO\nsecond@global: OTHER\n"
                              "first@global: TAIL\nfirst@global: \nfirst@global: 10%\rfirst@global: 20%\r\n")
    assert err.getvalue() == "second@global: ERR"
    logger.write_output(first, "\x1b]0;TITLE", "stdout")
    logger.write_output(second, "OTHER", "stdout")
    logger.write_output(first, "\x1b\\VISIBLE", "stdout")
    assert out.getvalue().endswith("\x1b]0;TITLEOTHER\x1b\\first@global: VISIBLE")
    logger.write_output(first, "\x1bP", "stdout")
    for _ in range(50):
        logger.write_output(first, "x" * 4096, "stdout")
    assert logger._streams["stdout"].control == "string"
    assert all(not isinstance(value, str) or len(value) < 20 for value in vars(logger._streams["stdout"]).values())
    logger.write_output(first, "\x1b\\END", "stdout")
    assert logger._streams["stdout"].control == "text"
    assert "\x18" not in out.getvalue()


@pytest.mark.parametrize("encoding", ["ascii", "cp1252"])
@pytest.mark.parametrize("options", [["--logger", "simple"], ["--simple-log"],
    ["--logger", "raw"], ["--verbose"], ["--github-actions"]])
def test_append_only_encoding_does_not_prevent_contextual_action_execution(tmp_path, encoding, options):
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (definitions / "actions.md").write_text(
        '# arguments\n- `args.message`: Context\n  - type: `string`\n  - default: sample\n\n'
        '# action: work\n```python\nmdl.use("args.message")\n'
        'mdl.ret("message", mdl.args["message"], "string")\n```\n', encoding="utf-8")
    env = os.environ.copy()
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]), NO_COLOR="1", TERM="dumb",
               PYTHONIOENCODING=encoding, COLUMNS="300")
    result = subprocess.run([sys.executable, "-m", "mudyla", "--without-nix", *options,
        "--keep-run-dir", "--out", "result.json", ":work", "--message=界", ":work", "--message=ascii"],
        cwd=tmp_path, env=env, capture_output=True, timeout=10)
    displayed = (result.stdout + result.stderr).decode(encoding)
    assert result.returncode == 0, displayed
    assert "UnicodeEncodeError" not in displayed
    assert displayed.count("Running command") == displayed.count("Finished (") == 2
    assert all(section in displayed for section in ["Run info:", "Contexts:", "Result:", "Outputs:"])
    values = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert "界" in str(values) and "ascii" in str(values)
    metadata = list((tmp_path / ".mdl" / "runs").rglob("meta.json"))
    assert len(metadata) == 2
    assert all(json.loads(path.read_text())["exit_code"] == 0 for path in metadata)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY")
@pytest.mark.parametrize("mode", ["simple", "github"])
@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("fragment", ["\x1b[31", "\x1b]0;OPEN", "\x1bP1qOPEN"])
def test_failure_replay_restores_terminal_text_before_result(tmp_path, mode, terminal, fragment):
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (definitions / "actions.md").write_text('# action: work\n```python\nimport sys\n'
        f'sys.stdout.write({fragment!r}); sys.stdout.flush()\nraise SystemExit(7)\n```\n')
    env = os.environ.copy()
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]), NO_COLOR="1", TERM="xterm-256color")
    argv = ["-m", "mudyla", "--without-nix", "--logger", mode, ":work"]
    if terminal:
        pexpect = pytest.importorskip("pexpect")
        child = pexpect.spawn(sys.executable, argv, cwd=str(tmp_path), env=env,
                              encoding="utf-8", dimensions=(120, 100), timeout=5)
        try:
            child.expect(pexpect.EOF)
            displayed = child.before
            child.close()
            assert child.exitstatus == 1
        finally:
            child.close(force=True)
    else:
        result = subprocess.run([sys.executable, *argv], cwd=tmp_path, env=env,
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 1
        displayed = result.stdout
    assert displayed.count(fragment) == (1 if mode == "simple" else 2)
    assert "Result:" in displayed and "Execution failed!" in displayed
    assert next((tmp_path / ".mdl" / "runs").rglob("stdout.log")).read_text() == fragment
    if terminal:
        assert displayed.count(fragment + "\x18\r\n") == displayed.count(fragment)
        if fragment == "\x1b[31":
            pyte = pytest.importorskip("pyte")
            screen = pyte.Screen(120, 100)
            pyte.Stream(screen).feed(displayed)
            visible = "\n".join(screen.display)
            assert "Result:" in visible and "Execution failed!" in visible
    else:
        assert "\x18" not in displayed


@pytest.mark.parametrize("mode,encoding", [
    ("pure", "utf-8"), ("table", "utf-8"), ("simple", "utf-8"), ("verbose", "utf-8"), ("github", "utf-8"),
    ("pure", "ascii"), ("pure", "cp1252"), ("table", "ascii"), ("table", "cp1252"),
])
@pytest.mark.parametrize("case", ["unknown-goal", "missing-defs"])
def test_unicode_preparation_errors_preserve_diagnostics_before_logger_start(tmp_path, mode, encoding, case):
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    source = '# action: work\n```python\nmdl.ret("done", True, "bool")\n```\n'
    definition = definitions / "actions.md"
    definition.write_text(source)
    env = os.environ.copy()
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]), PYTHONIOENCODING=encoding,
               NO_COLOR="1", TERM="dumb", COLUMNS="300")
    arguments = [":absent_界"] if case == "unknown-goal" else ["--defs", "absent_界.md", ":work"]
    result = subprocess.run([sys.executable, "-m", "mudyla", "--without-nix", "--logger", mode,
                            *(["--force-interactive"] if mode == "table" else []), *arguments],
                            cwd=tmp_path, env=env, capture_output=True, timeout=10)
    stdout = result.stdout.decode(encoding)
    stderr = result.stderr.decode(encoding)
    assert result.returncode == 1
    assert "UnicodeEncodeError" not in stderr and "Traceback" not in stderr, stderr
    expected = ("Action 'absent_界' not found. Available actions: work" if case == "unknown-goal"
                else "No markdown files found matching pattern: absent_界.md")
    assert expected.encode(encoding, errors="replace").decode(encoding) in stdout, stdout
    assert stdout.count("Using Nix:") == 1
    assert stdout.count("Run info:") == 1
    assert "Actions:" not in stdout and "Running command" not in stdout
    assert definition.read_text() == source
    assert not (tmp_path / ".mdl" / "runs").exists()


@pytest.mark.parametrize("mode", ["simple", "verbose", "github"])
def test_command_marker_retains_one_complete_logical_line(tmp_path, mode):
    columns = 80
    result = run_project(tmp_path, ["--logger", mode, "--keep-run-dir"], 'print("PAYLOAD")', columns=columns)
    assert result.returncode == 0, result.stderr
    command_lines = [line for line in result.stdout.splitlines() if "Running command" in line]
    assert len(command_lines) == 2
    assert all(len(line) > columns for line in command_lines), command_lines
    assert all(line.endswith("script.py`") for line in command_lines), command_lines


@pytest.mark.parametrize("script,completion,exit_code", [
    ('print("PAYLOAD")', "Finished (", 0),
    ('print("PAYLOAD"); raise SystemExit(7)', "Failed (", 1),
])
def test_github_completion_is_inside_each_action_group(tmp_path, script, completion, exit_code):
    result = run_project(tmp_path, ["--logger", "github"], script)
    assert result.returncode == exit_code
    action_group = result.stdout.split("::group::work\n", 1)[1].split("::endgroup::\n", 1)[0]
    assert "work@global: " + completion in action_group, action_group
    assert action_group.index("Running command") < action_group.index("PAYLOAD") < action_group.index(completion)
    assert result.stdout.count("::group::") == result.stdout.count("::endgroup::") == 2


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY")
def test_parallel_verbose_prefix_uses_marker_identity_foreground(tmp_path):
    from rich.console import Console
    pexpect = pytest.importorskip("pexpect")
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (definitions / "actions.md").write_text('# action: work\n```python\nprint("PAYLOAD")\n```\n')
    env = os.environ.copy()
    env.pop("NO_COLOR", None)
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]), TERM="xterm-256color", COLUMNS="80", LINES="24")
    child = pexpect.spawn(sys.executable, ["-m", "mudyla", "--without-nix", "--verbose", "--par",
                          "--keep-run-dir", ":work"], cwd=str(tmp_path), env=env,
                          encoding="utf-8", dimensions=(80, 24), timeout=5)
    try:
        child.expect(pexpect.EOF)
        rendered = terminal_text(child.before)
        child.close()
        assert child.exitstatus == 0
    finally:
        child.close(force=True)
    marker = rendered.plain.index("work@global: Running")
    prefix = rendered.plain.index("work@global: PAYLOAD")
    console = Console()
    for offset in range(len("work@global")):
        expected = rendered.get_style_at_offset(console, marker + offset).color
        assert expected is not None
        assert rendered.get_style_at_offset(console, prefix + offset).color == expected
    assert next((tmp_path / ".mdl" / "runs").rglob("stdout.log")).read_text() == "PAYLOAD\n"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY signals")
@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("cancel", ["timeout", "signal"])
def test_github_cancellation_closes_groups_after_any_completion(tmp_path, parallel, cancel):
    import signal
    pexpect = pytest.importorskip("pexpect")
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (definitions / "actions.md").write_text('# action: work\n```python\nimport time\n'
                                          'print("READY", flush=True)\ntime.sleep(30)\n```\n')
    env = os.environ.copy()
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]), NO_COLOR="1", TERM="xterm-256color")
    argv = ["-m", "mudyla", "--without-nix", "--logger", "github",
            *(["--par"] if parallel else ["--seq"]), *(["--timeout", "500"] if cancel == "timeout" else []), ":work"]
    capture = StringIO()
    child = pexpect.spawn(sys.executable, argv, cwd=str(tmp_path), env=env, encoding="utf-8", timeout=5)
    child.logfile_read = capture
    try:
        child.expect_exact("READY")
        if cancel == "signal":
            os.kill(child.pid, signal.SIGINT)
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == (130 if cancel == "signal" else 1)
    finally:
        child.close(force=True)
    output = capture.getvalue().replace("\r\n", "\n")
    assert output.count("::group::work\n") == output.count("::endgroup::\n") == 1
    for marker in ["work@global: Finished (", "work@global: Failed ("]:
        if marker in output:
            assert output.index(marker) < output.index("::endgroup::\n")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY descriptors")
@pytest.mark.parametrize("terminal_stream", ["stdout", "stderr"])
@pytest.mark.parametrize("policy", ["color", "no-color", "dumb"])
def test_verbose_prefix_color_uses_each_destination_capability(tmp_path, terminal_stream, policy):
    import pty
    import threading
    from rich.console import Console
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (definitions / "actions.md").write_text('# action: work\n```python\nimport sys\n'
                                          'print("OUT")\nprint("ERR", file=sys.stderr)\n```\n')
    env = os.environ.copy()
    env.pop("NO_COLOR", None)
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]), TERM="dumb" if policy == "dumb" else "xterm-256color",
               FORCE_COLOR="1", COLUMNS="160")
    if policy == "no-color":
        env["NO_COLOR"] = "1"
    master, slave = pty.openpty()
    data = bytearray()

    def read_terminal():
        try:
            while chunk := os.read(master, 65536):
                data.extend(chunk)
        except OSError:
            pass

    reader = threading.Thread(target=read_terminal)
    child = subprocess.Popen([sys.executable, "-m", "mudyla", "--without-nix", "--verbose", "--par",
                              "--keep-run-dir", ":work"], cwd=tmp_path, env=env,
                             stdout=slave if terminal_stream == "stdout" else subprocess.PIPE,
                             stderr=slave if terminal_stream == "stderr" else subprocess.PIPE)
    reader.start()
    try:
        stdout, stderr = child.communicate(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        os.close(slave)
        reader.join(timeout=2)
        os.close(master)
    assert not reader.is_alive()
    assert child.returncode == 0
    assert b"\x1b" not in (stderr if terminal_stream == "stdout" else stdout)
    rendered = terminal_text(data.decode())
    token = "OUT" if terminal_stream == "stdout" else "ERR"
    offset = rendered.plain.index("work@global: " + token)
    color = rendered.get_style_at_offset(Console(), offset).color
    assert (color is not None) == (policy == "color")
    assert next((tmp_path / ".mdl" / "runs").rglob("stderr.log")).read_text() == "ERR\n"
