"""Retainer progress retains the executor's decisions and capture semantics."""

from pathlib import Path
import os
import signal
import time
from typing import TYPE_CHECKING, Literal

import pytest

from mudyla.dag import DAGBuilder
from mudyla.dag.graph import ActionKey
from mudyla.executor.retainer_executor import RetainerExecutor
from mudyla.executor.process import process_factory
from mudyla.parser.markdown_parser import MarkdownParser

if TYPE_CHECKING:
    from mudyla.executor.retainer_executor import RetainerRequest, RetainerCompletion


class RecordingObserver:
    def __init__(self, finished: Path) -> None:
        self.finished = finished
        self.requests: list[RetainerRequest] = []
        self.completions: list[RetainerCompletion] = []
        self.output: list[tuple[str, str, bool]] = []

    def begin_retainer(self, request: "RetainerRequest") -> None:
        self.requests.append(request)

    def retainer_output(self, key: ActionKey, text: str, stream: Literal["stdout", "stderr"]) -> None:
        self.output.append((stream, text, self.finished.exists()))

    def end_retainer(self, completion: "RetainerCompletion") -> None:
        self.completions.append(completion)


def retainer_executor(tmp_path: Path, script: str, observer: RecordingObserver) -> RetainerExecutor:
    definition = tmp_path / "actions.md"
    definition.write_text(f'''# action: goal

```python
mdl.soft("action.target", "action.keep")
```

# action: target

```python
pass
```

# action: keep

```python
{script}
```
''', encoding="utf-8")
    document = MarkdownParser().parse_files([definition])
    graph = DAGBuilder(document).build_graph(["goal"], {})
    return RetainerExecutor(graph, document, tmp_path, {}, [], {}, {}, {}, observer, process_factory(), without_nix=True)


def test_retainer_stdout_and_stderr_are_visible_before_child_exit(tmp_path):
    finished = tmp_path / "finished"
    observer = RecordingObserver(finished)
    executor = retainer_executor(tmp_path, f'''import sys, time
from pathlib import Path
print("EARLY_STDOUT", flush=True)
print("EARLY_STDERR", file=sys.stderr, flush=True)
time.sleep(.15)
Path({str(finished)!r}).write_text("done")
mdl.retain()
''', observer)
    retained, results = executor.execute_retainers()
    assert any(stream == "stdout" and "EARLY_STDOUT" in text and not ended
               for stream, text, ended in observer.output), "stdout callback arrived only after child exit"
    assert any(stream == "stderr" and "EARLY_STDERR" in text and not ended
               for stream, text, ended in observer.output), "stderr callback arrived only after child exit"
    assert retained == {ActionKey.from_name("target")}
    assert results[0].stdout == "EARLY_STDOUT\n" and results[0].stderr == "EARLY_STDERR\n"
    assert observer.requests[0].targets == (ActionKey.from_name("target"),)
    assert observer.completions[0].result is results[0]


@pytest.mark.parametrize("exit_code,expected", [(0, "succeeded"), (7, "nonzero")])
def test_retainer_completion_distinguishes_ignore_from_nonzero(tmp_path, exit_code, expected):
    observer = RecordingObserver(tmp_path / "unused")
    retained, results = retainer_executor(tmp_path, f"raise SystemExit({exit_code})", observer).execute_retainers()
    assert len(observer.completions) == 1, "No per-retainer completion fact was published"
    assert observer.completions[0].outcome.value == expected
    assert not retained and not results[0].retained and results[0].soft_dep_targets == []
    assert observer.completions[0].decisions[0].target == ActionKey.from_name("target")
    assert not observer.completions[0].decisions[0].retained


def test_retainer_final_buffers_keep_strict_utf8_and_universal_newlines(tmp_path):
    observer = RecordingObserver(tmp_path / "unused")
    executor = retainer_executor(tmp_path, '''import os, time
os.write(1, b"a\\r")
time.sleep(.02)
os.write(1, b"\\nb\\rc\\n\\xc3")
time.sleep(.02)
os.write(1, b"\\xa9")
os.write(2, b"error\\r\\n")
''', observer)
    _, results = executor.execute_retainers()
    assert results[0].stdout == "a\nb\nc\né"
    assert results[0].stderr == "error\n"


