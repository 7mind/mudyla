"""Owned child lifetime; capture and execution policies remain with callers."""

import io
import os
import signal
import subprocess
import sys
import threading
from enum import Enum
from pathlib import Path
from typing import Protocol, cast


class StdinMode(Enum):
    INHERIT = "inherit"
    PIPE = "pipe"


class ProcessCleanupFailure(RuntimeError):
    pass


class Process(Protocol):
    @property
    def pid(self) -> int: ...
    @property
    def stdin(self) -> io.BufferedWriter | None: ...
    @property
    def stdout(self) -> io.BufferedReader: ...
    @property
    def stderr(self) -> io.BufferedReader: ...
    @property
    def returncode(self) -> int | None: ...
    def poll(self) -> int | None: ...
    def wait(self, timeout: float | None) -> int: ...
    def terminate_tree(self) -> None: ...
    def close(self) -> None: ...


class ProcessFactory(Protocol):
    def start(self, command: list[str], *, cwd: Path, environment: dict[str, str],
              stdin_mode: StdinMode) -> Process: ...


class PosixProcess:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        assert process.stdout is not None and process.stderr is not None
        self.process = process
        self.stdin = cast(io.BufferedWriter | None, process.stdin)
        self.stdout = cast(io.BufferedReader, process.stdout)
        self.stderr = cast(io.BufferedReader, process.stderr)
        self.closed = False
        self.lock = threading.RLock()

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    def poll(self) -> int | None:
        return self.process.poll()

    def wait(self, timeout: float | None) -> int:
        return self.process.wait(timeout=timeout)

    def terminate_tree(self) -> None:
        with self.lock:
            if not self.closed:
                if sys.platform == "win32":
                    raise RuntimeError("POSIX process APIs require a POSIX platform")
                try:
                    os.killpg(self.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

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
            if failures:
                raise ProcessCleanupFailure("POSIX process resources could not be released") from failures[0]


class PosixProcessFactory:
    def start(self, command: list[str], *, cwd: Path, environment: dict[str, str],
              stdin_mode: StdinMode) -> PosixProcess:
        return PosixProcess(subprocess.Popen(command, cwd=cwd, env=environment,
                            stdin=subprocess.PIPE if stdin_mode == StdinMode.PIPE else None,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True))


def process_factory() -> ProcessFactory:
    if sys.platform == "win32":
        from .process_windows import WindowsAPI, WindowsProcessFactory
        return WindowsProcessFactory(WindowsAPI())
    return PosixProcessFactory()
