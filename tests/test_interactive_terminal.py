"""Rendering and native terminal checks for the existing mdl action viewer."""

from io import BytesIO, StringIO, TextIOWrapper
import os
from pathlib import Path
import re
import sys
import time

import pytest
from rich.console import Console

from mudyla.dag.context import ContextId
from mudyla.dag.graph import ActionId, ActionKey
from mudyla.logging.action_logger_table import ActionLoggerTable, ViewState


def action_keys(count: int) -> list[ActionKey]:
    return [ActionKey(ActionId(f"task{index:02d}"), ContextId(())) for index in range(count)]


@pytest.mark.parametrize("width", [40, 120, 160])
@pytest.mark.parametrize("view", list(ViewState))
def test_inline_table_footer_advertises_keyboard_controls_only(monkeypatch, width, view):
    logger = ActionLoggerTable(action_keys(1))
    logger.state = view
    monkeypatch.setattr(logger, "_get_terminal_size", lambda: (width, 24))
    footer = logger._build_footer().plain
    assert "Wheel" not in footer
    assert "j/k" in footer
    if width >= 120:
        assert "PgUp/PgDn" in footer


@pytest.mark.parametrize("keep_running", [False, True])
def test_pure_overview_footer_matches_mouse_ownership(keep_running):
    from mudyla.logging.action_logger_pure import ActionLoggerPure
    from mudyla.logging.formatters import OutputFormatter

    output = OutputFormatter(no_color=True, compact=True)
    output._console = Console(file=StringIO(), width=160, height=24, force_terminal=True)
    logger = ActionLoggerPure(action_keys(1), output, True, keep_running=keep_running)
    assert ("Wheel/PgUp/PgDn scroll" in logger._build_footer().plain) == keep_running


def rendered(logger: ActionLoggerTable, width: int, height: int) -> str:
    stream = StringIO()
    console = Console(file=stream, width=width, height=height, force_terminal=False)
    console.print(logger._build_renderable())
    return stream.getvalue()


@pytest.mark.parametrize("width,height", [(80, 24), (40, 12), (120, 30)])
def test_large_plan_keeps_selection_and_controls_visible(monkeypatch, width, height):
    logger = ActionLoggerTable(action_keys(60), no_color=True)
    monkeypatch.setattr(logger, "_get_terminal_size", lambda: (width, height))
    logger.selected_index = 45
    frame = rendered(logger, width, height)
    assert len(frame.splitlines()) <= height
    assert "task45" in frame
    assert "q" in frame.splitlines()[-1]


@pytest.mark.parametrize("view", [ViewState.TABLE, ViewState.LOGS_STDOUT])
def test_footer_fits_one_terminal_row(monkeypatch, view):
    logger = ActionLoggerTable(action_keys(1), no_color=True)
    logger.state = view
    monkeypatch.setattr(logger, "_get_terminal_size", lambda: (80, 24))
    stream = StringIO()
    Console(file=stream, width=80).print(logger._build_footer())
    assert len(stream.getvalue().splitlines()) == 1
    assert "q" in stream.getvalue()


@pytest.mark.parametrize("complete,ending", [(False, "q kill"), (True, "q close")])
def test_input_error_footer_matches_overview_quit_behavior(complete, ending):
    logger = ActionLoggerTable(action_keys(1))
    logger.report_input_error(logger.action_keys[0], "Input closed")
    if complete:
        logger.mark_execution_complete()
    assert logger._build_footer().plain.startswith(ending)


@pytest.mark.parametrize("no_color", [False, True])
def test_log_controls_cannot_modify_terminal(tmp_path, monkeypatch, no_color):
    logger = ActionLoggerTable(action_keys(1), no_color=no_color)
    logger.mark_running(logger.action_keys[0], tmp_path)
    logger.state = ViewState.LOGS_STDOUT
    monkeypatch.setattr(logger, "_get_terminal_size", lambda: (80, 24))
    (tmp_path / "stdout.log").write_text("before\x1b[2Jafter\x1b]52;c;VEVTVA==\x07\n", encoding="utf-8")
    frame = rendered(logger, 80, 24)
    assert "\x1b" not in frame
    assert "\x07" not in frame
    assert "beforeafter" in frame


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX terminal input")
def test_coalesced_arrow_keys_are_not_discarded(monkeypatch):
    import tty

    master, slave = os.openpty()
    with os.fdopen(slave, "r", encoding="utf-8") as terminal:
        try:
            tty.setraw(terminal.fileno())
            monkeypatch.setattr(sys, "stdin", terminal)
            logger = ActionLoggerTable(action_keys(3))
            os.write(master, b"\x1b[B\x1b[B")
            assert [logger._read_key_unix(), logger._read_key_unix()] == ["down", "down"]
        finally:
            os.close(master)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX terminal input")
