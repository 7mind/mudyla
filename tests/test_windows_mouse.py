"""Windows console record layout, event ownership and mode restoration."""

from tests.logger_fixtures import prepared_logger

import ctypes
from io import StringIO
import threading
from types import SimpleNamespace
import pytest

from mudyla.logging.windows_mouse import InputRecord, WindowsMouseInput
from mudyla.logging import windows_mouse
from mudyla.logging import terminal_logger_table as interactive
from mudyla.dag.graph import ActionKey
from mudyla.logging.terminal_logger_pure import PureTerminalLogger
from mudyla.logging.formatters import OutputFormatter
from mudyla.logging.terminal_background import BackgroundProbe


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


@pytest.fixture
def windows_input(monkeypatch):
    api = ConsoleAPI(0x267, [])
    chars = list("input")
    logger = prepared_logger(interactive.TableTerminalLogger, [])
    logger._input_enabled = True
    logger._background_probe = BackgroundProbe(0)
    monkeypatch.setattr(windows_mouse, "_console_api", lambda: api)
    monkeypatch.setattr(interactive.sys, "platform", "win32")
    monkeypatch.setattr(interactive.sys, "stdin", SimpleNamespace(fileno=lambda: 7))
    monkeypatch.setattr(interactive, "msvcrt", SimpleNamespace(
        get_osfhandle=lambda fd: fd, kbhit=lambda: bool(chars), getwch=lambda: chars.pop(0)), raising=False)
    return logger, api, chars


@pytest.mark.parametrize("capture", [False, True])
def test_windows_input_ownership_follows_setup_and_successful_restoration(windows_input, capture):
    logger, api, chars = windows_input
    assert not logger._terminal_active
    logger._setup_terminal()
    assert logger._terminal_active
    logger._windows_mouse.set_capture(capture)
    logger._restore_terminal()
    assert not logger._terminal_active
    assert api.mode == 0x267
    logger._drain_background_reply()
    assert chars == list("input")
    assert logger._pending_windows_input == ""
    logger._restore_terminal()
    assert not logger._terminal_active


def test_windows_input_release_failure_retains_ownership_for_retry(monkeypatch, windows_input):
    logger, api, _ = windows_input
    logger._setup_terminal()
    logger._windows_mouse.set_capture(True)
    api.SetConsoleMode.callback = lambda handle, mode: 0
    monkeypatch.setattr(windows_mouse, "_console_error", lambda: OSError("console release failed"))
    with pytest.raises(OSError, match="console release failed"):
        logger._restore_terminal()
    assert logger._terminal_active
    api.SetConsoleMode.callback = api.set_mode
    logger._restore_terminal()
    assert not logger._terminal_active
    assert api.mode == 0x267


def test_windows_input_setup_failure_does_not_acquire_ownership(monkeypatch, windows_input):
    logger, _, _ = windows_input

    def unavailable():
        raise OSError("console setup failed")

    monkeypatch.setattr(windows_mouse, "_console_api", unavailable)
    with pytest.raises(OSError, match="console setup failed"):
        logger._setup_terminal()
    assert not logger._terminal_active
    assert logger._windows_mouse is None


@pytest.mark.parametrize("first", ["release", "collect"])
def test_windows_release_and_final_collection_serialize_ownership(monkeypatch, windows_input, first):
    logger, api, chars = windows_input
    logger._setup_terminal()
    logger._windows_mouse.set_capture(True)
    entered, resume, attempted = threading.Event(), threading.Event(), threading.Event()
    boundaries, failures = [], []
    native_release = api.SetConsoleMode.callback

    def boundary(name):
        boundaries.append(name)
        if name == first and not entered.is_set():
            entered.set()
            assert resume.wait(timeout=1)

    def release(handle, mode):
        boundary("release")
        return native_release(handle, mode)

    def ready():
        boundary("collect")
        return bool(chars)

    def operate(operation):
        try:
            operation()
        except BaseException as error:
            failures.append(error)

    api.SetConsoleMode.callback = release
    monkeypatch.setattr(interactive.msvcrt, "kbhit", ready)
    operations = {"release": logger._restore_terminal, "collect": logger._drain_background_reply}
    first_worker = threading.Thread(target=operate, args=(operations[first],))
    second_worker = threading.Thread(target=operate, args=(operations["collect" if first == "release" else "release"],))
    original_lock = logger.lock

    class Lock:
        def __enter__(self):
            if threading.current_thread() is second_worker:
                attempted.set()
            return original_lock.__enter__()

        def __exit__(self, *args):
            return original_lock.__exit__(*args)

    logger.lock = Lock()
    try:
        first_worker.start()
        assert entered.wait(timeout=.5)
        second_worker.start()
        assert attempted.wait(timeout=.5)
        assert boundaries == [first]
        assert logger._terminal_active
    finally:
        resume.set()
        first_worker.join(timeout=1)
        if second_worker.ident is not None:
            second_worker.join(timeout=1)
    assert not first_worker.is_alive() and not second_worker.is_alive()
    assert not failures
    assert not logger._terminal_active
    if first == "release":
        assert boundaries == ["release"]
        assert chars == list("input")
        assert logger._pending_windows_input == ""
    else:
        assert boundaries[-1] == "release"
        assert chars == []
        assert logger._pending_windows_input == "input"
