"""Inline progress and temporary or persistent fullscreen inspection."""

from io import BytesIO, StringIO, TextIOWrapper
import os
import sys
from types import SimpleNamespace

import pytest
from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

from mudyla.dag.graph import ActionKey
from mudyla.logging.action_logger_pure import ActionLoggerPure
from mudyla.logging.action_logger_table import ActionLoggerTable, InlineDisplay, ViewState
from mudyla.logging.formatters import OutputFormatter
from tests.test_logger_interactions import terminal_project
from tests.terminal_capture import terminal_text


@pytest.mark.parametrize("mode", ["pure", "table"])
@pytest.mark.parametrize("option", [None, "--it", "--interactive", "--force-interactive", "--fullscreen"])
def test_screen_ownership_follows_session_and_temporary_detail(terminal_project, tmp_path, mode, option):
    import pexpect

    release = tmp_path / "release"
    child = terminal_project(mode, '# action: work\n```python\nfrom pathlib import Path\nimport time\n'
                             'print("DETAIL_READY", flush=True)\n'
                             f'while not Path({str(release)!r}).exists(): time.sleep(.01)\n```\n',
                             options=(option,) if option else (), dimensions=(24, 100))
    captured = StringIO()
    fullscreen = option in {"--it", "--interactive", "--fullscreen"}
    child.logfile_read = captured
    child.expect_exact("q kill")
    assert captured.getvalue().count("\x1b[?1049h") == int(fullscreen)
    assert captured.getvalue().count("\x1b[?1000h") == int(fullscreen)
    child.send("l")
    child.expect_exact("DETAIL_READY")
    child.expect_exact("q back")
    assert captured.getvalue().count("\x1b[?1049h") == 1
    child.send("q")
    child.expect_exact("q kill")
    assert captured.getvalue().count("\x1b[?1049l") == int(not fullscreen)
    release.touch()
    if option in {"--it", "--interactive"}:
        child.expect_exact("q close")
        child.send("q")
    child.expect(pexpect.EOF)
    child.close()
    assert child.exitstatus == 0
    for code in ["1049", "1000", "1006", "25"]:
        assert captured.getvalue().count(f"\x1b[?{code}h") == captured.getvalue().count(f"\x1b[?{code}l")


@pytest.mark.parametrize("mode", ["pure", "table"])
@pytest.mark.parametrize("fails", [False, True])
def test_inline_completion_inside_detail_restores_screen_and_exits(terminal_project, tmp_path, mode, fails):
    import pexpect

    release = tmp_path / "release"
    child = terminal_project(mode, '# action: work\n```python\nfrom pathlib import Path\nimport time\n'
                             'print("DETAIL_READY", flush=True)\n'
                             f'while not Path({str(release)!r}).exists(): time.sleep(.01)\n'
                             + ('raise RuntimeError("EXPECTED_FAILURE")\n' if fails else '') + '```\n')
    captured = StringIO()
    child.logfile_read = captured
    child.expect_exact("q kill")
    child.send("l")
    child.expect_exact("DETAIL_READY")
    release.touch()
    child.expect(pexpect.EOF)
    child.close()
    assert child.exitstatus == int(fails)
    assert captured.getvalue().count("\x1b[?1049h") == captured.getvalue().count("\x1b[?1049l") == 1
    final = terminal_text(captured.getvalue().split("\x1b[?1049l", 1)[1]).plain
    assert "Actions:" in final
    assert ("Execution failed!" if fails else "Execution completed successfully!") in final


@pytest.mark.parametrize("width,height", [(120, 40), (60, 14), (80, 18), (40, 12)])
def test_inline_pure_keeps_only_populated_rows_and_omits_printed_preparation(width, height):
    output = OutputFormatter(no_color=True, compact=True, console=Console(file=StringIO(), width=width, height=height, force_terminal=True))
    keys = [ActionKey.from_name("first"), ActionKey.from_name("second")]
    logger = ActionLoggerPure(keys, output, True, run_info=Text("ALREADY_PRINTED_PREPARATION"))
    logger.selected_index = 1
    rows = output.console.render_lines(logger._build_renderable(), pad=False)
    text = "\n".join("".join(segment.text for segment in row) for row in rows)
    assert "ALREADY_PRINTED_PREPARATION" not in text
    assert "second" in text and "q kill" in text
    assert len(rows) <= min(height, 8)