def test_escape_preserves_immediately_following_input(monkeypatch):
    import tty

    master, slave = os.openpty()
    with os.fdopen(slave, "r", encoding="utf-8") as terminal:
        try:
            tty.setraw(terminal.fileno())
            monkeypatch.setattr(sys, "stdin", terminal)
            logger = ActionLoggerTable(action_keys(1))
            os.write(master, b"\x1bq")
            assert logger._read_key_unix() == "escape"
            assert logger._read_key_unix() == "q"
        finally:
            os.close(master)


@pytest.mark.parametrize("view,filename,content", [
    (ViewState.SOURCE, "script.py", "\n".join(f'print("line {i + 1:03d}")' for i in range(100))),
    (ViewState.META, "meta.json", "[" + ",".join(str(i) for i in range(100)) + "]"),
    (ViewState.OUTPUT, "output.json", "[" + ",".join(str(i) for i in range(100)) + "]"),
], ids=["source", "metadata", "output"])
def test_highlighted_line_numbers_match_scroll_position(tmp_path, monkeypatch, view, filename, content):
    logger = ActionLoggerTable(action_keys(1))
    logger.mark_running(logger.action_keys[0], tmp_path)
    logger.state = view
    monkeypatch.setattr(logger, "_get_terminal_size", lambda: (80, 24))
    (tmp_path / filename).write_text(content, encoding="utf-8")
    stream = StringIO()
    Console(file=stream, width=76).print(logger._build_detail_content())
    numbers = [int(match.group(1)) for line in stream.getvalue().splitlines()
               if (match := re.match(r"\s*(\d+)\s", line))]
    scroll = logger._get_scroll_state(logger.action_keys[0], view)
    assert numbers[0] == scroll.offset + 1
    assert numbers[-1] == scroll.total_lines


@pytest.mark.parametrize("encoding", ["ascii", "cp1252", "utf-8"])
@pytest.mark.parametrize("no_color", [False, True])
@pytest.mark.parametrize("view", [ViewState.TABLE, ViewState.LOGS_STDOUT])
def test_view_uses_output_encoding(tmp_path, monkeypatch, encoding, no_color, view):
    key = ActionKey(ActionId("build-é-構築"), ContextId(()))
    logger = ActionLoggerTable([key], no_color=no_color)
    logger.mark_running(key, tmp_path)
    logger.state = view
    (tmp_path / "stdout.log").write_text("résultat 構築\n", encoding="utf-8")
    monkeypatch.setattr(logger, "_get_terminal_size", lambda: (80, 24))
    with TextIOWrapper(BytesIO(), encoding=encoding) as stream:
        logger.console = Console(file=stream, width=80, height=24, force_terminal=True, no_color=no_color)
        logger.console.print(logger._build_renderable())
        stream.flush()


