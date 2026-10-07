from rich.console import Console

from tests.terminal_capture import terminal_text


def test_terminal_capture_preserves_crlf_lines_and_ansi_styles():
    rendered = terminal_text("\x1b[32mFIRST\x1b[0m\r\nSECOND\r\n")
    assert rendered.plain.splitlines() == ["FIRST", "SECOND"]
    assert rendered.get_style_at_offset(Console(), 0).color.number == 2


def test_terminal_capture_preserves_standalone_carriage_return_overwrite():
    assert terminal_text("before\rafter\r\n").plain.strip() == "after"
