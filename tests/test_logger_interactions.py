"""Shared action controls exercised through native terminal sessions."""

from io import StringIO
import os
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest
from rich.console import Console
from rich.text import Text

from tests.terminal_capture import terminal_text

from mudyla.dag.graph import ActionKey
from mudyla.logging.action_logger_table import ActionLoggerTable, TaskStatus, ViewState
from mudyla.logging.action_logger_pure import ActionLoggerPure
from mudyla.logging.formatters import OutputFormatter


@pytest.mark.parametrize("mode", ["pure", "table"])
@pytest.mark.parametrize("view,status", [
    *((view, TaskStatus.RUNNING) for view in ViewState),
    *((view, status) for view in [ViewState.TABLE, ViewState.LOGS_STDOUT] for status in [TaskStatus.TBD, TaskStatus.DONE]),
])
def test_input_hint_and_handler_share_running_stdout_or_overview_eligibility(mode, view, status):
    key = ActionKey.from_name("work")
    output = OutputFormatter(no_color=True, compact=True)
    console = Console(file=StringIO(), width=160, height=24, force_terminal=True, no_color=True)
    output._console = console
    logger = ActionLoggerPure([key], output, True) if mode == "pure" else ActionLoggerTable([key], no_color=True)
    logger.console = console
    logger.state = view
    logger.tasks[key].status = status
    eligible = status == TaskStatus.RUNNING and view in {ViewState.TABLE, ViewState.LOGS_STDOUT}
    assert ("i input" in logger._build_footer().plain) == eligible
    logger._handle_key_table("i")
    assert (logger._input_action == key) == eligible
    assert logger.state == view


@pytest.mark.parametrize("mode", ["pure", "table"])
@pytest.mark.parametrize("view,ending", [(ViewState.TABLE, "q close"), (ViewState.LOGS_STDOUT, "q back")])
def test_input_completion_feedback_preserves_ordinary_navigation(mode, view, ending):
    key = ActionKey.from_name("work")
    output = OutputFormatter(no_color=True, compact=True)
    console = Console(file=StringIO(), width=120, force_terminal=True, no_color=True)
    output._console = console
    logger = ActionLoggerPure([key], output, True) if mode == "pure" else ActionLoggerTable([key], no_color=True)
    logger.console = console
    logger.mark_done(key, .1)
    logger.mark_execution_complete()
    logger.state = view
    logger._input_message = "Action finished"
    footer = logger._build_footer().plain
    assert "Action finished" in footer and ending in footer
    assert "j/k" in footer and "i input" not in footer


@pytest.mark.parametrize("mode", ["pure", "table"])
def test_action_finishing_during_input_restores_navigation(terminal_project, tmp_path, mode):
    import pexpect

    release = tmp_path / "release"
    child = terminal_project(mode, '# action: work\n\n```python\nfrom pathlib import Path\nimport time\n'
                             'print("WAIT_RELEASE", flush=True)\n'
                             f'while not Path({str(release)!r}).exists(): time.sleep(.01)\n```\n',
                             options=("--it",))
    child.expect_exact("q kill")
    child.send("l")
    child.expect_exact("WAIT_RELEASE")
    child.send("iUNSENT")
    child.expect_exact("UNSENT")
    release.touch()
    child.expect_exact("Action finished")
    footer = terminal_text(child.before).plain
    assert "q back" in footer and "j/k" in footer, footer
    child.send("q")
    child.expect_exact("q close")
    child.send("q")
    child.expect(pexpect.EOF)
    child.close()
    assert child.exitstatus == 0


@pytest.mark.parametrize("mode", ["pure", "table"])
def test_nonstdout_input_shortcut_does_not_capture_navigation(terminal_project, tmp_path, mode):
    import pexpect

    release = tmp_path / "release"
    child = terminal_project(mode, '# action: work\n\n```python\nfrom pathlib import Path\nimport time\n'
                             'print("WAIT_RELEASE", flush=True)\n'
                             f'while not Path({str(release)!r}).exists(): time.sleep(.01)\n```\n',
                             options=("--it",))
    child.expect_exact("q kill")
    for view_key in ["e", "m", "o", "s"]:
        child.send(view_key)
        child.expect_exact("q back")
        child.send("iq")
        child.expect_exact("q kill")
    release.touch()
    child.expect_exact("q close")
    child.expect_exact("q close")
    assert "i input" not in terminal_text(child.before).plain
    child.send("q")
    child.expect(pexpect.EOF)
    child.close()
    assert child.exitstatus == 0


