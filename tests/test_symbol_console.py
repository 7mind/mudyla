"""Independent executable requirements for console-owned symbol resolution."""

from io import BytesIO, TextIOWrapper
import inspect
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from mudyla.logging.formatters import OutputFormatter
from mudyla.logging.formatters.symbols import StatusSymbol, SymbolsFormatter


def test_status_request_uses_formatter_owned_console():
    output = OutputFormatter(no_color=False, compact=True)
    stream = TextIOWrapper(BytesIO(), encoding="utf-8")
    output.console.file = stream
    assert output.symbols.status(StatusSymbol.READY, now=0) == "○"


def test_status_api_does_not_accept_repeated_console_policy():
    parameters = inspect.signature(SymbolsFormatter.status).parameters
    assert "encoding" not in parameters and "ascii_only" not in parameters, str(parameters)


def test_completion_resolution_tracks_actual_console_destination():
    output = OutputFormatter(no_color=False)
    utf8 = TextIOWrapper(BytesIO(), encoding="utf-8")
    ascii_stream = TextIOWrapper(BytesIO(), encoding="ascii")
    output.console.file = utf8
    assert output.symbols.Check == "●"
    output.console.file = ascii_stream
    assert output.symbols.Check == "+", (
        output.console.encoding, output.symbols.Check,
        output.symbols.status(StatusSymbol.DONE, now=0),
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX terminal sizing")
@pytest.mark.parametrize("mode", ["pure", "table"])
@pytest.mark.parametrize("term", ["dumb", "unknown", "xterm-256color"])
@pytest.mark.parametrize("width,height", [(None, None), (95, None), (None, 33), (95, 33)])
def test_force_interactive_preserves_console_sizing_policy(tmp_path, mode, term, width, height):
    import fcntl
    import struct
    import termios

    script = """
import fcntl
import json
import os
from pathlib import Path
import struct
import sys
import termios
import threading
from rich.console import Console
from mudyla.logging.action_logger_pure import ActionLoggerPure
from mudyla.logging.action_logger_table import ActionLoggerTable
from mudyla.logging.formatters import OutputFormatter

mode, result_path, size_json = sys.argv[1:]
width, height = json.loads(size_json)
environment = dict(os.environ)
threads = [thread.ident for thread in threading.enumerate()]
original = Console(width=width, height=height)
output = OutputFormatter(no_color=False, compact=True, console=original)
logger = (ActionLoggerPure([], output, True, force_interactive=True) if mode == "pure" else
          ActionLoggerTable([], console=original, force_interactive=True))
before = tuple(logger.console.size)
fcntl.ioctl(sys.stdout.fileno(), termios.TIOCSWINSZ, struct.pack("HHHH", 40, 160, 0, 0))
after = tuple(logger.console.size)
Path(result_path).write_text(json.dumps({
    "before": before, "after": after,
    "same_console": logger.console is original,
    "symbols_bound_live": logger._output.symbols.console is logger.console,
    "threads_unchanged": threads == [thread.ident for thread in threading.enumerate()],
    "environment_unchanged": environment == dict(os.environ),
}))
"""
    environment = dict(os.environ, TERM=term)
    environment.pop("COLUMNS", None)
    environment.pop("LINES", None)
    result_path = tmp_path / "sizing.json"
    master, slave = os.openpty()
    child = None
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        child = subprocess.Popen([sys.executable, "-c", script, mode, str(result_path), json.dumps([width, height])],
                                 cwd=Path(__file__).resolve().parents[1], env=environment,
                                 stdin=slave, stdout=slave, stderr=subprocess.PIPE, start_new_session=True)
        _, error = child.communicate(timeout=4)
        assert child.returncode == 0, error.decode()
    finally:
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=2)
        os.close(slave)
        os.close(master)
    assert child is not None
    with pytest.raises(ProcessLookupError):
        os.killpg(child.pid, 0)
    record = json.loads(result_path.read_text())
    assert record["before"] == [width if width is not None else 80, height if height is not None else 24]
    assert record["after"] == [width if width is not None else 160, height if height is not None else 40]
    assert record["same_console"] == (term == "xterm-256color")
    assert record["symbols_bound_live"]
    assert record["threads_unchanged"]
    assert record["environment_unchanged"]