@pytest.mark.parametrize("sequence,expected", [
    (b"\xe0H", "up"), (b"\xe0P", "down"), (b"\x00I", "page_up"), (b"\x00Q", "page_down"),
    (b"\x00G", "top"), (b"\x00O", "bottom"), (b"q", "q"), (b"\r", "enter"),
    (b"m", "m"), (b"e", "e"), (b"o", "o"), (b"s", "s"), (b"g", "g"), (b"G", "G"),
])
def test_windows_console_key_sequences(monkeypatch, sequence, expected):
    from types import SimpleNamespace
    from mudyla.logging import action_logger_table as module

    logger = ActionLoggerTable(action_keys(1))
    keys = list(sequence)
    terminal = SimpleNamespace(kbhit=lambda: bool(keys), getwch=lambda: chr(keys.pop(0)))
    monkeypatch.setattr(module, "msvcrt", terminal, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    assert logger._read_key_windows() == expected


def test_windows_input_decoder_preserves_unicode_and_edit_keys(monkeypatch):
    from types import SimpleNamespace
    from mudyla.logging import action_logger_table as module

    logger = ActionLoggerTable(action_keys(1))
    logger._input_action = logger.action_keys[0]
    keys = list("é界\ud83d\ude42\b\r\x04\x1b")
    terminal = SimpleNamespace(kbhit=lambda: bool(keys), getwch=lambda: keys.pop(0))
    monkeypatch.setattr(module, "msvcrt", terminal, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    assert [logger._read_key_windows() for _ in range(8)] == [
        "é", "界", "", "🙂", "backspace", "enter", "eof", "escape"]


def test_stop_before_start_is_idempotent():
    logger = ActionLoggerTable(action_keys(0))
    logger.stop()
    logger.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="Native POSIX terminal restoration")
@pytest.mark.parametrize("stage", ["start", "stop"])
@pytest.mark.parametrize("mode", ["table", "pure"])
def test_render_error_restores_screen_cursor_and_terminal(monkeypatch, stage, mode):
    import termios
    from rich.console import Group
    from rich.text import Text
    from mudyla.logging.action_logger_pure import ActionLoggerPure
    from mudyla.logging.formatters import OutputFormatter

    master, slave = os.openpty()
    with os.fdopen(slave, "r", encoding="utf-8") as terminal, TextIOWrapper(BytesIO(), encoding="ascii") as stream:
        monkeypatch.setattr(sys, "stdin", terminal)
        logger = (ActionLoggerPure(action_keys(1), OutputFormatter(no_color=True, compact=True), True,
                                   force_interactive=True) if mode == "pure" else
                  ActionLoggerTable(action_keys(1), no_color=True))
        logger.console = Console(file=stream, width=80, height=24, force_terminal=True)
        logger.state = ViewState.LOGS_STDOUT
        original = termios.tcgetattr(terminal)
        try:
            if stage == "stop":
                logger.start()
                logger.stop_flag = True
                logger._main_thread.join(timeout=1)
            monkeypatch.setattr(logger, "_build_renderable", lambda: Group(Text("unsupported: \u2026")))
            with pytest.raises(UnicodeEncodeError):
                logger.start() if stage == "start" else logger.stop()
            changed_flags = termios.ICANON | termios.ECHO | termios.ISIG | termios.NOFLSH
            assert termios.tcgetattr(terminal)[3] & changed_flags == original[3] & changed_flags
            output = stream.buffer.getvalue().decode("ascii")
            assert output.count("\x1b[?1049h") == output.count("\x1b[?1049l") == 1
            assert output.count("\x1b[?25l") == output.count("\x1b[?25h") == 1
            assert output.count("\x1b[?1000h") == output.count("\x1b[?1000l") == 1
            assert output.count("\x1b[?1006h") == output.count("\x1b[?1006l") == 1
        finally:
            logger._restore_terminal()
            if logger.live is not None:
                with logger.console.capture():
                    pass
                logger.live.stop()
            os.close(master)
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX SIGINT and process groups")
@pytest.mark.parametrize("mode", ["--seq", "--par"])
@pytest.mark.parametrize("logger_mode", ["pure", "table"])
def test_cli_interrupt_stops_action_tree_and_restores_terminal(tmp_path, mode, logger_mode):
    import signal
    import termios

    pexpect = pytest.importorskip("pexpect")
    (tmp_path / ".git").mkdir()
    definitions = tmp_path / ".mdl" / "defs"
    definitions.mkdir(parents=True)
    pid_path = tmp_path / "action.pid"
    marker = tmp_path / "descendant-survived"
    descendant = f"import time; from pathlib import Path; time.sleep(1); Path({str(marker)!r}).touch()"
    script = ("import os, sys, subprocess, time\nfrom pathlib import Path\n"
              f"subprocess.Popen([sys.executable, '-c', {descendant!r}])\n"
              f"Path({str(pid_path)!r}).write_text(str(os.getpid()))\n"
              "time.sleep(30)\n")
    (definitions / "actions.md").write_text(f"# action: slow\n\n```python\n{script}```\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(TERM="xterm-256color", PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    child = pexpect.spawn(sys.executable, ["-m", "mudyla", "--without-nix", "--logger", logger_mode, mode, ":slow"],
                          cwd=str(tmp_path), env=env, encoding="utf-8", timeout=4, dimensions=(24, 80))
    original_flags = termios.tcgetattr(child.child_fd)[3]
    try:
        child.expect_exact("q kill")
        deadline = time.monotonic() + 3
        while not pid_path.exists() and time.monotonic() < deadline:
            try:
                child.read_nonblocking(65536, timeout=.02)
            except pexpect.TIMEOUT:
                pass
        assert pid_path.exists()
        child.sendcontrol("c")
        child.expect(pexpect.EOF, timeout=3)
        output = child.before
        restored_flags = termios.tcgetattr(child.child_fd)[3]
        restored_mask = termios.ICANON | termios.ECHO | termios.NOFLSH
        assert restored_flags & restored_mask == original_flags & restored_mask
        child.close()
        assert child.exitstatus == 130
        assert "Traceback" not in output
        time.sleep(1.1)
        assert not marker.exists()
    finally:
        if pid_path.exists():
            try:
                os.killpg(int(pid_path.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
        child.close(force=True)