@pytest.mark.parametrize("no_color", [False, True])
@pytest.mark.parametrize("view", [ViewState.OUTPUT, ViewState.SOURCE])
def test_pure_json_long_values_remain_reachable_at_five_rows(tmp_path, no_color, view):
    output = OutputFormatter(no_color=no_color, compact=True)
    output._console = Console(file=StringIO(), width=40, height=5, force_terminal=True, no_color=no_color)
    key = ActionKey.from_name("work")
    logger = ActionLoggerPure([key], output, True)
    logger.mark_running(key, tmp_path)
    logger.mark_done(key, .1)
    (tmp_path / "output.json").write_text(json.dumps({"description": {
        "type": "string", "value": "BEGIN " + "long_value " * 12 + " TARGET_END"}}))
    (tmp_path / "script.py").write_text('print("' + "long_value " * 12 + ' TARGET_END")')
    logger.state = view
    logger._build_renderable()
    logger._handle_key_scroll("top")
    frames = []
    for _ in range(15):
        with output.console.capture() as capture:
            output.console.print(logger._build_renderable())
        frames.append(Text.from_ansi(capture.get()).plain)
        logger._handle_key_scroll("page_down")
    assert "TARGET_END" in "\n".join(frames)


def test_failed_metadata_prioritizes_diagnosis_in_five_rows(tmp_path):
    output = OutputFormatter(no_color=True, compact=True)
    output._console = Console(file=StringIO(), width=40, height=5, force_terminal=True, no_color=True)
    key = ActionKey.from_name("failed")
    logger = ActionLoggerPure([key], output, True)
    logger.mark_running(key, tmp_path)
    logger.mark_failed(key, .1)
    (tmp_path / "meta.json").write_text(json.dumps({"action_name": "failed", "success": False,
                                                   "exit_code": 7, "error_message": "FAILED_MARKER"}))
    logger.state = ViewState.META
    with output.console.capture() as capture:
        output.console.print(logger._build_renderable())
    frame = Text.from_ansi(capture.get()).plain
    assert "failed" in frame and "Exit code" in frame and "7" in frame
    assert "FAILED_MARKER" in frame


def test_large_metadata_values_keep_live_navigation_and_original_json(terminal_project, tmp_path):
    import pexpect

    child = terminal_project("pure", '# action: work\n\n```python\nprint("COMPLETE")\n```\n',
                             options=("--it",), dimensions=(5, 40))
    child.expect_exact("q close")
    path = next((tmp_path / ".mdl" / "runs").glob("*/work/meta.json"))
    original = json.dumps({"duration_seconds": 10 ** 1000 + 123, "stdout_size": 10 ** 400 + 789}, indent=2)
    path.write_text(original, encoding="utf-8")
    child.send("m")
    child.expect_exact("Duration")
    child.send("G")
    child.expect_exact("789")
    child.send("v")
    child.expect_exact("duration_seconds")
    child.send("G")
    child.expect_exact("789")
    child.send("q")
    child.expect_exact("q close")
    assert path.read_text(encoding="utf-8") == original
    child.send("q")
    child.expect(pexpect.EOF)
    child.close()
    assert child.exitstatus == 0


@pytest.mark.parametrize("view,filename", [(ViewState.META, "meta.json"), (ViewState.OUTPUT, "output.json")])
def test_pure_json_toggle_preserves_original_text_and_independent_positions(tmp_path, view, filename):
    output = OutputFormatter(no_color=False, compact=True)
    output._console = Console(file=StringIO(), width=40, height=5, force_terminal=True)
    key = ActionKey.from_name("work")
    logger = ActionLoggerPure([key], output, True)
    logger.mark_running(key, tmp_path)
    logger.mark_done(key, .1)
    original = '{\n "lexical": 1.2300,\n "escaped": "\\u69cb\\u7bc9",\n' + ',\n'.join(
        f' "field{i:02d}": "VALUE_{i:02d}"' for i in range(30)) + '\n}\n'
    path = tmp_path / filename
    path.write_text(original, encoding="utf-8")
    logger.state = view

    def frame():
        with output.console.capture() as capture:
            output.console.print(logger._build_renderable())
        return Text.from_ansi(capture.get()).plain

    assert "1.23" in frame()
    logger._handle_key_scroll("page_down")
    formatted = frame()
    logger._handle_key_scroll("v")
    raw_top = frame()
    assert "1.2300" in raw_top and r"\u69cb\u7bc9" in raw_top
    logger._handle_key_scroll("page_down")
    raw_scrolled = frame()
    assert raw_scrolled != raw_top
    logger._handle_key_scroll("v")
    assert frame() == formatted
    logger._handle_key_scroll("v")
    assert frame() == raw_scrolled
    logger._handle_key_scroll("bottom")
    assert "VALUE_29" in frame()
    assert path.read_text(encoding="utf-8") == original


def test_pure_incomplete_json_preserves_raw_text_until_refresh(tmp_path):
    output = OutputFormatter(no_color=True, compact=True)
    output._console = Console(file=StringIO(), width=60, height=10, force_terminal=True)
    key = ActionKey.from_name("work")
    logger = ActionLoggerPure([key], output, True)
    logger.mark_running(key, tmp_path)
    path = tmp_path / "output.json"
    path.write_text('{"partial":')
    logger.state = ViewState.OUTPUT
    assert "Read error" in logger._build_detail_content().plain
    logger._handle_key_scroll("v")
    assert '{"partial":' in logger._build_detail_content().plain
    path.write_text('{"partial": false}')
    logger._handle_key_scroll("r")
    logger._handle_key_scroll("v")
    assert "false" in logger._build_detail_content().plain


