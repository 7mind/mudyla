"""Foreground preservation and proven terminal ownership for verbose prefixes."""

from io import StringIO
import ctypes
from ctypes import wintypes
import os
import sys
from types import SimpleNamespace

import pytest
from rich.console import Console
from rich.color import Color
from rich.text import Text

from mudyla.dag.context import ContextId
from mudyla.dag.graph import ActionId, ActionKey
from mudyla.logging.action_logger_verbose import ActionLoggerVerbose
from mudyla.logging.formatters import OutputFormatter
from mudyla.logging import terminal_output
from mudyla.logging.terminal_output import StreamState, same_terminal


class TerminalCapture(StringIO):
    def isatty(self):
        return True


def make_logger(monkeypatch, *, shared=True, no_color=False, term="xterm-256color", legacy=False):
    stdout = TerminalCapture()
    stderr = stdout if shared else TerminalCapture()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setenv("TERM", term)
    output = OutputFormatter(no_color=no_color, compact=True, console=Console(file=stdout, force_terminal=True, color_system="truecolor", no_color=no_color,
                              legacy_windows=legacy))
    output._stderr_console = Console(file=stderr, force_terminal=True, color_system="truecolor", no_color=no_color,
                                     legacy_windows=legacy)
    keys = [ActionKey(ActionId(name), ContextId.empty()) for name in ["first", "second"]]
    return ActionLoggerVerbose(keys, output, parallel=True), keys, stdout, stderr


@pytest.mark.parametrize("parameters,foreground", [
    ("", "39"), ("31", "31"), ("94", "94"), ("31;39", "39"), ("31;0", "39"),
    ("31;;32", "32"), ("31;1;3;4;44", "31"), ("38;5;202", "38;5;202"),
    ("38:5:202", "38:5:202"), ("38;2;11;22;33", "38;2;11;22;33"),
    ("38:2:11:22:33", "38:2:11:22:33"), ("38:2::11:22:33", "38:2::11:22:33"),
    ("38:2:0:11:22:33", "38:2:0:11:22:33"), ("31;48;2;30;31;32", "31"),
    ("31;58;2;39;0;37", "31"), ("31;48:2::30:31:32;58:5:39", "31"),
    ("38;5;999", None), ("38;2;1", None), ("38:2:1:11:22:33", None),
    ("38:2::11:22:33:44", None), ("38;7", None), ("38;5;999;34", "34"),
])
def test_split_sgr_prefix_restores_effective_foreground(monkeypatch, parameters, foreground):
    sequence = "\x1b[" + parameters + "m"
    for split in range(len(sequence) + 1):
        logger, keys, stdout, _ = make_logger(monkeypatch)
        logger.write_output(keys[0], sequence[:split], "stdout")
        assert stdout.getvalue() == sequence[:split]
        logger.write_output(keys[0], sequence[split:], "stdout")
        assert stdout.getvalue() == sequence
        logger.write_output(keys[0], "PAYLOAD", "stdout")
        added = stdout.getvalue()[len(sequence):]
        if foreground is None:
            assert added == "first@global: PAYLOAD"
        else:
            assert added.endswith("\x1b[" + foreground + "mPAYLOAD")
            if ":" not in foreground:
                text = Text.from_ansi(added)
                expected = Text.from_ansi("\x1b[" + foreground + "mPAYLOAD")
                assert text.get_style_at_offset(logger._output.console, text.plain.index("PAYLOAD")).color == expected.get_style_at_offset(logger._output.console, 0).color
        assert "\x1b[0m" not in added and "\x1b[22m" not in added


def test_prefix_preserves_child_attributes_and_cross_channel_foreground(monkeypatch):
    logger, keys, stdout, _ = make_logger(monkeypatch)
    child_style = "\x1b[1;2;3;4;7;8;31;44m"
    logger.write_output(keys[0], child_style, "stderr")
    logger.write_output(keys[1], "SECOND\n", "stdout")
    logger.write_output(keys[0], "\x1b[94mFIRST", "stderr")
    actual = Text.from_ansi(stdout.getvalue())
    expected = Text.from_ansi(child_style + "SECOND\n\x1b[94mFIRST")
    for word in ["SECOND", "FIRST"]:
        assert actual.get_style_at_offset(logger._output.console, actual.plain.index(word)) == expected.get_style_at_offset(logger._output.console, expected.plain.index(word))
    assert "\x1b[0m" not in stdout.getvalue()


def test_marker_resets_only_its_destination_foreground(monkeypatch):
    logger, keys, stdout, _ = make_logger(monkeypatch)
    logger.write_output(keys[0], "\x1b[31mBEFORE", "stderr")
    logger.begin_action(keys[1], ["python3", "script.py"])
    logger.write_output(keys[0], "AFTER", "stdout")
    actual = Text.from_ansi(stdout.getvalue())
    assert actual.get_style_at_offset(logger._output.console, actual.plain.index("AFTER")).color == Color.default()


@pytest.mark.parametrize("options", [{"shared": False}, {"no_color": True}, {"term": "dumb"}, {"legacy": True}])
def test_uncertain_or_disabled_terminal_color_adds_no_ansi(monkeypatch, options):
    logger, keys, stdout, _ = make_logger(monkeypatch, **options)
    logger.write_output(keys[0], "PAYLOAD", "stdout")
    assert stdout.getvalue() == "first@global: PAYLOAD"


def test_cancellation_keeps_effective_foreground_and_parser_storage_bounded():
    state = StreamState()
    for char in "\x1b[31m\x1b[38;2;1;2\x18":
        state.consume(char)
    assert state.foreground == "31" and state.control == "text"
    for char in "\x1b[38;5;" + "9" * 100_000 + ":" * 100_000 + "m":
        state.consume(char)
    assert state.foreground is None
    assert len(state._sgr._group) <= 6 and len(state._sgr._operands) <= 4
    for char in "\x1b[39m":
        state.consume(char)
    assert state.foreground == "39"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptors")
def test_terminal_identity_distinguishes_shared_and_separate_ptys():
    import pty
    first_master, first_slave = pty.openpty()
    second_master, second_slave = pty.openpty()
    try:
        with os.fdopen(os.dup(first_slave), "w") as first, os.fdopen(os.dup(first_slave), "w") as duplicate, os.fdopen(os.dup(second_slave), "w") as second:
            assert same_terminal(first, duplicate)
            assert not same_terminal(first, second)
    finally:
        for descriptor in [first_master, first_slave, second_master, second_slave]:
            os.close(descriptor)


def test_windows_handle_identity_distinguishes_shared_and_separate_objects(monkeypatch):
    class Handle(TerminalCapture):
        def __init__(self, descriptor):
            super().__init__()
            self.descriptor = descriptor

        def fileno(self):
            return self.descriptor

    monkeypatch.setattr(terminal_output, "sys", SimpleNamespace(platform="win32"))
    handles = {1: 2**40 + 100, 2: 2**40 + 101, 3: 2**40 + 200}
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=handles.__getitem__))

    def compare(first, second):
        return first in {handles[1], handles[2]} and second in {handles[1], handles[2]}

    def load(name):
        assert name == "kernelbase"
        return SimpleNamespace(CompareObjectHandles=compare)

    monkeypatch.setattr(ctypes, "WinDLL", load, raising=False)
    assert same_terminal(Handle(1), Handle(2))
    assert not same_terminal(Handle(1), Handle(3))
    assert compare.argtypes == [wintypes.HANDLE, wintypes.HANDLE]
    assert compare.restype == wintypes.BOOL
