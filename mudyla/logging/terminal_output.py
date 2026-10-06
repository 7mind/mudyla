"""Bounded terminal lexical state shared by streamed and replayed output."""

from dataclasses import dataclass, field
import os
import sys
from typing import Literal, Optional, TextIO

from ..dag.graph import ActionKey


ControlState = Literal["text", "escape", "csi", "osc", "osc_escape", "string", "string_escape"]
MAX_SGR_PARAMETER = 65535
MAX_COLOR_FIELDS = 6


def same_terminal(first: TextIO, second: TextIO) -> bool:
    return first is second or (first.isatty() and second.isatty() and same_destination(first, second))


def same_destination(first: TextIO, second: TextIO) -> bool:
    if first is second:
        return True
    try:
        if sys.platform == "win32":
            import msvcrt
            return msvcrt.get_osfhandle(first.fileno()) == msvcrt.get_osfhandle(second.fileno())
        return os.path.samestat(os.fstat(first.fileno()), os.fstat(second.fileno()))
    except (OSError, ValueError):
        return False


@dataclass
class SGRForeground:
    candidate: Optional[str]
    _number: Optional[int] = None
    _group: list[Optional[int]] = field(default_factory=list)
    _valid: bool = True
    _extended: Optional[int] = None
    _operands: list[Optional[int]] = field(default_factory=list)
    _remaining: int = 0

    def consume(self, char: str) -> None:
        if "0" <= char <= "9":
            number = (self._number or 0) * 10 + ord(char) - ord("0")
            self._number = number if self._number != -1 and number <= MAX_SGR_PARAMETER else -1
        elif char == ":":
            self._field()
        elif char == ";":
            self._parameter()
        else:
            self._valid = False

    def _field(self) -> None:
        if len(self._group) < MAX_COLOR_FIELDS:
            self._group.append(self._number)
        else:
            self._valid = False
        self._number = None

    def _color(self, code: int, values: list[Optional[int]], colon: bool) -> None:
        if code != 38:
            return
        self.candidate = None
        if len(values) == 2 and values[0] == 5 and values[1] is not None and 0 <= values[1] <= 255:
            self.candidate = f"38;5;{values[1]}"
        elif values and values[0] == 2:
            rgb = values[1:] if len(values) == 4 else values[2:] if len(values) == 5 and values[1] in {None, 0} else []
            if len(rgb) == 3 and all(value is not None and 0 <= value <= 255 for value in rgb):
                self.candidate = "38;2;" + ";".join(str(value) for value in rgb)
        if colon and self.candidate is not None:
            # Terminals differ on the optional color-space slot; replay its syntax.
            self.candidate = "38:" + ":".join("" if value is None else str(value) for value in values)

    def _parameter(self) -> None:
        self._field()
        values, self._group = self._group, []
        if self._extended is not None:
            if len(values) != 1:
                if self._extended == 38:
                    self.candidate = None
                self._extended = None
                self._operands = []
                return
            self._operands.append(values[0])
            if len(self._operands) == 1:
                self._remaining = 1 if values[0] == 5 else 3 if values[0] == 2 else 0
            else:
                self._remaining -= 1
            if self._remaining == 0:
                self._color(self._extended, self._operands, False)
                self._extended = None
                self._operands = []
            return
        code = values[0] or 0
        if len(values) > 1:
            if code in {38, 48, 58}:
                self._color(code, values[1:], True)
            elif code in {0, 39} or 30 <= code <= 37 or 90 <= code <= 97:
                self.candidate = None
        elif code in {0, 39}:
            self.candidate = "39"
        elif 30 <= code <= 37 or 90 <= code <= 97:
            self.candidate = str(code)
        elif code in {38, 48, 58}:
            self._extended = code

    def finish(self) -> Optional[str]:
        self._parameter()
        if not self._valid or self._extended == 38:
            return None
        return self.candidate


@dataclass
class StreamState:
    owner: Optional[ActionKey] = None
    line_start: bool = True
    carriage_return: bool = False
    control: ControlState = "text"
    foreground: Optional[str] = "39"
    _sgr: SGRForeground = field(default_factory=lambda: SGRForeground("39"))

    def consume(self, char: str) -> None:
        if char in {"\x18", "\x1a"}:
            self.control = "text"
        elif self.control == "text":
            if char == "\x1b":
                self.control = "escape"
            elif char == "\x9b":
                self.control = "csi"
                self._sgr = SGRForeground(self.foreground)
            elif char == "\x9d":
                self.control = "osc"
            elif char in {"\x90", "\x98", "\x9e", "\x9f"}:
                self.control = "string"
            if self.control != "text":
                return
            self.line_start = char == "\n"
            self.carriage_return = char == "\r"
        elif self.control == "escape":
            if char == "[":
                self.control = "csi"
                self._sgr = SGRForeground(self.foreground)
            elif char == "]":
                self.control = "osc"
            elif char in "PX^_":
                self.control = "string"
            elif "0" <= char <= "~":
                self.control = "text"
        elif self.control == "csi":
            if "@" <= char <= "~":
                if char == "m":
                    self.foreground = self._sgr.finish()
                self.control = "text"
            elif char == "\x1b":
                self.control = "escape"
            else:
                self._sgr.consume(char)
        elif self.control in {"osc", "string"}:
            if char == "\x9c" or (self.control == "osc" and char == "\x07"):
                self.control = "text"
            elif char == "\x1b":
                self.control = "osc_escape" if self.control == "osc" else "string_escape"
        elif char == "\\" or char == "\x9c" or (self.control == "osc_escape" and char == "\x07"):
            self.control = "text"
        elif char != "\x1b":
            self.control = "osc" if self.control == "osc_escape" else "string"


    def finish_control(self, target: TextIO) -> None:
        if self.control != "text":
            # Cancel without dispatching an unfinished OSC/DCS command.
            if target.isatty() and os.environ.get("TERM") not in {"dumb", "unknown"}:
                target.write("\x18")
            self.control = "text"