@pytest.mark.parametrize("view", [ViewState.TABLE, ViewState.META, ViewState.OUTPUT, ViewState.LOGS_STDOUT])
def test_pure_sections_keep_keyboard_help_last_and_selectors_under_heading(tmp_path, view):
    output = OutputFormatter(no_color=True, compact=True)
    output._console = Console(file=StringIO(), width=80, height=12, force_terminal=True, no_color=True)
    key = ActionKey.from_name("work")
    logger = ActionLoggerPure([key], output, True)
    logger.mark_running(key, tmp_path)
    (tmp_path / "stdout.log").write_text("LOG_CONTENT\n")
    (tmp_path / "output.json").write_text('{"value": {"type":"int", "value":42}}')
    logger.state = view
    with output.console.capture() as capture:
        output.console.print(logger._build_renderable())
    rows = Text.from_ansi(capture.get()).plain.splitlines()
    assert "q kill" in rows[-1] if view == ViewState.TABLE else "q back" in rows[-1]
    assert "running" in rows[-2]
    if view in {ViewState.META, ViewState.OUTPUT}:
        assert "v JSON" in rows[1]
        assert "v JSON" not in rows[-1]


@pytest.fixture
def terminal_project(tmp_path):
    if sys.platform == "win32":
        pytest.skip("Native POSIX PTY; Windows decoder covered separately")
    pexpect = pytest.importorskip("pexpect")
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    children = []

    def launch(mode, actions, options=(), goals=(":work",), dimensions=(24, 100), term="xterm-256color"):
        (definitions / "actions.md").write_text(actions, encoding="utf-8")
        env = os.environ.copy()
        env.update(TERM=term, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        child = pexpect.spawn(sys.executable,
                              ["-m", "mudyla", "--without-nix", "--logger", mode, *options, *goals],
                              cwd=str(tmp_path), env=env, encoding="utf-8", timeout=5, dimensions=dimensions)
        children.append(child)
        return child

    yield launch
    for child in children:
        if child.isalive():
            child.sendcontrol("c")
            try:
                child.expect(pexpect.EOF, timeout=3)
            except pexpect.TIMEOUT:
                pass
        child.close(force=True)


@pytest.mark.parametrize("mode", ["pure", "table"])
@pytest.mark.parametrize("term", ["dumb", "unknown"])
def test_force_interactive_overrides_terminal_capability(terminal_project, mode, term):
    import pexpect

    child = terminal_project(mode, '# action: work\n\n```python\nprint("COMPLETE")\n```\n',
                             options=("--force-interactive", "--it"), term=term)
    child.expect_exact("q close")
    child.send("l")
    child.expect_exact("COMPLETE")
    child.send("qq")
    child.expect(pexpect.EOF)
    child.close()
    assert child.exitstatus == 0


@pytest.mark.parametrize("view", [ViewState.TABLE, ViewState.LOGS_STDOUT])
def test_table_small_frames_do_not_fill_the_terminal(tmp_path, monkeypatch, view):
    keys = [ActionKey.from_name(name) for name in ["base", "work"]]
    logger = ActionLoggerTable(keys, no_color=True)
    logger.console = Console(file=StringIO(), width=120, height=60, force_terminal=True, no_color=True)
    monkeypatch.setattr(logger, "_get_terminal_size", lambda: (120, 60))
    logger.mark_running(keys[0], tmp_path)
    (tmp_path / "stdout.log").write_text("FIRST_LOG_LINE\nSECOND_LOG_LINE\n")
    logger.state = view
    lines = logger.console.render_lines(logger._build_renderable(), pad=False)
    assert len(lines) <= 9, "Short table/detail frames must leave preceding run information visible"
    text = "\n".join("".join(segment.text for segment in line) for line in lines)
    assert "base" in text and ("work" in text if view == ViewState.TABLE else "SECOND_LOG_LINE" in text)


def test_table_native_run_information_stays_in_normal_terminal_history(terminal_project, tmp_path):
    import pexpect

    release = tmp_path / "release"
    actions = ('# arguments\n- `args.flavor`: Context\n  - type: `string`\n  - default: demo\n\n'
               '# action: base\n```python\nprint("BASE_OUTPUT")\n```\n\n'
               '# action: work\n```python\nmdl.use("args.flavor")\nmdl.dep("action.base")\n'
               'from pathlib import Path\nimport time\nprint("WORK_STDOUT", flush=True)\n'
               f'while not Path({str(release)!r}).exists(): time.sleep(.01)\n```\n')
    child = terminal_project("table", actions, options=("--no-color",), dimensions=(60, 120))
    transcript = StringIO()
    child.logfile_read = transcript
    child.expect_exact("q kill")
    child.expect_exact("q kill")
    initial = transcript.getvalue()
    for field in ["Project root:", "Execution mode:", "Run ID:", "Contexts:", "Plan:"]:
        assert field in terminal_text(initial).plain
    assert "\x1b[?1049h" not in initial, "An alternate screen hides the printed run information"
    assert "\x1b[?1000h" not in initial and "\x1b[?1006h" not in initial
    child.send("jl")
    child.expect_exact("WORK_STDOUT")
    child.expect_exact("q back")
    child.setwinsize(40, 100)
    child.expect_exact("q back")
    child.send("q")
    child.expect_exact("q kill")
    release.touch()
    child.expect(pexpect.EOF)
    child.close()
    assert child.exitstatus == 0
    for code in ["1049", "1000", "1006"]:
        assert transcript.getvalue().count(f"\x1b[?{code}h") == transcript.getvalue().count(f"\x1b[?{code}l") == 1


@pytest.mark.parametrize("execution", ["--seq", "--par"])
def test_live_tree_tracks_real_shared_action_starts_and_completion(terminal_project, tmp_path, execution):
    import pexpect
    import re

    actions = ''
    for name in ["base", "left", "right"]:
        dependency = 'mdl.dep("action.base")\n' if name != "base" else ''
        actions += (f'# action: {name}\n\n```python\n{dependency}from pathlib import Path\nimport time\n'
                    f'Path("{name}-started").touch()\nprint("{name.upper()}_WAIT", flush=True)\n'
                    f'while not Path("{name}-release").exists(): time.sleep(.02)\n'
                    'mdl.ret("ok", True, "bool")\n```\n\n')
    actions += '# action: goal\n\n```python\nmdl.dep("action.left")\nmdl.dep("action.right")\nmdl.ret("ok", True, "bool")\n```\n'
    child = terminal_project("pure", actions, options=("--it", "--plan-tree", execution), goals=(":goal",), dimensions=(40, 100))
    child.expect_exact("BASE_WAIT")

    def current_tree():
        child.expect_exact("q kill")
        child.expect_exact("q kill")
        return terminal_text(child.before).plain.split("Actions:", 1)[0]

    first = current_tree()
    running = r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]"
    assert len(re.findall(running + r" base", first)) == 1, first
    assert first.count("○ goal") == 2
    assert "○ left" in first and "○ right" in first
    assert "Execution plan:" not in first and "Plan:" in first
    (tmp_path / "base-release").touch()
    child.expect_exact("LEFT_WAIT")
    if execution == "--par":
        deadline = time.monotonic() + 3
        while not (tmp_path / "right-started").exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert (tmp_path / "right-started").exists()
    after = current_tree()
    assert after.count("✓ base") == 1, after
    assert after.count("○ goal") == 2
    assert re.search(running + r" left", after), after
    assert ("◇ right" in after) if execution == "--seq" else re.search(running + r" right", after)
    assert not (tmp_path / "right-started").exists() if execution == "--seq" else True
    (tmp_path / "left-release").touch()
    (tmp_path / "right-release").touch()
    child.expect_exact("q close")
    child.expect_exact("q close")
    complete = terminal_text(child.before).plain.split("Actions:", 1)[0]
    assert "✓ left" in complete and "✓ right" in complete and complete.count("✓ base") == 1
    assert complete.count("✓ goal") == 2
    child.send("q")
    child.expect(pexpect.EOF)
    child.close()
    assert child.exitstatus == 0


