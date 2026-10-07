"""Background replies share terminal input without becoming action commands."""

from io import StringIO
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from rich.console import Console

from mudyla.logging import action_logger_table as table
from mudyla.logging.action_logger_pure import ActionLoggerPure
from mudyla.logging.formatters import OutputFormatter
from mudyla.logging.terminal_background import BACKGROUND_QUERY, BackgroundProbe
from tests.test_plan_dag import crossing_graph


@pytest.mark.parametrize("ending", ["\x07", "\x1b\\"])
def test_reply_at_every_chunk_boundary_preserves_keys_and_scaling(ending):
    reply = "\x1b]11;rgb:f/800/1234" + ending
    for split in range(len(reply) + 1):
        probe = BackgroundProbe(0)
        result = probe.feed("j" + reply[:split], 0)
        result += probe.feed(reply[split:] + "\x1b[Bé", .001)
        assert result == "j\x1b[Bé"
        assert probe.background == (255, 128, 18)
        assert not probe.pending(.002)


@pytest.mark.parametrize("reply,ordinary", [("\x1b]11;rgb:z/0/0\x07", "z/0/0"),
    ("\x1b]11;rgb:" + "a" * 1000 + "\x1b\\", "a" * 996)])
def test_impossible_reply_grammar_recovers_ordinary_input_with_bounded_storage(reply, ordinary):
    probe = BackgroundProbe(0)
    recovered = ""
    for char in reply:
        recovered += probe.feed(char, .001)
        assert len(probe._candidate) < 32
    assert recovered == ordinary
    assert probe.background is None
    assert probe.feed("q", .002) == "q"
    assert probe.selection_style("truecolor") is None


def test_escape_unknown_prefix_expiration_and_late_reply():
    probe = BackgroundProbe(0)
    assert probe.feed("\x1b", 0) == ""
    assert probe.feed("", .021) == "\x1b"
    assert probe.feed("\x1bx", .030) == "\x1bx"
    assert probe.feed("\x1b]11;rgb:", .040) == ""
    assert probe.feed("q", .300) == "q"
    assert not probe.pending(.300)
    assert probe.feed("\x1b]11;rgb:00/00/00\x07k", .500) == "k"
    assert probe.background == (0, 0, 0)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX input")
def test_delayed_fragmented_reply_never_enters_action_input(monkeypatch):
    import tty

    master, slave = os.openpty()
    child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.readline())"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    with os.fdopen(slave, "r", encoding="utf-8") as terminal:
        try:
            tty.setraw(terminal.fileno())
            monkeypatch.setattr(sys, "stdin", terminal)
            _, keys = crossing_graph()
            logger = table.ActionLoggerTable(keys)
            logger._input_action = keys[0]

            def send_input(key, text):
                assert key == keys[0]
                child.stdin.write(text)
                child.stdin.flush()

            logger.set_input_callback(send_input)
            logger._background_probe = BackgroundProbe(time.monotonic())
            os.write(master, b"PREFIX_\x1b]11;rgb:1/2/")
            prefix_keys = [logger._read_key_unix() for _ in range(8)]
            for key in prefix_keys:
                logger._handle_input_key(key)
            time.sleep(.3)
            os.write(master, b"f\x07SUFFIX\r")
            suffix_keys = [logger._read_key_unix() for _ in range(10)]
            for key in suffix_keys:
                logger._handle_input_key(key)
            assert "".join(prefix_keys + suffix_keys) == "PREFIX_SUFFIXenter"
            assert logger._background_probe.background == (17, 34, 255)
            assert child.wait(timeout=2) == 0
            assert child.stdout.read() == "PREFIX_SUFFIX\n"
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=2)
            child.stdin.close()
            child.stdout.close()
            os.close(master)


@pytest.mark.parametrize("key", ["q", "j", "\r", "\x1b[B"])
def test_impossible_byte_after_partial_rgb_recovers_user_key_after_deadline(key):
    probe = BackgroundProbe(0)
    assert probe.feed("\x1b]11;rgb:1/2/", 0) == ""
    assert probe.feed(key + "\x07", .3) == key
    assert probe.background is None


def test_escape_cancels_an_incomplete_rgb_reply_with_normal_key_delay():
    probe = BackgroundProbe(0)
    assert probe.feed("\x1b]11;rgb:1/2/", 0) == ""
    assert probe.feed("\x1b", .01) == ""
    assert probe.feed("", .031) == "\x1b"


@pytest.mark.parametrize("background,expected", [((250, 250, 250), (242, 242, 242)), ((20, 25, 30), (27, 32, 37))])
def test_truecolor_tint_changes_only_background(background, expected):
    probe = BackgroundProbe(0)
    probe.background = background
    style = probe.selection_style("truecolor")
    assert tuple(style.bgcolor.get_truecolor()) == expected
    assert style.color is None and style.bold is None and style.dim is None
    assert probe.selection_style("standard") is None
    assert probe.selection_style(None) is None


def test_palette_quantization_requires_small_change_in_intended_direction():
    probe = BackgroundProbe(0)
    probe.background = (250, 250, 250)
    assert probe.selection_style("256").bgcolor.get_truecolor() == (238, 238, 238)
    probe.background = (20, 25, 30)
    assert probe.selection_style("256") is None


