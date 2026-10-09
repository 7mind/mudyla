"""Windows job ownership established before the target's first instruction."""

import ctypes
import io
import math
import os
import subprocess
import sys
import threading
from ctypes import wintypes
from dataclasses import dataclass
from functools import cmp_to_key
from pathlib import Path
from typing import NewType, Protocol, cast

from .process import ProcessCleanupFailure, StdinMode

Handle = NewType("Handle", int)
CREATE_SUSPENDED = 0x00000004
CREATE_UNICODE_ENVIRONMENT = 0x00000400
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
STARTF_USESTDHANDLES = 0x00000100
PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
HANDLE_FLAG_INHERIT = 1
DUPLICATE_SAME_ACCESS = 2
STD_INPUT_HANDLE = -10
FILE_TYPE_CHAR = 2
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
INFINITE = 0xFFFFFFFF
TERMINATION_EXIT_CODE = 1
INITIALIZATION_CLEANUP_SECONDS = 1.0


class SecurityAttributes(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("descriptor", wintypes.LPVOID), ("inherit", wintypes.BOOL)]


class StartupInfo(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("reserved", wintypes.LPWSTR), ("desktop", wintypes.LPWSTR),
                ("title", wintypes.LPWSTR), ("x", wintypes.DWORD), ("y", wintypes.DWORD),
                ("x_size", wintypes.DWORD), ("y_size", wintypes.DWORD), ("x_chars", wintypes.DWORD),
                ("y_chars", wintypes.DWORD), ("fill", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("show", wintypes.WORD), ("reserved_size", wintypes.WORD), ("reserved_bytes", wintypes.LPVOID),
                ("stdin", wintypes.HANDLE), ("stdout", wintypes.HANDLE), ("stderr", wintypes.HANDLE)]


class StartupInfoEx(ctypes.Structure):
    _fields_ = [("startup", StartupInfo), ("attributes", wintypes.LPVOID)]


class ProcessInformation(ctypes.Structure):
    _fields_ = [("process", wintypes.HANDLE), ("thread", wintypes.HANDLE),
                ("pid", wintypes.DWORD), ("tid", wintypes.DWORD)]


@dataclass(frozen=True)
class NativeChild:
    process: Handle
    thread: Handle
    pid: int


@dataclass(frozen=True)
class StandardHandles:
    stdin: Handle
    stdout: Handle
    stderr: Handle


class WindowsOperations(Protocol):
    def create_job(self) -> Handle: ...
    def read_pipe(self) -> tuple[io.BufferedReader, Handle]: ...
    def write_pipe(self) -> tuple[io.BufferedWriter, Handle]: ...
    def inherited_stdin(self) -> Handle: ...
    def create_suspended(self, command: list[str], cwd: Path, environment: dict[str, str],
                         standard: StandardHandles) -> NativeChild: ...
    def assign(self, job: Handle, process: Handle) -> None: ...
    def resume(self, thread: Handle) -> None: ...
    def wait(self, process: Handle, timeout: float | None) -> int | None: ...
    def terminate_job(self, job: Handle) -> None: ...
    def terminate_process(self, process: Handle) -> None: ...
    def close_handle(self, handle: Handle) -> None: ...


class WindowsAPI:
    def __init__(self) -> None:
        self.kernel: ctypes.CDLL
        if sys.platform != "win32":
            raise RuntimeError("Windows process APIs require Windows")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.CreatePipe.argtypes = [ctypes.POINTER(wintypes.HANDLE), ctypes.POINTER(wintypes.HANDLE),
                                           ctypes.POINTER(SecurityAttributes), wintypes.DWORD]
        self.kernel.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
        self.kernel.GetCurrentProcess.restype = wintypes.HANDLE
        self.kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        self.kernel.GetStdHandle.restype = wintypes.HANDLE
        self.kernel.GetFileType.argtypes = [wintypes.HANDLE]
        self.kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                                ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.InitializeProcThreadAttributeList.argtypes = [wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                                                  ctypes.POINTER(ctypes.c_size_t)]
        self.kernel.UpdateProcThreadAttribute.argtypes = [wintypes.LPVOID, wintypes.DWORD, ctypes.c_size_t,
                                                          wintypes.LPVOID, ctypes.c_size_t, wintypes.LPVOID, wintypes.LPVOID]
        self.kernel.DeleteProcThreadAttributeList.argtypes = [wintypes.LPVOID]
        self.kernel.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.LPVOID, wintypes.LPVOID,
                                               wintypes.BOOL, wintypes.DWORD, wintypes.LPVOID, wintypes.LPCWSTR,
                                               ctypes.POINTER(StartupInfoEx), ctypes.POINTER(ProcessInformation)]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        self.kernel.ResumeThread.restype = wintypes.DWORD
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CompareStringOrdinal.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.LPCWSTR, ctypes.c_int, wintypes.BOOL]

    @staticmethod
    def _error() -> OSError:
        if sys.platform == "win32":
            return ctypes.WinError()
        raise RuntimeError("Windows process APIs require Windows")

    def create_job(self) -> Handle:
        handle = self.kernel.CreateJobObjectW(None, None)
        if not handle:
            raise self._error()
        return Handle(int(handle))

    def _pipe(self) -> tuple[Handle, Handle]:
        read, write = wintypes.HANDLE(), wintypes.HANDLE()
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), None, True)
        if not self.kernel.CreatePipe(ctypes.byref(read), ctypes.byref(write), ctypes.byref(attributes), 0):
            raise self._error()
        assert read.value is not None and write.value is not None
        return Handle(int(read.value)), Handle(int(write.value))

    def _stream(self, parent: Handle, child: Handle, mode: str) -> io.BufferedIOBase:
        if sys.platform != "win32":
            raise RuntimeError("Windows process APIs require Windows")
        import msvcrt
        transferred = False
        try:
            if not self.kernel.SetHandleInformation(parent, HANDLE_FLAG_INHERIT, 0):
                raise self._error()
            descriptor = msvcrt.open_osfhandle(parent, os.O_BINARY | (os.O_RDONLY if mode == "rb" else os.O_WRONLY))
            transferred = True
            try:
                return cast(io.BufferedIOBase, os.fdopen(descriptor, mode))
            except BaseException:
                os.close(descriptor)
                raise
        except BaseException:
            if not transferred:
                self.close_handle(parent)
            self.close_handle(child)
            raise

    def read_pipe(self) -> tuple[io.BufferedReader, Handle]:
        read, write = self._pipe()
        return cast(io.BufferedReader, self._stream(read, write, "rb")), write

    def write_pipe(self) -> tuple[io.BufferedWriter, Handle]:
        read, write = self._pipe()
        return cast(io.BufferedWriter, self._stream(write, read, "wb")), read

    def inherited_stdin(self) -> Handle:
        source = self.kernel.GetStdHandle(STD_INPUT_HANDLE)
        if not source or source == ctypes.c_void_p(-1).value:
            read, write = self._pipe()
            self.close_handle(write)
            return read
        current = self.kernel.GetCurrentProcess()
        duplicated = wintypes.HANDLE()
        if not self.kernel.DuplicateHandle(current, source, current, ctypes.byref(duplicated), 0, True, DUPLICATE_SAME_ACCESS):
            raise self._error()
        assert duplicated.value is not None
        return Handle(int(duplicated.value))

    def _compare_keys(self, first: str, second: str) -> int:
        result = int(self.kernel.CompareStringOrdinal(first, -1, second, -1, True))
        if not result:
            raise self._error()
        return result - 2

    def environment_block(self, environment: dict[str, str]) -> str:
        keys = sorted(environment, key=cmp_to_key(self._compare_keys))
        unique = [key for index, key in enumerate(keys)
                  if index == len(keys) - 1 or self._compare_keys(key, keys[index + 1]) != 0]
        entries = []
        for key in unique:
            value = environment[key]
            if "\0" in key or "\0" in value:
                raise ValueError("embedded null character")
            if not key or "=" in key[1:]:
                raise ValueError("illegal environment variable name")
            entries.append(key + "=" + value)
        return "\0".join(entries) + "\0\0"

    def create_suspended(self, command: list[str], cwd: Path, environment: dict[str, str],
                         standard: StandardHandles) -> NativeChild:
        handles = [handle for handle in (standard.stdin, standard.stdout, standard.stderr)
                   if not (handle & 3 == 3 and self.kernel.GetFileType(handle) == FILE_TYPE_CHAR)]
        inherited = (wintypes.HANDLE * len(handles))(*handles)
        size = ctypes.c_size_t()
        self.kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        attributes = ctypes.create_string_buffer(size.value)
        if not self.kernel.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)):
            raise self._error()
        try:
            if not self.kernel.UpdateProcThreadAttribute(attributes, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                                        inherited, ctypes.sizeof(inherited), None, None):
                raise self._error()
            startup = StartupInfoEx()
            startup.startup.cb = ctypes.sizeof(StartupInfoEx)
            startup.startup.flags = STARTF_USESTDHANDLES
            startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = standard.stdin, standard.stdout, standard.stderr
            startup.attributes = ctypes.cast(attributes, wintypes.LPVOID)
            information = ProcessInformation()
            arguments = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
            variables = ctypes.create_unicode_buffer(self.environment_block(environment))
            flags = CREATE_SUSPENDED | EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT
            if not self.kernel.CreateProcessW(None, arguments, None, None, True, flags, variables, str(cwd),
                                               ctypes.byref(startup), ctypes.byref(information)):
                raise self._error()
            return NativeChild(Handle(int(information.process)), Handle(int(information.thread)), int(information.pid))
        finally:
            self.kernel.DeleteProcThreadAttributeList(attributes)

    def assign(self, job: Handle, process: Handle) -> None:
        if not self.kernel.AssignProcessToJobObject(job, process):
            raise self._error()

    def resume(self, thread: Handle) -> None:
        previous = self.kernel.ResumeThread(thread)
        if previous == INFINITE:
            raise self._error()
        if previous != 1:
            raise RuntimeError(f"Initial thread suspension count was {previous}, expected 1")

    def wait(self, process: Handle, timeout: float | None) -> int | None:
        milliseconds = INFINITE if timeout is None else min(INFINITE - 1, max(0, math.ceil(timeout * 1000)))
        result = self.kernel.WaitForSingleObject(process, milliseconds)
        if result == WAIT_TIMEOUT:
            return None
        if result != WAIT_OBJECT_0:
            raise self._error()
        code = wintypes.DWORD()
        if not self.kernel.GetExitCodeProcess(process, ctypes.byref(code)):
            raise self._error()
        return int(code.value)

    def terminate_job(self, job: Handle) -> None:
        if not self.kernel.TerminateJobObject(job, TERMINATION_EXIT_CODE):
            raise self._error()

    def terminate_process(self, process: Handle) -> None:
        if not self.kernel.TerminateProcess(process, TERMINATION_EXIT_CODE):
            raise self._error()

    def close_handle(self, handle: Handle) -> None:
        if not self.kernel.CloseHandle(handle):
            raise self._error()