def test_live_dag_resizes_detail_and_restores_complete_final_actions(terminal_project, tmp_path):
    import pexpect
    import re

    actions = ('# action: source\n\n```python\nfrom pathlib import Path\nimport time\n'
               'print("\\n".join("LOG_LINE_" + str(i) for i in range(20)), flush=True)\n'
               'print("SOURCE_WAIT", flush=True)\n'
               'while not Path("release").exists(): time.sleep(.02)\n```\n\n')
    for name in ["left", "right"]:
        actions += f'# action: {name}\n\n```python\nmdl.dep("action.source")\n```\n\n'
    actions += '# action: goal\n\n```python\nmdl.dep("action.left")\nmdl.dep("action.right")\n```\n'
    child = terminal_project("pure", actions, options=("--it", "--par", "--plan-dag"),
                             goals=(":goal",), dimensions=(40, 100))
    child.expect_exact("SOURCE_WAIT")
    child.expect_exact("q kill")
    child.expect_exact("q kill")
    initial = terminal_text(child.before).plain
    assert "Plan:" not in initial
    assert len(re.findall(r"goal\s+\(@global; goal\)", initial)) == 1, initial
    child.send("l")
    child.expect_exact("q back")
    child.setwinsize(5, 40)
    child.expect_exact("SOURCE_WAIT")
    child.setwinsize(40, 100)
    child.expect_exact("q back")
    child.send("q")
    child.expect_exact("q kill")
    child.expect_exact("q kill")
    returned = terminal_text(child.before).plain
    assert "Plan:" not in returned and "Actions:" in returned
    assert "LOG_LINE_19" not in returned
    (tmp_path / "release").touch()
    child.expect_exact("q close")
    child.send("q")
    child.expect(pexpect.EOF)
    assert "\x1b[?1049l" in child.before
    final = terminal_text(child.before.split("\x1b[?1049l", 1)[1]).plain
    child.close()
    assert child.exitstatus == 0
    assert "Plan:" not in final and final.count("Actions:") == 1, final
    actions = final.split("Actions:", 1)[1].split("Result:", 1)[0]
    assert len(re.findall(r"goal\s+\(@global; goal\)", actions)) == 1
    assert all(name in actions for name in ["source", "left", "right", "goal"])


