"""Process ownership is independent of capture, logging and retention policy."""

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from mudyla.executor.process import PosixProcess, ProcessFactory, StdinMode, process_factory
from mudyla.executor.process_windows import Handle, NativeChild, StandardHandles, WindowsAPI, WindowsProcessFactory


class RecordingWindowsAPI:
    def __init__(self, failure: str | None) -> None:
        self.failure = failure
        self.error = OSError("injected native boundary failure")
        self.events: list[str] = []
        self.handles: set[Handle] = set()
        self.streams: list[io.BufferedIOBase] = []
        self.read_count = 0
        self.started = False
        self.returncode: int | None = None
        self.launch: tuple[list[str], Path, dict[str, str], StandardHandles] | None = None

    def event(self, name: str) -> None:
        self.events.append(name)
        if self.failure == name:
            self.failure = None
            raise self.error

    def acquire(self, number: int) -> Handle:
        handle = Handle(number)
        assert handle not in self.handles
        self.handles.add(handle)
        return handle

    def create_job(self) -> Handle:
        self.event("job")
        return self.acquire(1)

    def inherited_stdin(self) -> Handle:
        self.event("stdin")
        return self.acquire(2)

    def write_pipe(self) -> tuple[io.BufferedWriter, Handle]:
        self.event("stdin")
        stream = io.BufferedWriter(io.BytesIO())
        self.streams.append(stream)
        return stream, self.acquire(2)

    def read_pipe(self) -> tuple[io.BufferedReader, Handle]:
        self.read_count += 1
        self.event(f"read{self.read_count}")
        stream = io.BufferedReader(io.BytesIO())
        self.streams.append(stream)
        return stream, self.acquire(2 + self.read_count)

    def create_suspended(self, command: list[str], cwd: Path, environment: dict[str, str],
                         standard: StandardHandles) -> NativeChild:
        self.event("create")
        self.launch = command, cwd, environment, standard
        return NativeChild(self.acquire(10), self.acquire(11), 123)

    def assign(self, job: Handle, process: Handle) -> None:
        assert job == 1 and process == 10
        self.event("assign")

    def resume(self, thread: Handle) -> None:
        assert thread == 11
        self.event("resume")
        self.started = True

    def wait(self, process: Handle, timeout: float | None) -> int | None:
        assert process == 10
        self.event("wait")
        return self.returncode

    def terminate_job(self, job: Handle) -> None:
        assert job == 1
        self.event("terminate_job")
        self.returncode = 1

    def terminate_process(self, process: Handle) -> None:
        assert process == 10
        self.event("terminate_process")
        self.returncode = 1

    def close_handle(self, handle: Handle) -> None:
        self.event(f"close{handle}")
        self.handles.remove(handle)


@pytest.mark.parametrize("stdin_mode", list(StdinMode))
def test_windows_adapter_assigns_before_resume_and_preserves_launch_policy(stdin_mode, tmp_path):
    api = RecordingWindowsAPI(None)
    command, environment = ["python", "a quoted argument"], {"value": "λ", "=C:": "C:\\"}
    process = WindowsProcessFactory(api).start(command, cwd=tmp_path, environment=environment, stdin_mode=stdin_mode)
    assert api.events.index("create") < api.events.index("assign") < api.events.index("resume")
    assert api.launch == (command, tmp_path, environment, StandardHandles(Handle(2), Handle(3), Handle(4)))
    assert api.handles == {Handle(1), Handle(10)}
    assert api.started and (process.stdin is not None) == (stdin_mode == StdinMode.PIPE)
    api.returncode = 0
    assert process.poll() == 0
    process.close()
    process.close()
    process.terminate_tree()
    assert "terminate_job" not in api.events and not api.handles
    assert all(stream.closed for stream in api.streams)


@pytest.mark.parametrize("failure", ["job", "stdin", "read1", "read2", "create", "assign", "close2", "close3", "close4", "resume", "close11"])
def test_windows_adapter_rolls_back_every_acquisition_boundary(failure, tmp_path):
    api = RecordingWindowsAPI(failure)
    with pytest.raises(OSError) as caught:
        WindowsProcessFactory(api).start(["python"], cwd=tmp_path, environment={}, stdin_mode=StdinMode.PIPE)
    assert caught.value is api.error
    assert not api.handles and all(stream.closed for stream in api.streams)
    if "create" in api.events and failure != "create":
        termination = "terminate_process" if failure == "assign" else "terminate_job"
        assert termination in api.events and "wait" in api.events
    if failure != "close11":
        assert not api.started


