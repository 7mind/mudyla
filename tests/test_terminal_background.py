"""Background replies share terminal input without becoming action commands."""

from tests.logger_fixtures import prepared_logger

from io import StringIO
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from rich.console import Console

from mudyla.logging import terminal_logger_table as table
from mudyla.logging.terminal_logger_pure import PureTerminalLogger
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
            logger = prepared_logger(table.TableTerminalLogger, keys)
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
    assert probe.selection_style("256").bgcolor.get_truecolor() == (28, 28, 28)


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
@pytest.mark.parametrize("mode", ["pure", "table"])
def test_query_requires_actual_colored_input_and_output_tty(monkeypatch, color, no_color, output_tty, input_tty, plan, expected, mode):
    graph, keys = crossing_graph()
    stream = Terminal() if output_tty else StringIO()
    monkeypatch.setattr(sys, "stdin", Terminal() if input_tty else StringIO())
    output = OutputFormatter(no_color=no_color, compact=True, console=Console(file=stream, force_terminal=True, color_system=color, no_color=no_color))
    logger = (prepared_logger(PureTerminalLogger, keys, output, True, graph=graph, plan_style=plan) if mode == "pure" else
              prepared_logger(table.TableTerminalLogger, keys, no_color=no_color, console=output.console))
    monkeypatch.setattr(table.TableTerminalLogger, "_setup_terminal", lambda self: setattr(self, "_terminal_active", True))
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
            logger = prepared_logger(table.TableTerminalLogger, [])
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
    logger = prepared_logger(table.TableTerminalLogger, [])
    logger._background_probe = BackgroundProbe(time.monotonic())
    assert [logger._read_key_windows() for _ in range(2)] == ["down", "down"]
    logger._input_action = object()
    chars.extend("hé界")
    assert "".join(logger._read_key_windows() for _ in range(3)) == "hé界"
    assert logger._background_probe.background == (0, 0, 0)


@pytest.mark.parametrize("state", ["pending", "answered", "expired"])
def test_shutdown_waits_only_for_remaining_background_probe_deadline(monkeypatch, state):
    logger = prepared_logger(table.TableTerminalLogger, [])
    probe = BackgroundProbe(0)
    original_deadline = probe.deadline
    assert original_deadline == .25
    now = original_deadline if state == "expired" else original_deadline / 2
    started = now
    if state == "answered":
        probe.feed("\x1b]11;rgb:fa/fa/fa\x07", now)
        assert probe.answered
    logger._background_probe = probe
    logger._input_enabled = True
    waits, joins = [], []
    alive = True

    def sleep(seconds):
        nonlocal now
        assert state == "pending"
        assert 0 < seconds <= original_deadline - now
        waits.append(seconds)
        now += seconds

    def join(timeout):
        nonlocal alive
        assert logger.stop_flag and not probe.pending(now)
        joins.append(timeout)
        logger._drain_background_reply()
        alive = False

    logger._main_thread = SimpleNamespace(is_alive=lambda: alive, join=join)
    with monkeypatch.context() as timing:
        timing.setattr(table.time, "monotonic", lambda: now)
        timing.setattr(table.time, "sleep", sleep)
        logger.stop()
        first_waits = list(waits)
        logger.stop()
        assert waits == first_waits

    assert sum(waits) == pytest.approx(original_deadline - started if state == "pending" else 0)
    assert now == (original_deadline if state == "pending" else started)
    assert probe.deadline == original_deadline
    assert joins == [1.0]
    assert logger.stop_flag and not alive and not logger._terminal_active
    assert probe.background == ((250, 250, 250) if state == "answered" else None)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX terminal lifecycle")