@pytest.mark.parametrize("mode", ["pure", "table"])
def test_keep_open_all_detail_views_scroll_and_quit(terminal_project, mode):
    import pexpect

    child = terminal_project(mode, '# action: work\n\n```python\nimport sys\n'
                             'for n in range(80):\n    print(f"STDOUT_{n:03d}")\n'
                             'print("STDERR_MARKER", file=sys.stderr)\nmdl.ret("answer", 42, "int")\n```\n',
                             options=("--interactive",))
    child.expect_exact("q close")
    for key, marker in [("l", "STDOUT_079"), ("e", "STDERR_MARKER"),
                        ("m", "Action" if mode == "pure" else '"action_name"'),
                        ("o", "answer" if mode == "pure" else '"answer"'), ("s", "ret")]:
        child.send(key)
        child.expect_exact(marker)
        if key == "l":
            child.send("gg")
            child.expect_exact("STDOUT_000")
            child.send("G")
            child.expect_exact("STDOUT_079")
            child.send("r")
        if mode == "pure" and key in {"m", "o"}:
            child.send("v")
            child.expect_exact('"action_name"' if key == "m" else '"answer"')
            child.send("v")
            child.expect_exact(marker)
        child.send("q")
        child.expect_exact("q close")
        assert child.isalive()
    child.send("q")
    child.expect(pexpect.EOF)
    child.close()
    assert child.exitstatus == 0, child.before


@pytest.mark.parametrize("context_options", [(), ("--full-ctx-reprs",)])
def test_pure_overview_contains_the_complete_cli_run_information(terminal_project, tmp_path, context_options):
    import pexpect
    import re

    previous = tmp_path / ".mdl" / "runs" / "previous-run"
    previous.mkdir(parents=True)
    actions = ('# arguments\n\n- `args.message`: message\n  - type: `string`\n'
               '# Axis\n\n- `platform`=`{local*|other}`\n\n'
               '# action: base\n\n```python\nprint("BASE_COMPLETE")\n```\n\n'
               '# action: work\n\n```bash\ndep action.base\n'
               'echo "${args.message}" >/dev/null\necho WORK_COMPLETE\n```\n')
    child = terminal_project("pure", actions, options=("--it", "--continue", *context_options),
                             goals=("work", "--message=CONTEXT_ONE", ":work", "--message=CONTEXT_TWO"),
                             dimensions=(40, 140))
    child.expect_exact("\x1b[?1049h")
    prelude = terminal_text(child.before).plain
    child.expect_exact("q close")
    pages = []
    for key in ["\x1b[H", "\x1b[6~", "\x1b[6~"]:
        child.send(key)
        child.expect_exact("q close")
        pages.append(terminal_text(child.before).plain)
    overview = "\n".join(pages)
    run_id = re.search(r"Run ID:\s*(\S+)", prelude).group(1)
    markers = ["Using Nix: No (disabled with --without-nix)", str(tmp_path),
               "Using default axes:", "platform:local", "definition file(s)",
               "Goal should start with ':'", "Contexts:", "@global", 'message="CONTEXT_ONE"',
               'message="CONTEXT_TWO"', "with", "Goals:", "Execution mode:", "Built plan graph",
               "Continuing from previous run:", "previous-run", "Run ID:", run_id]
    assert all("".join(marker.split()) in "".join(prelude.split()) for marker in markers), prelude
    missing = [marker for marker in markers if "".join(marker.split()) not in "".join(overview.split())]
    assert not missing, f"CLI run information missing from the complete live overview: {missing}"
    child.send("q")
    child.expect(pexpect.EOF)
    child.close()
    assert child.exitstatus == 0, child.before


def test_pure_mouse_wheel_scrolls_details_and_capture_spans_the_interactive_session(terminal_project):
    import pexpect

    child = terminal_project("pure", '# action: work\n\n```python\n'
                             'for n in range(100):\n    print(f"LOG_{n:03d}")\n```\n',
                             options=("--it",), dimensions=(24, 100))
    recorded = StringIO()
    child.logfile_read = recorded
    child.expect_exact("q close")
    assert "\x1b[?1000h" in recorded.getvalue()
    child.send("l")
    visible = 21
    child.expect_exact(f"{101 - visible}-100/100 live")
    child.send("\x1b[<64;10;5M")
    child.expect_exact(f"{98 - visible}-97/100")
    child.send("\x1b[<65;10;5M")
    child.expect_exact(f"{101 - visible}-100/100 live")
    child.send("q")
    child.expect_exact("q close")
    assert "\x1b[?1000l" not in recorded.getvalue()
    child.send("q")
    child.expect(pexpect.EOF)
    child.close()
    assert child.exitstatus == 0
    assert recorded.getvalue().count("\x1b[?1000h") == recorded.getvalue().count("\x1b[?1000l") == 1
    assert recorded.getvalue().count("\x1b[?1006h") == recorded.getvalue().count("\x1b[?1006l") == 1


