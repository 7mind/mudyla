"""Decode captured terminal lines while retaining ANSI text styles."""

from rich.text import Text


def terminal_text(capture: str) -> Text:
    return Text.from_ansi(capture.replace("\r\n", "\n"))