def test_fullscreen_table_can_page_preparation_and_last_action_without_changing_selection(monkeypatch):
    logger = ActionLoggerTable([ActionKey.from_name(f"task-{index:04d}") for index in range(1000)], keep_running=True,
                               console=Console(file=StringIO(), width=80, height=24, force_terminal=True))
    logger._run_info = Text("PREPARATION_MARKER\n" * 40)
    monkeypatch.setattr(logger, "_get_terminal_size", lambda: (80, 24))
    logger.selected_index = 500
    logger._build_renderable()
    logger._handle_key_table("top")
    logger.console.print(logger._build_renderable())
    assert logger.selected_index == 500
    assert "PREPARATION_MARKER" in logger.console.file.getvalue()
    logger._handle_key_table("bottom")
    with logger.console.capture() as capture:
        logger.console.print(logger._build_renderable())
    assert "task-0999" in capture.get()
    assert logger.selected_index == 500


def test_fullscreen_table_keeps_selected_directory_on_narrow_terminal(monkeypatch):
    key = ActionKey.from_name("work")
    logger = ActionLoggerTable([key], keep_running=True, show_dirs=True,
                               console=Console(file=StringIO(), width=80, height=12, force_terminal=True))
    logger.action_dirs_map[logger._action_formatter.format_label_plain(key, True)] = "SELECTED_DIRECTORY"
    monkeypatch.setattr(logger, "_get_terminal_size", lambda: (80, 12))
    rows = logger.console.render_lines(logger._build_renderable(), pad=False)
    text = "\n".join("".join(segment.text for segment in row) for row in rows)
    assert "SELECTED_DIRECTORY" in text
    assert len(rows) <= 12


@pytest.mark.parametrize("width,height", [(40, 12), (80, 18)])
def test_fullscreen_table_resize_keeps_counts_bottom_border_and_controls(monkeypatch, width, height):
    keys = [ActionKey.from_name(name) for name in ["prepare", "cache", "compile-alpha", "compile-beta"]]
    logger = ActionLoggerTable(keys, keep_running=True, show_dirs=True, run_info=Text("PREPARATION\n" * 35),
                               console=Console(file=StringIO(), width=120, height=40, force_terminal=True))
    monkeypatch.setattr(logger, "_get_terminal_size", lambda: tuple(logger.console.size))
    for key in keys[:2]:
        logger.mark_done(key, 0.1)
    for key in keys[2:]:
        logger.mark_running(key)
    logger.selected_index = 3
    logger.action_dirs_map[logger._action_formatter.format_label_plain(keys[3], True)] = "SELECTED_DIRECTORY"
    logger._build_renderable()
    logger.console.size = (width, height)
    rows = logger.console.render_lines(logger._build_renderable(), pad=False)
    text = "\n".join("".join(segment.text for segment in row) for row in rows)
    assert "2 done | 2 running" in text
    assert "╰" in text and "q kill" in text and "SELECTED_DIRECTORY" in text
    assert logger._get_selected_action_key() == keys[3]
    assert len(rows) <= height


def test_fullscreen_table_resize_shows_all_actions_when_the_complete_table_fits(monkeypatch):
    keys = [ActionKey.from_name(name) for name in ["prepare", "cache", "compile-alpha", "compile-beta"]]
    logger = ActionLoggerTable(keys, keep_running=True, show_dirs=True, run_info=Text("PREPARATION\n" * 35),
                               console=Console(file=StringIO(), width=120, height=40, force_terminal=True))
    monkeypatch.setattr(logger, "_get_terminal_size", lambda: tuple(logger.console.size))
    logger.selected_index = 2
    logger._build_renderable()
    for size in [(60, 14), (40, 12), (80, 18), (120, 40)]:
        logger.console.size = size
        rows = logger.console.render_lines(logger._build_renderable(), pad=False)
        text = "\n".join("".join(segment.text for segment in row) for row in rows)
        assert logger._table_window() == (0, 4), text
        assert all(str(key.id) in text for key in keys)
        assert "Actions:" in text and "4 pending" in text and "╰" in text and "q kill" in text
        assert len(rows) <= size[1]