@pytest.mark.parametrize("mode", ["pure", "table"])
@pytest.mark.parametrize("ending", ["interrupt", "timeout"])
def test_detail_mouse_capture_restores_after_cancellation(terminal_project, mode, ending):
    import pexpect

    options = ("--timeout", "1000") if ending == "timeout" else ()
    child = terminal_project(mode, '# action: work\n\n```python\nimport time\n'
                             'print("ACTION_READY", flush=True)\ntime.sleep(30)\n```\n', options=options)
    recorded = StringIO()
    child.logfile_read = recorded
    child.expect_exact("q kill")
    child.send("l")
    child.expect_exact("ACTION_READY")
    if ending == "interrupt":
        child.sendcontrol("c")
    child.expect(pexpect.EOF)
    child.close()
    assert child.exitstatus == (130 if ending == "interrupt" else 1)
    expected = 1
    assert recorded.getvalue().count("\x1b[?1000h") == recorded.getvalue().count("\x1b[?1000l") == expected
    assert recorded.getvalue().count("\x1b[?1006h") == recorded.getvalue().count("\x1b[?1006l") == expected


@pytest.mark.parametrize("mode", ["pure", "table"])
def test_input_targets_selected_parallel_action_and_preserves_unicode(terminal_project, tmp_path, mode):
    import pexpect

    actions = ""
    for name in ["first", "second"]:
        actions += (f'# action: {name}\n\n```python\nfrom pathlib import Path\n'
                    f'Path({str(tmp_path / (name + ".ready"))!r}).touch()\n'
                    f'answer = input("PROMPT_{name}: ")\n'
                    f'Path({str(tmp_path / (name + ".answer"))!r}).write_text(answer, encoding="utf-8")\n```\n\n')
    child = terminal_project(mode, actions, options=("--par", "--it"), goals=(":first", ":second"))
    child.expect_exact("q kill")
    deadline = time.monotonic() + 3
    while not all((tmp_path / (name + ".ready")).exists() for name in ["first", "second"]):
        assert time.monotonic() < deadline
        try:
            child.read_nonblocking(65536, timeout=.02)
        except pexpect.TIMEOUT:
            pass
    child.send("ié界🙂x\x7fqjk\n")
    child.expect_exact("Action finished")
    assert (tmp_path / "first.answer").read_text(encoding="utf-8") == "é界🙂qjk"
    assert not (tmp_path / "second.answer").exists()
    child.send("jiSECOND\n")
    child.expect_exact("q close")
    assert (tmp_path / "second.answer").read_text() == "SECOND"
    child.send("q")
    child.expect(pexpect.EOF)
    child.close()
    assert child.exitstatus == 0


@pytest.mark.parametrize("mode", ["pure", "table"])
def test_input_eof_and_cancel_restore_terminal(terminal_project, tmp_path, mode):
    import pexpect
    import termios

    child = terminal_project(mode, '# action: work\n\n```python\nimport sys, time\n'
                             f'from pathlib import Path\nPath({str(tmp_path / "ready")!r}).touch()\n'
                             'print("WAIT_EOF", flush=True)\nsys.stdin.read()\n'
                             'print("GOT_EOF", flush=True)\ntime.sleep(30)\n```\n')
    original = termios.tcgetattr(child.child_fd)[3]
    child.expect_exact("q kill")
    deadline = time.monotonic() + 3
    while not (tmp_path / "ready").exists():
        assert time.monotonic() < deadline
        try:
            child.read_nonblocking(65536, timeout=.02)
        except pexpect.TIMEOUT:
            pass
    child.send("i\x04")
    child.send("l")
    child.expect_exact("GOT_EOF")
    child.send("q")
    child.expect_exact("q kill")
    child.send("iUNSENT")
    child.sendcontrol("c")
    child.expect(pexpect.EOF)
    restored = termios.tcgetattr(child.child_fd)[3]
    restored_mask = termios.ICANON | termios.ECHO | termios.NOFLSH
    assert restored & restored_mask == original & restored_mask
    child.close()
    assert child.exitstatus == 130


@pytest.mark.parametrize("mode", ["pure", "table"])
def test_closed_child_stdin_reports_delivery_error_without_failing_action(terminal_project, mode):
    import pexpect

    child = terminal_project(mode, '# action: work\n\n```python\nimport os, time\n'
                             'os.close(0)\nprint("INPUT_CLOSED", flush=True)\ntime.sleep(1)\n```\n',
                             options=("--it",))
    child.expect_exact("q kill")
    child.send("l")
    child.expect_exact("INPUT_CLOSED")
    child.send("ihello\n")
    child.expect_exact("Action input closed before delivery")
    child.send("\x1bq")
    child.expect_exact("q close")
    child.send("q")
    child.expect(pexpect.EOF)
    child.close()
    assert child.exitstatus == 0