class Terminal(StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize("color,no_color,output_tty,input_tty,plan,expected", [
    ("truecolor", False, True, True, "dag", True),
    ("256", False, True, True, "dag", True),
    ("standard", False, True, True, "dag", False),
    ("truecolor", True, True, True, "dag", False),
    ("truecolor", False, False, True, "dag", False),
    ("truecolor", False, True, False, "dag", False),
    ("truecolor", False, True, True, "tree", True),
])
def test_query_requires_actual_colored_input_and_output_tty(monkeypatch, color, no_color, output_tty, input_tty, plan, expected):
    graph, keys = crossing_graph()
    stream = Terminal() if output_tty else StringIO()
    monkeypatch.setattr(sys, "stdin", Terminal() if input_tty else StringIO())
    output = OutputFormatter(no_color=no_color, compact=True)
    output._console = Console(file=stream, force_terminal=True, color_system=color, no_color=no_color)
    logger = ActionLoggerPure(keys, output, True, graph=graph, plan_style=plan)
    monkeypatch.setattr(table.ActionLoggerTable, "_setup_terminal", lambda self: setattr(self, "_terminal_active", True))
    logger._setup_terminal()
    logger._probe_terminal_background()
    logger._probe_terminal_background()
    assert stream.getvalue().count(BACKGROUND_QUERY) == int(expected)
    assert (logger._background_probe is not None) == expected


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX input")
def test_posix_reader_filters_reply_and_preserves_arrow_utf8_and_escape(monkeypatch):
    import tty

    master, slave = os.openpty()
    with os.fdopen(slave, "r", encoding="utf-8") as terminal:
        try:
            tty.setraw(terminal.fileno())
            monkeypatch.setattr(sys, "stdin", terminal)
            logger = table.ActionLoggerTable([])
            logger._background_probe = BackgroundProbe(time.monotonic())
            os.write(master, b"j\x1b]11;rgb:ff/ff/ff\x1b\\\x1b[B\x1bq")
            assert [logger._read_key_unix() for _ in range(4)] == ["down", "down", "escape", "q"]
            logger._input_action = object()
            os.write(master, "hé界".encode())
            assert "".join(logger._read_key_unix() for _ in range(6)) == "hé界"
            assert logger._background_probe.background == (255, 255, 255)
        finally:
            os.close(master)


def test_windows_reader_filters_reply_and_preserves_extended_and_unicode_keys(monkeypatch):
    chars = list("\x1b]11;rgb:00/00/00\x07j\xe0P")
    monkeypatch.setattr(table.sys, "platform", "win32")
    monkeypatch.setattr(table, "msvcrt", SimpleNamespace(kbhit=lambda: bool(chars), getwch=lambda: chars.pop(0)), raising=False)
    logger = table.ActionLoggerTable([])
    logger._background_probe = BackgroundProbe(time.monotonic())
    assert [logger._read_key_windows() for _ in range(2)] == ["down", "down"]
    logger._input_action = object()
    chars.extend("hé界")
    assert "".join(logger._read_key_windows() for _ in range(3)) == "hé界"
    assert logger._background_probe.background == (0, 0, 0)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX terminal lifecycle")
@pytest.mark.parametrize("quit_first,reply", [(False, True), (True, True), (False, False)])
@pytest.mark.parametrize("slow_initial_frame", [False, True])
def test_native_query_shutdown_owns_input_until_reply_or_bounded_deadline(monkeypatch, quit_first, reply, slow_initial_frame):
    import select
    import termios

    master, slave = os.openpty()
    captured = bytearray()
    query_times = []
    done = threading.Event()
    with os.fdopen(slave, "r", encoding="utf-8") as terminal, os.fdopen(os.dup(slave), "w", encoding="utf-8") as stream:
        original = termios.tcgetattr(terminal)
        monkeypatch.setattr(sys, "stdin", terminal)
        monkeypatch.setenv("TERM", "xterm-256color")
        graph, keys = crossing_graph()
        output = OutputFormatter(no_color=False, compact=True)
        output._console = Console(file=stream, width=80, height=24, color_system="truecolor", no_color=False)
        logger = ActionLoggerPure(keys, output, True, graph=graph)
        initial_frames = []
        refresh = logger._refresh_display

        def initial_refresh():
            if slow_initial_frame and not initial_frames:
                initial_frames.append(True)
                time.sleep(.3)
            refresh()

        monkeypatch.setattr(logger, "_refresh_display", initial_refresh)

        def terminal_reply():
            answered = False
            while not done.is_set():
                if select.select([master], [], [], .01)[0]:
                    captured.extend(os.read(master, 65536))
                    if BACKGROUND_QUERY.encode() in captured and not answered:
                        query_times.append(time.monotonic())
                        answered = True
                        if quit_first:
                            os.write(master, b"q")
                        if reply:
                            time.sleep(.05)
                            os.write(master, b"\x1b]11;rgb:fa/fa/fa\x07")

        worker = threading.Thread(target=terminal_reply)
        worker.start()
        try:
            started = time.monotonic()
            logger.start()
            logger.stop()
            elapsed = time.monotonic() - started
            assert elapsed < (.9 if slow_initial_frame else .6)
            if slow_initial_frame:
                assert query_times[0] - started >= .3
            assert logger._background_probe.background == ((250, 250, 250) if reply else None)
            assert not logger._main_thread.is_alive()
            restored = termios.tcgetattr(terminal)
            changed_flags = termios.ICANON | termios.ECHO | termios.ISIG | termios.NOFLSH
            assert restored[3] & changed_flags == original[3] & changed_flags
            assert restored[:3] == original[:3] and restored[4:] == original[4:]
            assert captured.count(BACKGROUND_QUERY.encode()) == 1
            started = time.monotonic()
            logger.stop()
            assert time.monotonic() - started < .1
        finally:
            done.set()
            worker.join(timeout=1)
            os.close(master)