@pytest.mark.parametrize("quit_first,reply", [(False, True), (True, True), (False, False)])
@pytest.mark.parametrize("slow_initial_frame", [False, True])
@pytest.mark.parametrize("mode", ["pure", "table"])
def test_native_query_shutdown_owns_input_until_reply_or_bounded_deadline(monkeypatch, quit_first, reply, slow_initial_frame, mode):
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
        output = OutputFormatter(no_color=False, compact=True, console=Console(file=stream, width=80, height=24, color_system="truecolor", no_color=False))
        logger = (prepared_logger(PureTerminalLogger, keys, output, True, graph=graph) if mode == "pure" else
                  prepared_logger(table.TableTerminalLogger, keys, console=output.console))
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
            if slow_initial_frame:
                assert query_times[0] - started >= .3
            assert logger._background_probe.background == ((250, 250, 250) if reply else None)
            assert not logger._main_thread.is_alive()
            restored = termios.tcgetattr(terminal)
            changed_flags = termios.ICANON | termios.ECHO | termios.ISIG | termios.NOFLSH
            assert restored[3] & changed_flags == original[3] & changed_flags
            assert restored[:3] == original[:3] and restored[4:] == original[4:]
            assert captured.count(BACKGROUND_QUERY.encode()) == 1
            if mode == "table":
                assert b"\x1b[?1049h" not in captured
                assert b"\x1b[?1000h" not in captured and b"\x1b[?1006h" not in captured
            logger.stop()
        finally:
            done.set()
            worker.join(timeout=1)
            os.close(master)


def test_white_palette_background_keeps_selected_row_visible():
    probe = BackgroundProbe(0)
    assert probe.feed('\x1b]11;rgb:ff/ff/ff\x07', 0) == ''
    style = probe.selection_style('256')
    assert style is not None, 'White 256-color background suppresses the selected-row tint'
    color = style.bgcolor.get_truecolor()
    assert max(color) < 255
    assert style.color is None and style.bold is None and style.dim is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX terminal lifecycle")
@pytest.mark.parametrize("mode", ["pure", "table"])
def test_native_shutdown_collects_reply_queued_before_deadline_during_refresh(monkeypatch, mode):
    import select
    import termios

    master, slave = os.openpty()
    captured = bytearray()
    refreshing = threading.Event()
    replied = threading.Event()
    done = threading.Event()
    reply_times = []
    prefix, suffix = "pré", "\x1b[Bsuffix"
    with os.fdopen(slave, "r", encoding="utf-8") as terminal, os.fdopen(os.dup(slave), "w", encoding="utf-8") as stream:
        original = termios.tcgetattr(terminal)
        monkeypatch.setattr(sys, "stdin", terminal)
        monkeypatch.setenv("TERM", "xterm-256color")
        graph, keys = crossing_graph()
        output = OutputFormatter(no_color=False, compact=True, console=Console(file=stream, width=80, height=24, color_system="truecolor", no_color=False))
        logger = (prepared_logger(PureTerminalLogger, keys, output, True, graph=graph) if mode == "pure" else
                  prepared_logger(table.TableTerminalLogger, keys, console=output.console))
        refresh = logger._refresh_display

        def hold_refresh():
            if logger._background_probe is not None and not refreshing.is_set():
                refreshing.set()
                assert replied.wait(timeout=.1)
                assert reply_times[0] < logger._background_probe.deadline
                time.sleep(max(0, logger._background_probe.deadline - time.monotonic()) + .02)
                assert time.monotonic() >= logger._background_probe.deadline
            refresh()

        monkeypatch.setattr(logger, "_refresh_display", hold_refresh)

        def terminal_reply():
            while not done.is_set():
                if select.select([master], [], [], .01)[0]:
                    captured.extend(os.read(master, 65536))
                if BACKGROUND_QUERY.encode() in captured and refreshing.is_set() and not replied.is_set():
                    os.write(master, prefix.encode() + b"\x1b]11;rgb:fa/fa/fa\x07" + suffix.encode())
                    reply_times.append(time.monotonic())
                    replied.set()

        worker = threading.Thread(target=terminal_reply)
        worker.start()
        try:
            logger.start()
            assert refreshing.wait(timeout=.3)
            logger.stop()
            assert logger._background_probe.background == (250, 250, 250)
            assert logger._pending_input == (prefix + suffix).encode()
            assert not logger._main_thread.is_alive()
            restored = termios.tcgetattr(terminal)
            changed_flags = termios.ICANON | termios.ECHO | termios.ISIG | termios.NOFLSH
            assert restored[3] & changed_flags == original[3] & changed_flags
            assert restored[:3] == original[:3] and restored[4:] == original[4:]
            assert captured.count(BACKGROUND_QUERY.encode()) == 1
            logger.stop()
        finally:
            logger.stop()
            done.set()
            worker.join(timeout=1)
            assert not worker.is_alive()
            os.close(master)