def test_tiny_detail_input_editor_remains_visible(tmp_path):
    output = OutputFormatter(no_color=True, compact=True)
    output._console = Console(file=StringIO(), width=40, height=5, force_terminal=True)
    key = ActionKey.from_name("work")
    logger = ActionLoggerPure([key], output, True)
    logger.mark_running(key, tmp_path)
    (tmp_path / "stdout.log").write_text("\n".join(f"LINE_{n:03d}" for n in range(20)))
    logger.state = ViewState.LOGS_STDOUT
    logger._handle_key_table("i")
    logger._handle_input_key("x")
    stream = StringIO()
    Console(file=stream, width=40, height=5).print(logger._build_renderable())
    assert len(stream.getvalue().splitlines()) == 5
    assert "work > x|" in stream.getvalue().splitlines()[-1]


@pytest.mark.parametrize("width", [12, 40, 80])
@pytest.mark.parametrize("text", ["abcdefghijklmnopqrstuvwxyz0123456789LAST_TYPED", "界🙂é" * 20 + "LAST_TYPED"])
def test_input_footer_scrolls_to_cursor_without_changing_delivery(width, text):
    output = OutputFormatter(no_color=True, compact=True)
    output._console = Console(file=StringIO(), width=width, height=5, force_terminal=True)
    key = ActionKey.from_name("long-action-name")
    logger = ActionLoggerPure([key], output, True)
    logger.mark_running(key)
    logger._handle_key_table("i")
    for char in text:
        logger._handle_input_key(char)
    stream = StringIO()
    Console(file=stream, width=width).print(logger._build_footer())
    assert len(stream.getvalue().splitlines()) == 1
    assert "ED|" in stream.getvalue()
    for _ in range(10):
        logger._handle_input_key("left")
    assert "|" in logger._build_footer().plain
    delivered = []
    logger.set_input_callback(lambda action, line: delivered.append((action, line)))
    logger._handle_input_key("enter")
    assert delivered == [(key, text + "\n")]


def test_blocked_child_input_writer_does_not_block_controls():
    from mudyla.executor.engine import ExecutionEngine, RunningAction

    key = ActionKey.from_name("work")
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                               stdin=subprocess.PIPE, text=True, encoding="utf-8")
    engine = ExecutionEngine.__new__(ExecutionEngine)
    running = RunningAction(process)
    engine._running_processes = {key: running}
    engine._processes_lock = threading.Lock()
    output = OutputFormatter(no_color=True, compact=True)
    output._console = Console(file=StringIO())
    logger = ActionLoggerPure([key], output, True)
    engine._current_logger = logger
    try:
        assert engine._send_action_input(key, "x" * 1000000) is None
        assert running.input_thread is not None
        running.input_thread.join(timeout=.05)
        assert running.input_thread.is_alive()
        assert engine._send_action_input(key, "second") == "Previous input is still being written"
        running.stop_input.set()
        running.input_thread.join(timeout=1)
        assert not running.input_thread.is_alive()
        assert process.poll() is None
        assert "closed before delivery" in logger._input_message
        process.kill()
        process.wait(timeout=2)
        running.input_thread.join(timeout=2)
        assert not running.input_thread.is_alive()
        assert "closed before delivery" in logger._input_message
        assert engine._send_action_input(key, "late") == "Action input is closed"
        assert engine._send_action_input(ActionKey.from_name("other"), "late") == "Action is not ready for input or has finished"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if process.stdin is not None:
            process.stdin.close()


def test_input_delivery_stops_when_parent_exits_with_descendant_holding_pipe(tmp_path):
    from mudyla.executor.engine import ExecutionEngine, RunningAction
    import signal

    key = ActionKey.from_name("work")
    script = ('import subprocess, sys, time\nfrom pathlib import Path\n'
              'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], '
              'stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n'
              f'Path({str(tmp_path / "descendant.pid")!r}).write_text(str(child.pid))\n'
              f'while not Path({str(tmp_path / "finish")!r}).exists(): time.sleep(.01)\n')
    process = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               text=True, encoding="utf-8", start_new_session=sys.platform != "win32")
    engine = ExecutionEngine.__new__(ExecutionEngine)
    running = RunningAction(process)
    engine._running_processes = {key: running}
    engine._processes_lock = threading.Lock()
    output = OutputFormatter(no_color=True, compact=True)
    output._console = Console(file=StringIO())
    logger = ActionLoggerPure([key], output, True)
    engine._current_logger = logger
    try:
        deadline = time.monotonic() + 3
        while not (tmp_path / "descendant.pid").exists():
            assert time.monotonic() < deadline
            time.sleep(.01)
        assert engine._send_action_input(key, "構築🙂" * 100000) is None
        running.input_thread.join(timeout=.05)
        assert running.input_thread.is_alive()
        (tmp_path / "finish").touch()
        process.wait(timeout=2)
        running.input_thread.join(timeout=.5)
        assert not running.input_thread.is_alive(), "parent exit must end pending input even while a descendant holds stdin"
        assert "closed before delivery" in logger._input_message
    finally:
        try:
            os.kill(int((tmp_path / "descendant.pid").read_text()), signal.SIGTERM)
        except (FileNotFoundError, ProcessLookupError):
            pass
        if process.poll() is None:
            process.kill()
            process.wait()
        if running.input_thread is not None:
            running.input_thread.join(timeout=2)
        process.stdin.close()