def test_invalid_utf8_does_not_cancel_retainer_side_effects(tmp_path):
    finished = tmp_path / "finished"
    observer = RecordingObserver(finished)
    executor = retainer_executor(tmp_path, f'''import os, time
from pathlib import Path
os.write(1, b"\\xff")
time.sleep(.08)
Path({str(finished)!r}).write_text("done")
mdl.retain()
''', observer)
    retained, results = executor.execute_retainers()
    assert finished.exists(), "Display decoding cancelled the retainer before its side effect"
    assert not retained and not results[0].retained
    assert "utf-8" in results[0].stderr


def test_invalid_utf8_keeps_timeout_outcome_and_public_empty_buffers(tmp_path, monkeypatch):
    from mudyla.executor import retainer_executor as module
    monkeypatch.setattr(module, "RETAINER_TIMEOUT_SECONDS", .12)
    observer = RecordingObserver(tmp_path / "unused")
    _, results = retainer_executor(tmp_path, '''import os, time
os.write(1, b"\\xff")
time.sleep(1)
''', observer).execute_retainers()
    assert results[0].stdout == "" and results[0].stderr == "Timeout expired"
    assert observer.completions[0].outcome.value == "timed_out"


def test_invalid_utf8_reports_full_buffer_error_position(tmp_path):
    observer = RecordingObserver(tmp_path / "unused")
    _, results = retainer_executor(tmp_path, '''import os, time
os.write(1, b"a" * 37)
time.sleep(.02)
os.write(1, b"\\xff")
''', observer).execute_retainers()
    assert "position 37" in results[0].stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group cleanup for the retained baseline control")
@pytest.mark.parametrize("invalid_output", [False, True])
def test_completed_retainer_does_not_cancel_redirected_background_child(tmp_path, invalid_output):
    marker = tmp_path / "background-finished"
    child_pid = tmp_path / "background-pid"
    release = tmp_path / "background-release"
    observer = RecordingObserver(tmp_path / "unused")
    background = f'''import os, time
from pathlib import Path
Path({str(child_pid)!r}).write_text(str(os.getpid()))
time.sleep(.15)
Path({str(marker)!r}).write_text('done')
deadline = time.monotonic() + 5
while not Path({str(release)!r}).exists():
    if time.monotonic() >= deadline: raise TimeoutError("background fixture was not released")
    time.sleep(.01)
'''
    executor = retainer_executor(tmp_path, f'''import os, subprocess, sys
from pathlib import Path
subprocess.Popen([sys.executable, "-c", {background!r}], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
{"os.write(1, b'\\xff')" if invalid_output else "pass"}
mdl.retain()
''', observer)
    failure: BaseException | None = None
    try:
        retained, results = executor.execute_retainers()
        deadline = time.monotonic() + .7
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert marker.exists(), "Ordinary completed capture terminated a redirected background child"
        os.kill(int(child_pid.read_text()), 0)
        if invalid_output:
            assert not retained and "utf-8" in results[0].stderr
        else:
            assert retained == {ActionKey.from_name("target")} and results[0].retained
    except BaseException as error:
        failure = error
        raise
    finally:
        try:
            release.touch()
            if child_pid.exists():
                pid = int(child_pid.read_text())
                deadline = time.monotonic() + 2
                terminated = False
                while True:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    if time.monotonic() >= deadline:
                        assert not terminated, "Owned background child survived release and termination"
                        os.kill(pid, signal.SIGKILL)
                        terminated = True
                        deadline = time.monotonic() + 1
                    time.sleep(.01)
        except BaseException as cleanup_error:
            if failure is not None:
                raise BaseExceptionGroup("Background assertion and cleanup failed", [failure, cleanup_error]) from None
            raise