@pytest.fixture(params=["posix", "windows"])
def expired_probe_input(monkeypatch, request):
    if request.param == "posix" and sys.platform == "win32":
        pytest.skip("POSIX input")
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True, fileno=lambda: 9))
    logger = prepared_logger(table.TableTerminalLogger, [])
    logger._background_probe = BackgroundProbe(0)
    chunks, reads = [], []
    monkeypatch.setattr(table.time, "monotonic", lambda: 1)
    monkeypatch.setattr(table.time, "sleep", lambda seconds: pytest.fail("Expired collection must not sleep"))
    monkeypatch.setattr(logger, "_read_key", lambda: pytest.fail("Expired collection must not decode keys"))
    if request.param == "windows":
        monkeypatch.setattr(table.sys, "platform", "win32")

        def getwch():
            reads.append(1)
            return chunks.pop(0)

        monkeypatch.setattr(table, "msvcrt", SimpleNamespace(kbhit=lambda: bool(chunks), getwch=getwch), raising=False)
        from mudyla.logging import windows_mouse
        from tests.test_windows_mouse import ConsoleAPI

        monkeypatch.setattr(windows_mouse, "_console_api", lambda: ConsoleAPI(0x267, []))
        monkeypatch.setattr(table.msvcrt, "get_osfhandle", lambda fd: fd, raising=False)
    else:
        import termios

        settings = [0, 0, 0, termios.ICANON | termios.ECHO, 0, 0, [b"\x00"] * termios.NCCS]
        monkeypatch.setattr(table.termios, "tcgetattr", lambda fd: settings[:6] + [settings[6][:]])
        monkeypatch.setattr(table.termios, "tcsetattr", lambda fd, when, value: None)

        def ready(read, write, error, timeout):
            assert timeout == 0
            return (read if chunks else [], [], [])

        def read(fd, limit):
            chunk = chunks.pop(0)
            raw, rest = chunk[:limit], chunk[limit:]
            if rest:
                chunks.insert(0, rest)
            reads.append(len(raw))
            return raw

        monkeypatch.setattr(table.select, "select", ready)
        monkeypatch.setattr(table.os, "read", read)
    logger._setup_terminal()
    assert logger._terminal_active
    return logger, chunks, reads


@pytest.mark.parametrize("payload,ordinary,background", [
    ("j\x1b]11;rgb:ff/ff/ff\x07é\x1b[B\x1bq", "jé\x1b[B\x1bq", (255, 255, 255)),
    ("j\x1b]11;rgb:1/2/", "j", None),
    ("j\x1b]11;rgb:z/0/0\x07q", "jz/0/0q", None),
])
def test_expired_shutdown_collection_preserves_ready_input_and_reply_grammar(expired_probe_input, payload, ordinary, background):
    logger, chunks, reads = expired_probe_input
    if sys.platform == "win32":
        logger._pending_windows_input = "prior"
        chunks.extend(payload)
    else:
        logger._pending_input = b"prior"
        chunks.extend(bytes([byte]) for byte in payload.encode())
    logger._drain_background_reply()
    assert logger._background_probe.background == background
    assert len(logger._background_probe._candidate) < 32
    assert not chunks
    assert sum(reads) <= logger.MAX_INPUT_BYTES
    if sys.platform == "win32":
        assert logger._pending_windows_input == "prior" + ordinary
    else:
        assert logger._pending_input == ("prior" + ordinary).encode()