class WindowsProcess:
    def __init__(self, api: WindowsOperations, command: list[str], child: NativeChild, job: Handle,
                 stdin: io.BufferedWriter | None, stdout: io.BufferedReader, stderr: io.BufferedReader) -> None:
        self.api = api
        self.command = command
        self.pid = child.pid
        self.handle = child.process
        self.job = job
        self.stdin, self.stdout, self.stderr = stdin, stdout, stderr
        self.returncode: int | None = None
        self.closed = False
        self.lock = threading.RLock()

    def poll(self) -> int | None:
        with self.lock:
            if self.returncode is None and not self.closed:
                self.returncode = self.api.wait(self.handle, 0)
            return self.returncode

    def wait(self, timeout: float | None) -> int:
        if self.returncode is not None:
            return self.returncode
        assert not self.closed, "Wait after process close"
        result = self.api.wait(self.handle, timeout)
        if result is None:
            assert timeout is not None, "Unbounded native wait returned a timeout"
            raise subprocess.TimeoutExpired(self.command, timeout)
        self.returncode = result
        return result

    def terminate_tree(self) -> None:
        with self.lock:
            if not self.closed:
                self.api.terminate_job(self.job)

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
            failures: list[BaseException] = []
            for stream in (self.stdin, self.stdout, self.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except BaseException as error:
                        failures.append(error)
            for handle in (self.handle, self.job):
                try:
                    self.api.close_handle(handle)
                except BaseException as error:
                    failures.append(error)
            if failures:
                raise ProcessCleanupFailure("Windows process resources could not be released") from failures[0]


class WindowsProcessFactory:
    def __init__(self, api: WindowsOperations) -> None:
        self.api = api

    def start(self, command: list[str], *, cwd: Path, environment: dict[str, str],
              stdin_mode: StdinMode) -> WindowsProcess:
        handles: list[Handle] = []
        streams: list[io.BufferedIOBase] = []
        child: NativeChild | None = None
        assigned = False
        try:
            job = self.api.create_job()
            handles.append(job)
            stdin: io.BufferedWriter | None = None
            if stdin_mode == StdinMode.PIPE:
                stdin, child_stdin = self.api.write_pipe()
                streams.append(stdin)
            else:
                child_stdin = self.api.inherited_stdin()
            handles.append(child_stdin)
            stdout, child_stdout = self.api.read_pipe()
            streams.append(stdout)
            handles.append(child_stdout)
            stderr, child_stderr = self.api.read_pipe()
            streams.append(stderr)
            handles.append(child_stderr)
            child = self.api.create_suspended(command, cwd, environment, StandardHandles(child_stdin, child_stdout, child_stderr))
            handles.extend([child.process, child.thread])
            self.api.assign(job, child.process)
            assigned = True
            process = WindowsProcess(self.api, command, child, job, stdin, stdout, stderr)
            for handle in (child_stdin, child_stdout, child_stderr):
                self.api.close_handle(handle)
                handles.remove(handle)
            self.api.resume(child.thread)
            self.api.close_handle(child.thread)
            return process
        except BaseException as error:
            failures: list[BaseException] = []
            if child is not None:
                try:
                    if assigned:
                        self.api.terminate_job(job)
                    else:
                        self.api.terminate_process(child.process)
                    if self.api.wait(child.process, INITIALIZATION_CLEANUP_SECONDS) is None:
                        raise ProcessCleanupFailure("Suspended child did not exit during initialization rollback")
                except BaseException as cleanup_error:
                    failures.append(cleanup_error)
            for stream in streams:
                try:
                    stream.close()
                except BaseException as cleanup_error:
                    failures.append(cleanup_error)
            for handle in reversed(handles):
                try:
                    self.api.close_handle(handle)
                except BaseException as cleanup_error:
                    failures.append(cleanup_error)
            if failures:
                failure = ProcessCleanupFailure("Windows process initialization rollback failed")
                for cleanup_failure in failures:
                    failure.add_note(str(cleanup_failure))
                raise failure from error
            raise
