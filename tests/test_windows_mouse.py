"""Windows console record layout, event ownership and mode restoration."""

from tests.logger_fixtures import prepared_logger

import ctypes
from io import StringIO
from types import SimpleNamespace
import pytest

from mudyla.logging.windows_mouse import InputRecord, WindowsMouseInput
from mudyla.logging import windows_mouse
from mudyla.logging import terminal_logger_table as interactive
from mudyla.dag.graph import ActionKey
from mudyla.logging.terminal_logger_pure import PureTerminalLogger
from mudyla.logging.formatters import OutputFormatter


class Function:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


class ConsoleAPI:
    def __init__(self, mode, records):
        self.mode = mode
        self.records = records
        self.GetConsoleMode = Function(self.get_mode)
        self.SetConsoleMode = Function(self.set_mode)
        self.PeekConsoleInputW = Function(self.peek)
        self.ReadConsoleInputW = Function(self.read)

    def get_mode(self, handle, output):
        output._obj.value = self.mode
        return 1

    def set_mode(self, handle, mode):
        self.mode = mode
        return 1

    def peek(self, handle, output, size, count):
        count._obj.value = min(size, len(self.records))
        if self.records:
            ctypes.memmove(output, ctypes.byref(self.records[0]), ctypes.sizeof(InputRecord))
        return 1

    def read(self, handle, output, size, count):
        self.peek(handle, output, size, count)
        if self.records:
            self.records.pop(0)
        return 1


def mouse_record(delta):
    record = InputRecord()
    record.kind = WindowsMouseInput.MOUSE_EVENT
    record.event.mouse.flags = WindowsMouseInput.MOUSE_WHEELED
    record.event.mouse.buttons = (delta & 0xffff) << 16
    return record


def test_windows_wheel_capture_preserves_keyboard_records_and_original_mode(monkeypatch):
    assert ctypes.sizeof(InputRecord) == 20
    key = InputRecord()
    key.kind = WindowsMouseInput.KEY_EVENT
    key.event.key.down = 1
    key.event.key.char = ord("q")
    key_up = InputRecord()
    key_up.kind = WindowsMouseInput.KEY_EVENT
    original = 0x267
    api = ConsoleAPI(original, [mouse_record(120), key_up, mouse_record(-120), key])
    monkeypatch.setattr(windows_mouse, "_console_api", lambda: api)
    mouse = WindowsMouseInput(7)
    assert mouse.read() is None
    assert len(api.records) == 4
    mouse.set_capture(True)
    assert api.mode & WindowsMouseInput.ENABLE_MOUSE_INPUT
    assert not api.mode & WindowsMouseInput.ENABLE_QUICK_EDIT_MODE
    assert not api.mode & WindowsMouseInput.ENABLE_VIRTUAL_TERMINAL_INPUT
    assert mouse.read() == "wheel_up"
    assert mouse.read() == "wheel_down"
    assert mouse.read() is None
    assert api.records == [key]
    mouse.set_capture(False)
    assert api.mode == original
    mouse.set_capture(False)
    assert api.mode == original


@pytest.mark.parametrize("mode", ["pure", "table"])
@pytest.mark.parametrize("fullscreen", [False, True])
def test_shared_windows_reader_routes_wheel_and_keys_and_restores_mode(monkeypatch, mode, fullscreen):
    api = ConsoleAPI(0x267, [mouse_record(-120)] if fullscreen else [])
    key = InputRecord()
    key.kind = WindowsMouseInput.KEY_EVENT
    key.event.key.down = 1
    key.event.key.char = ord("q")
    api.records.append(key)
    monkeypatch.setattr(windows_mouse, "_console_api", lambda: api)
    keys = [ActionKey.from_name("work")]
    logger = (prepared_logger(PureTerminalLogger, keys, OutputFormatter(no_color=False, compact=True), True, force_interactive=True)
              if mode == "pure" else prepared_logger(interactive.TableTerminalLogger, keys))
    logger.console.file = StringIO()
    logger._input_enabled = True
    monkeypatch.setattr(interactive.sys, "platform", "win32")
    monkeypatch.setattr(interactive.sys, "stdin", SimpleNamespace(fileno=lambda: 7))
    monkeypatch.setattr(interactive, "msvcrt", SimpleNamespace(
        get_osfhandle=lambda fd: fd, kbhit=lambda: bool(api.records),
        getwch=lambda: chr(api.records.pop(0).event.key.char)), raising=False)
    logger._setup_terminal()
    logger._screen_active = fullscreen
    logger._set_mouse_capture(True)
    if fullscreen:
        assert logger._read_key_windows() == "wheel_down"
    else:
        assert api.mode == 0x267
        assert "\x1b[?1000h" not in logger.console.file.getvalue()
    assert logger._read_key_windows() == "q"
    logger._set_mouse_capture(False)
    logger._restore_terminal()
    assert api.mode == 0x267