def test_expired_shutdown_collection_stops_at_cumulative_input_limit(expired_probe_input):
    logger, chunks, reads = expired_probe_input
    payload = "x" * (logger.MAX_INPUT_BYTES + 1)
    chunk_size = 7
    chunks.extend(payload if sys.platform == "win32" else
                  [payload[index:index + chunk_size].encode() for index in range(0, len(payload), chunk_size)])
    logger._drain_background_reply()
    assert sum(reads) == logger.MAX_INPUT_BYTES
    assert len(chunks) == 1
    if sys.platform == "win32":
        assert logger._pending_windows_input == payload[:-1]
    else:
        assert logger._pending_input == payload[:-1].encode()


@pytest.mark.parametrize("answered,input_enabled", [(False, True), (True, True), (False, False)])
def test_expired_shutdown_collection_never_waits_or_consumes_after_input_release(expired_probe_input, answered, input_enabled):
    logger, chunks, reads = expired_probe_input
    logger._background_probe.answered = answered
    logger._input_enabled = input_enabled
    if answered or not input_enabled:
        chunks.extend("q" if sys.platform == "win32" else [b"q"])
    before = list(chunks)
    logger._drain_background_reply()
    assert chunks == before
    assert not reads
    assert logger._background_probe.background is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX input")
def test_expired_shutdown_collection_recognizes_eof(expired_probe_input):
    logger, chunks, reads = expired_probe_input
    if sys.platform == "win32":
        pytest.skip("Windows input has no byte-stream EOF")
    chunks.append(b"")
    logger._drain_background_reply()
    assert not logger._input_enabled
    assert reads == [0]


def test_expired_windows_collection_preserves_extended_keys_and_utf16_units(expired_probe_input):
    logger, chunks, reads = expired_probe_input
    if sys.platform != "win32":
        pytest.skip("Windows input units")
    payload = "\xe0P\ud83d\ude00"
    chunks.extend(payload)
    logger._drain_background_reply()
    assert logger._pending_windows_input == payload
    assert sum(reads) == len(payload)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX terminal lifecycle")
def test_shutdown_does_not_collect_input_after_terminal_restoration(monkeypatch):
    import select
    import termios

    master, slave = os.openpty()
    paused, resume, done = threading.Event(), threading.Event(), threading.Event()
    with os.fdopen(slave, "r", encoding="utf-8") as terminal, os.fdopen(os.dup(slave), "w", encoding="utf-8") as stream:
        monkeypatch.setattr(sys, "stdin", terminal)
        monkeypatch.setenv("TERM", "xterm-256color")
        logger = prepared_logger(table.TableTerminalLogger, [], console=Console(
            file=stream, width=80, height=24, color_system="truecolor", no_color=False))
        refresh = logger._refresh_display

        def held_refresh():
            if logger._background_probe is not None and not paused.is_set():
                paused.set()
                assert resume.wait(timeout=5)
            refresh()

        def drain_output():
            while not done.is_set():
                if select.select([master], [], [], .01)[0]:
                    os.read(master, 65536)

        monkeypatch.setattr(logger, "_refresh_display", held_refresh)
        worker = threading.Thread(target=drain_output)
        worker.start()
        try:
            logger.start()
            assert paused.wait(timeout=.5)
            assert logger._terminal_active
            logger.stop()
            assert logger._main_thread.is_alive()
            assert not logger._terminal_active
            assert termios.tcgetattr(terminal)[3] & termios.ICANON
            payload = b"ownership-check\n"
            os.write(master, payload)
            assert select.select([slave], [], [], .1)[0]
            resume.set()
            logger._main_thread.join(timeout=1)
            assert not logger._main_thread.is_alive()
            assert logger._pending_input == b""
            assert select.select([slave], [], [], 0)[0]
            assert os.read(slave, len(payload)) == payload
        finally:
            resume.set()
            if logger._main_thread is not None:
                logger._main_thread.join(timeout=1)
            logger.stop()
            done.set()
            worker.join(timeout=1)
            assert not worker.is_alive()
            os.close(master)