def test_inline_display_preserves_styles_and_unicode_cell_width_without_hard_newlines():
    stream = StringIO()
    console = Console(file=stream, width=10, height=5, force_terminal=True, color_system="truecolor", no_color=False)
    content = Group(Text("界é", style="red"), Text("tail"))
    display = InlineDisplay(content, console)
    display.start()
    display.update(content, refresh=True)
    frame = stream.getvalue()
    assert "\n" not in frame
    assert "界é" + " " * 7 + "tail" in Text.from_ansi(frame).plain
    assert "\x1b[31m" in frame
    assert frame.endswith("\x1b[1G\x1b[1A")
    display.stop()
    display.stop()
    assert stream.getvalue().count("\x1b[?25l") == stream.getvalue().count("\x1b[?25h") == 1


@pytest.mark.parametrize("legacy_windows,dumb_terminal", [(False, False), (True, False), (False, True)])
def test_inline_display_dispatch_preserves_console_capabilities(monkeypatch, legacy_windows, dumb_terminal):
    monkeypatch.setenv("TERM", "dumb" if dumb_terminal else "xterm-256color")
    logger = ActionLoggerTable([ActionKey.from_name("work")],
                               console=Console(file=StringIO(), width=80, height=24, force_terminal=True,
                                               legacy_windows=legacy_windows))
    monkeypatch.setattr(logger, "_get_terminal_size", lambda: (80, 24))
    logger._refresh_display()
    inline = not legacy_windows and not dumb_terminal
    assert isinstance(logger.live, InlineDisplay if inline else Live)
    logger.stop()
    assert ("\x1b[J" in logger.console.file.getvalue()) == inline
    if dumb_terminal:
        assert "\x1b" not in logger.console.file.getvalue()


@pytest.mark.parametrize("mode", ["pure", "table"])
def test_legacy_windows_display_uses_native_cursor_and_line_operations(monkeypatch, mode):
    from collections import namedtuple
    from types import ModuleType
    import rich.console

    events = []

    class LegacyStream(StringIO):
        def fileno(self):
            return 1

        def isatty(self):
            return True

    class LegacyTerm:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            def call(*args):
                events.append((name, args))
            return call

    native = ModuleType("rich._win32_console")
    native.LegacyWindowsTerm = LegacyTerm
    native.WindowsCoordinates = namedtuple("WindowsCoordinates", ["row", "col"])
    monkeypatch.setitem(sys.modules, "rich._win32_console", native)
    monkeypatch.setattr(rich.console, "WINDOWS", True)
    stream = LegacyStream()
    console = Console(file=stream, force_terminal=True, force_interactive=True, legacy_windows=True,
                      no_color=True, width=80, height=24)
    output = OutputFormatter(no_color=True, compact=True, console=console)
    keys = [ActionKey.from_name("LEGACY_VISIBLE")]
    logger = (ActionLoggerPure(keys, output, True) if mode == "pure" else
              ActionLoggerTable(keys, no_color=True, console=console))
    monkeypatch.setattr(logger, "_get_terminal_size", lambda: (80, 24))
    logger._refresh_display()
    assert isinstance(logger.live, Live)
    logger.state = ViewState.META
    logger._refresh_display()
    logger.state = ViewState.TABLE
    logger._refresh_display()
    logger.stop()
    logger.stop()
    names = [name for name, _ in events]
    assert "erase_line" in names
    assert names.count("hide_cursor") == names.count("show_cursor") == 1
    assert any("LEGACY_VISIBLE" in str(args) for _, args in events)
    assert "\x1b" not in stream.getvalue()
    assert not logger._screen_active and not logger._mouse_enabled


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX terminal restoration")
@pytest.mark.parametrize("mode", ["pure", "table"])
@pytest.mark.parametrize("entering", [False, True])
def test_render_failure_during_screen_transition_restores_terminal(monkeypatch, mode, entering):
    import termios

    master, slave = os.openpty()
    with os.fdopen(slave, "r", encoding="utf-8") as terminal, TextIOWrapper(BytesIO(), encoding="ascii") as stream:
        monkeypatch.setattr(sys, "stdin", terminal)
        keys = [ActionKey.from_name("work")]
        console = Console(file=stream, width=80, height=24, force_terminal=True)
        logger = (ActionLoggerPure(keys, OutputFormatter(no_color=True, compact=True, console=console), True,
                                   force_interactive=True) if mode == "pure" else
                  ActionLoggerTable(keys, no_color=True, console=console))
        original = termios.tcgetattr(terminal)
        try:
            logger._setup_terminal()
            logger.state = ViewState.TABLE if entering else ViewState.LOGS_STDOUT
            logger._refresh_display()
            logger.state = ViewState.LOGS_STDOUT if entering else ViewState.TABLE
            monkeypatch.setattr(logger, "_build_renderable", lambda: Group(Text("unsupported: …")))
            monkeypatch.setattr(logger, "_run_main_loop", logger._refresh_display)
            logger._main_loop()
            changed_flags = termios.ICANON | termios.ECHO | termios.ISIG | termios.NOFLSH
            assert termios.tcgetattr(terminal)[3] & changed_flags == original[3] & changed_flags
            with pytest.raises(UnicodeEncodeError):
                logger.stop()
            logger.stop()
            output = stream.buffer.getvalue().decode("ascii")
            for code in ["1049", "1000", "1006", "25"]:
                assert output.count(f"\x1b[?{code}h") == output.count(f"\x1b[?{code}l")
        finally:
            logger._restore_terminal()
            os.close(master)


