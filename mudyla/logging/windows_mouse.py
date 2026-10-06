"""Native console wheel events alongside the existing Windows keyboard reader."""

import ctypes
import sys
from typing import Optional


def _console_api() -> ctypes.CDLL:
    if sys.platform == "win32":
        return ctypes.WinDLL("kernel32", use_last_error=True)
    raise RuntimeError("Native console input requires Windows")


def _console_error() -> OSError:
    if sys.platform == "win32":
        return ctypes.WinError()
    return OSError("Native console input requires Windows")


class KeyEvent(ctypes.Structure):
    _fields_ = [("down", ctypes.c_int32), ("repeat", ctypes.c_uint16),
                ("key", ctypes.c_uint16), ("scan", ctypes.c_uint16),
                ("char", ctypes.c_uint16), ("control", ctypes.c_uint32)]


class MouseEvent(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int16), ("y", ctypes.c_int16),
                ("buttons", ctypes.c_uint32), ("control", ctypes.c_uint32),
                ("flags", ctypes.c_uint32)]


class InputEvent(ctypes.Union):
    _fields_ = [("key", KeyEvent), ("mouse", MouseEvent)]


class InputRecord(ctypes.Structure):
    _fields_ = [("kind", ctypes.c_uint16), ("event", InputEvent)]


class WindowsMouseInput:
    """Capture wheel records without consuming key-down events needed by getwch."""

    ENABLE_MOUSE_INPUT = 0x0010
    ENABLE_QUICK_EDIT_MODE = 0x0040
    ENABLE_EXTENDED_FLAGS = 0x0080
    ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
    KEY_EVENT = 1
    MOUSE_EVENT = 2
    MOUSE_WHEELED = 4
    MAX_IGNORED_EVENTS = 32

    def __init__(self, handle: int) -> None:
        self._handle = ctypes.c_void_p(handle)
        api = _console_api()
        self._get_mode = api.GetConsoleMode
        self._get_mode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        self._set_mode = api.SetConsoleMode
        self._set_mode.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self._peek = api.PeekConsoleInputW
        self._read = api.ReadConsoleInputW
        for function in (self._peek, self._read):
            function.argtypes = [ctypes.c_void_p, ctypes.POINTER(InputRecord), ctypes.c_uint32,
                                 ctypes.POINTER(ctypes.c_uint32)]
        self._saved_mode: Optional[int] = None

    def set_capture(self, enabled: bool) -> None:
        if enabled == (self._saved_mode is not None):
            return
        if enabled:
            mode = ctypes.c_uint32()
            if not self._get_mode(self._handle, ctypes.byref(mode)):
                raise _console_error()
            changed = ((mode.value | self.ENABLE_MOUSE_INPUT | self.ENABLE_EXTENDED_FLAGS)
                       & ~(self.ENABLE_QUICK_EDIT_MODE | self.ENABLE_VIRTUAL_TERMINAL_INPUT))
            if not self._set_mode(self._handle, changed):
                raise _console_error()
            self._saved_mode = mode.value
        else:
            if not self._set_mode(self._handle, self._saved_mode):
                raise _console_error()
            self._saved_mode = None

    def read(self) -> Optional[str]:
        if self._saved_mode is None:
            return None
        for _ in range(self.MAX_IGNORED_EVENTS):
            record = InputRecord()
            count = ctypes.c_uint32()
            if not self._peek(self._handle, ctypes.byref(record), 1, ctypes.byref(count)):
                raise _console_error()
            if not count.value:
                return None
            if record.kind == self.KEY_EVENT and record.event.key.down:
                return None
            if not self._read(self._handle, ctypes.byref(record), 1, ctypes.byref(count)):
                raise _console_error()
            if record.kind == self.MOUSE_EVENT and record.event.mouse.flags == self.MOUSE_WHEELED:
                delta = ctypes.c_int16(record.event.mouse.buttons >> 16).value
                return "wheel_up" if delta > 0 else "wheel_down"
        return ""