def test_windows_adapter_terminates_owned_job_after_leader_exit_and_preserves_code(tmp_path):
    api = RecordingWindowsAPI(None)
    process = WindowsProcessFactory(api).start(["python"], cwd=tmp_path, environment={}, stdin_mode=StdinMode.INHERIT)
    api.returncode = 259
    assert process.poll() == 259
    process.terminate_tree()
    assert process.wait(None) == 259
    assert "terminate_job" in api.events
    process.close()
    assert not api.handles


def test_windows_adapter_wait_timeout_and_initialization_failure_keep_original_cause(tmp_path):
    api = RecordingWindowsAPI(None)
    process = WindowsProcessFactory(api).start(["python"], cwd=tmp_path, environment={}, stdin_mode=StdinMode.INHERIT)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        process.wait(.01)
    assert caught.value.timeout == .01
    process.terminate_tree()
    assert process.wait(1) == 1
    process.close()


@pytest.mark.parametrize("closed", [False, True])
def test_posix_adapter_rejects_windows_termination_only_while_open(monkeypatch, closed):
    child = Mock(spec=subprocess.Popen, pid=123, stdin=None,
                 stdout=io.BufferedReader(io.BytesIO()), stderr=io.BufferedReader(io.BytesIO()))
    process = PosixProcess(child)
    killpg = Mock()
    monkeypatch.setattr(os, "killpg", killpg, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    try:
        if closed:
            process.close()
            process.terminate_tree()
            process.terminate_tree()
        else:
            with pytest.raises(RuntimeError, match="POSIX process APIs require a POSIX platform"):
                process.terminate_tree()
        killpg.assert_not_called()
    finally:
        process.close()


def test_platform_process_preserves_binary_pipes_unicode_environment_cwd_and_input(tmp_path):
    factory: ProcessFactory = process_factory()
    code = "import os,sys,json; print(json.dumps([os.getcwd(),os.environ['VALUE'],sys.argv[1],sys.stdin.buffer.read().decode()])); sys.stderr.buffer.write(b'err\\r\\n')"
    process = factory.start([sys.executable, "-c", code, "quoted λ value"], cwd=tmp_path,
                            environment={**os.environ, "VALUE": "unicode λ"}, stdin_mode=StdinMode.PIPE)
    try:
        assert process.stdin is not None
        process.stdin.write("input λ".encode())
        process.stdin.close()
        assert json.loads(process.stdout.read()) == [str(tmp_path), "unicode λ", "quoted λ value", "input λ"]
        assert process.stderr.read() == b"err\r\n"
        assert process.wait(2) == 0
    finally:
        if process.poll() is None:
            process.terminate_tree()
            process.wait(2)
        process.close()


def test_platform_process_terminates_pipe_holding_descendant_after_leader_exit(tmp_path):
    child = "import time; time.sleep(10)"
    parent = "import subprocess,sys; p=subprocess.Popen([sys.executable,'-c',sys.argv[1]]); print(p.pid,flush=True)"
    process = process_factory().start([sys.executable, "-c", parent, child], cwd=tmp_path,
                                     environment=dict(os.environ), stdin_mode=StdinMode.INHERIT)
    try:
        assert int(process.stdout.readline()) > 0
        assert process.wait(2) == 0
        process.terminate_tree()
        assert process.stdout.read() == b"" and process.stderr.read() == b""
        assert process.wait(2) == 0
    finally:
        process.terminate_tree()
        process.close()


def test_platform_normal_close_preserves_redirected_background_child(tmp_path):
    marker = tmp_path / "completed"
    child = "import time,pathlib,sys; time.sleep(.15); pathlib.Path(sys.argv[1]).write_text('done')"
    parent = "import subprocess,sys; subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"
    process = process_factory().start([sys.executable, "-c", parent, child, str(marker)], cwd=tmp_path,
                                     environment=dict(os.environ), stdin_mode=StdinMode.INHERIT)
    try:
        assert process.stdout.read() == b"" and process.stderr.read() == b""
        assert process.wait(2) == 0
        process.close()
        deadline = time.monotonic() + 2
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert marker.read_text() == "done"
    finally:
        if not marker.exists():
            process.terminate_tree()
        process.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Requires real Windows job APIs")
@pytest.mark.parametrize("boundary", ["assign", "resume"])
def test_native_windows_initialization_failure_never_runs_target(boundary, tmp_path):
    class FailingWindowsAPI(WindowsAPI):
        def assign(self, job: Handle, process: Handle) -> None:
            if boundary == "assign":
                raise OSError("assignment rejected")
            super().assign(job, process)

        def resume(self, thread: Handle) -> None:
            raise OSError("resume rejected")

    marker = tmp_path / "should-not-run"
    with pytest.raises(OSError):
        WindowsProcessFactory(FailingWindowsAPI()).start(
            [sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()", str(marker)],
            cwd=tmp_path, environment=dict(os.environ), stdin_mode=StdinMode.INHERIT)
    assert not marker.exists()