def test_input_failure_during_background_reply_drain_preserves_render_error_and_cleanup(monkeypatch):
    logger = ActionLoggerTable([ActionKey.from_name("work")])
    original = RuntimeError("render failed")
    restored = []

    def fail_render():
        raise original

    def fail_read():
        raise OSError("terminal input closed")

    logger._background_probe = SimpleNamespace(pending=lambda now: True)
    logger._input_enabled = True
    monkeypatch.setattr(logger, "_run_main_loop", fail_render)
    monkeypatch.setattr(logger, "_read_key", fail_read)
    monkeypatch.setattr(logger, "_restore_terminal", lambda: restored.append(True))
    logger._main_loop()
    assert restored
    with pytest.raises(RuntimeError, match="render failed") as raised:
        logger.stop()
    assert raised.value is original


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX terminal restoration")
@pytest.mark.parametrize("mode", ["pure", "table"])
@pytest.mark.parametrize("stage", ["start", "inline_to_detail", "detail_to_inline"])
def test_partial_live_acquisition_restores_cursor_screen_and_original_error(monkeypatch, mode, stage):
    import termios

    class TransientWriteFailure(StringIO):
        def __init__(self):
            super().__init__()
            self.armed = False
            self.fired = False
            self.last_write = ""
            self.failure = OSError("output flush failed after hiding cursor")

        def write(self, text):
            self.last_write = text
            return super().write(text)

        def flush(self):
            if self.armed and not self.fired and "\x1b[?25l" in self.last_write:
                self.fired = True
                raise self.failure
            return super().flush()

    master, slave = os.openpty()
    with os.fdopen(slave, "r", encoding="utf-8") as terminal:
        monkeypatch.setattr(sys, "stdin", terminal)
        original = termios.tcgetattr(terminal)
        stream = TransientWriteFailure()
        output = OutputFormatter(no_color=True, compact=True, console=Console(file=stream, width=80, height=24, force_terminal=True))
        keys = [ActionKey.from_name("work")]
        logger = (ActionLoggerPure(keys, output, True, keep_running=stage == "start") if mode == "pure" else
                  ActionLoggerTable(keys, no_color=True, keep_running=stage == "start", console=output.console))
        monkeypatch.setattr(logger, "_get_terminal_size", lambda: (80, 24))
        try:
            if stage == "start":
                stream.armed = True
                with pytest.raises(OSError) as raised:
                    logger.start()
            else:
                logger._setup_terminal()
                logger.state = ViewState.TABLE if stage == "inline_to_detail" else ViewState.LOGS_STDOUT
                logger._refresh_display()
                logger.state = ViewState.LOGS_STDOUT if stage == "inline_to_detail" else ViewState.TABLE
                stream.armed = True
                monkeypatch.setattr(logger, "_run_main_loop", logger._refresh_display)
                logger._main_loop()
                with pytest.raises(OSError) as raised:
                    logger.stop()
            assert stream.fired
            assert raised.value is stream.failure
            logger.stop()
            logger.stop()
            changed_flags = termios.ICANON | termios.ECHO | termios.ISIG | termios.NOFLSH
            assert termios.tcgetattr(terminal)[3] & changed_flags == original[3] & changed_flags
            text = stream.getvalue()
            for code in ["1049", "1000", "1006", "25"]:
                assert text.count(f"\x1b[?{code}h") == text.count(f"\x1b[?{code}l"), (code, text)
            assert logger.live is None and not logger._screen_active
        finally:
            logger._restore_terminal()
            os.close(master)