def test_partial_nonblocking_input_writes_preserve_utf8(monkeypatch):
    from mudyla.executor.engine import ExecutionEngine, RunningAction

    key = ActionKey.from_name("work")
    process = subprocess.Popen([sys.executable, "-c", "import sys; print(input(), flush=True)"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8")
    engine = ExecutionEngine.__new__(ExecutionEngine)
    running = RunningAction(process)
    engine._running_processes = {key: running}
    engine._processes_lock = threading.Lock()
    engine._current_logger = None
    original_write = os.write
    attempts = 0

    def partial_write(fd, value):
        nonlocal attempts
        if fd != process.stdin.fileno():
            return original_write(fd, value)
        attempts += 1
        if attempts == 1:
            return 0
        if attempts % 3 == 0:
            raise BlockingIOError()
        return original_write(fd, value[:1])

    monkeypatch.setattr(os, "write", partial_write)
    try:
        assert engine._send_action_input(key, "a界🙂é\n") is None
        running.input_thread.join(timeout=2)
        assert not running.input_thread.is_alive()
        process.wait(timeout=2)
        assert process.stdout.read() == "a界🙂é\n"
        assert attempts > len("a界🙂é\n".encode("utf-8"))
    finally:
        running.stop_input.set()
        if process.poll() is None:
            process.kill()
            process.wait()
        running.input_thread.join(timeout=2)
        process.stdin.close()
        process.stdout.close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY descriptors")
@pytest.mark.parametrize("mode", ["pure", "table", "raw"])
def test_forced_rendering_with_redirected_stdin_does_not_keep_open(tmp_path, mode):
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    (definitions / "actions.md").write_text('# action: work\n\n```python\nprint("DONE")\n```\n')
    env = os.environ.copy()
    env.update(TERM="xterm-256color", PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    master, slave = os.openpty()
    def drain():
        try:
            while os.read(master, 65536):
                pass
        except OSError:
            pass
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        result = subprocess.run([sys.executable, "-m", "mudyla", "--without-nix", "--logger", mode,
                                 "--force-interactive", "--it", ":work"], cwd=tmp_path, env=env,
                                stdin=subprocess.DEVNULL, stdout=slave, stderr=subprocess.PIPE, timeout=5)
        assert result.returncode == 0, result.stderr
    finally:
        os.close(slave)
        reader.join(timeout=1)
        os.close(master)


def test_pure_detail_fills_resized_viewport_and_keeps_following(tmp_path):
    output = OutputFormatter(no_color=True, compact=True)
    output._console = Console(file=StringIO(), width=80, height=30, force_terminal=True)
    key = ActionKey.from_name("work")
    logger = ActionLoggerPure([key], output, True)
    logger.mark_running(key, tmp_path)
    (tmp_path / "stdout.log").write_text("\n".join(f"LINE_{n:03d}" for n in range(100)))
    logger.state = ViewState.LOGS_STDOUT
    for height in [30, 5, 24, 5]:
        logger.console._height = height
        stream = StringIO()
        Console(file=stream, width=80, height=height).print(logger._build_renderable())
        rows = stream.getvalue().splitlines()
        assert len(rows) == height
        assert "LINE_099" in stream.getvalue()
        if height == 5:
            assert all("LINE_" in row for row in rows)
    logger._handle_key_scroll("q")
    assert logger.state == ViewState.TABLE
    assert logger._get_content_height() < 5


def test_paused_log_position_survives_rewrap_and_new_output(tmp_path):
    output = OutputFormatter(no_color=True, compact=True)
    output._console = Console(file=StringIO(), width=80, height=24, force_terminal=True)
    key = ActionKey.from_name("work")
    logger = ActionLoggerPure([key], output, True)
    logger.mark_running(key, tmp_path)
    log_path = tmp_path / "stdout.log"
    log_path.write_text("\n".join(f"LOG_{n:03d} " + "0123456789" * 16 for n in range(180)))
    logger.state = ViewState.LOGS_STDOUT
    logger._build_detail_content()
    for _ in range(8):
        logger._handle_key_scroll("half_up")

    def displayed():
        stream = StringIO()
        Console(file=stream, width=logger.console.width).print(logger._build_detail_content())
        return stream.getvalue()

    import re
    anchor = re.findall(r"LOG_\d+", displayed())[0]
    source_position = logger._get_scroll_state(key, ViewState.LOGS_STDOUT).anchor
    for width, height in [(40, 24), (120, 30), (80, 24), (40, 5), (120, 30)]:
        logger.console._width = width
        logger.console._height = height
        frame = displayed()
        if height > 5:
            assert anchor in frame
        assert logger._get_scroll_state(key, ViewState.LOGS_STDOUT).anchor == source_position
        assert not logger._get_scroll_state(key, ViewState.LOGS_STDOUT).at_end
    with log_path.open("a") as stream:
        stream.write("\nNEW_LAST_LINE")
    assert anchor in displayed()
    assert "NEW_LAST_LINE" not in displayed()
    logger._handle_key_scroll("G")
    assert "NEW_LAST_LINE" in displayed()
