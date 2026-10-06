"""TeamCity service-message transport and incremental parallel child framing."""

from dataclasses import dataclass
from io import StringIO, TextIOWrapper
import sys
from threading import RLock
from typing import Callable, Optional, TextIO
from unicodedata import category
from uuid import uuid4

from .terminal_output import StreamState, same_destination


SERVICE_START = "##teamcity["
MAX_SERVICE_MESSAGE_CHARACTERS = 1024 * 1024
ATTRIBUTE_WHITESPACE = "".join(chr(code) for code in range(33))


def escape_value(value: str) -> str:
    result: list[str] = []
    escapes = {"|": "||", "'": "|'", "[": "|[", "]": "|]", "\n": "|n", "\r": "|r"}
    for char in value:
        if char in escapes:
            result.append(escapes[char])
        elif not 32 <= ord(char) < 127:
            units = char.encode("utf-16-be", errors="surrogatepass")
            result.extend(f"|0x{int.from_bytes(units[i:i + 2], 'big'):04x}" for i in range(0, len(units), 2))
        else:
            result.append(char)
    return "".join(result)


@dataclass(frozen=True)
class ServiceMessage:
    name: str
    attributes: dict[str, str]

    def serialize(self) -> str:
        attributes = "".join(f" {key}='{escape_value(value)}'" for key, value in self.attributes.items())
        return f"{SERVICE_START}{self.name}{attributes}]"


def _identifier(value: str) -> bool:
    if not value:
        return False
    for index, char in enumerate(value):
        kind = category(char)
        code = ord(char)
        start = kind.startswith("L") or kind in {"Nl", "Sc", "Pc"}
        continuation = kind in {"Mn", "Mc", "Nd", "Cf"} or code <= 8 or 14 <= code <= 27 or 127 <= code <= 159
        if code > 0xffff or not (start or (index > 0 and continuation)):
            return False
    return True


def _header_whitespace(char: str) -> bool:
    return char in "\t\n\v\f\r\x1c\x1d\x1e\x1f" or (
        category(char) in {"Zs", "Zl", "Zp"} and char not in "\u00a0\u2007\u202f")


def parse_message(text: str) -> Optional[ServiceMessage]:
    if not text.startswith(SERVICE_START) or not text.endswith("]") or "\r" in text or "\n" in text:
        return None
    body = text[len(SERVICE_START):-1]
    separator = next((index for index, char in enumerate(body) if _header_whitespace(char)), len(body))
    name = body[:separator]
    if not name:
        return None
    arguments = body[separator + 1:].strip(ATTRIBUTE_WHITESPACE)
    attributes: dict[str, str] = {}
    position = 0
    while position < len(arguments):
        while position < len(arguments) and arguments[position] in ATTRIBUTE_WHITESPACE:
            position += 1
        if position == len(arguments):
            break
        single = arguments[position] == "'"
        if single:
            if attributes:
                return None
            key = "tc:arg"
        else:
            end = arguments.find("=", position)
            if end < 0:
                return None
            key = arguments[position:end].strip(ATTRIBUTE_WHITESPACE)
            if not _identifier(key.removeprefix("tc:")):
                return None
            position = end + 1
            while position < len(arguments) and arguments[position] in ATTRIBUTE_WHITESPACE:
                position += 1
        if position >= len(arguments) or arguments[position] != "'":
            return None
        position += 1
        value: list[str] = []
        while position < len(arguments) and arguments[position] != "'":
            char = arguments[position]
            if char == "|" and position + 1 < len(arguments):
                escaped = arguments[position + 1]
                substitutions = {"n": "\n", "r": "\r", "x": "\u0085", "l": "\u2028", "p": "\u2029",
                                 "|": "|", "'": "'", "[": "[", "]": "]"}
                if escaped in substitutions:
                    value.append(substitutions[escaped])
                    position += 2
                    continue
                if arguments[position + 1:position + 3] == "0x":
                    digits = arguments[position + 3:position + 7]
                    if len(digits) == 4 and all(c in "0123456789abcdefABCDEF" for c in digits):
                        value.append(chr(int(digits, 16)))
                        position += 7
                        continue
            value.append(char)
            position += 1
        if position == len(arguments):
            return None
        attributes[key] = "".join(value).encode("utf-16-be", errors="surrogatepass").decode("utf-16-be", errors="surrogatepass")
        position += 1
        if single and arguments[position:].strip(ATTRIBUTE_WHITESPACE):
            return None
    return ServiceMessage(name, attributes)


class ChildMessages:
    """Only an ambiguous opener or one bounded native record can remain pending."""

    def __init__(self, ordinary: Callable[[str], None], native: Callable[[ServiceMessage], None]):
        self._ordinary = ordinary
        self._native = native
        self._candidate: list[str] = []
        self._escaped = False
        self._in_message = False

    def write(self, text: str) -> None:
        ordinary: list[str] = []
        for char in text:
            if self._in_message:
                self._candidate.append(char)
                if len(self._candidate) > MAX_SERVICE_MESSAGE_CHARACTERS:
                    raise ValueError("TeamCity child service message exceeds the 1,048,576-character transport limit")
                if char in "\r\n":
                    self.finish()
                elif self._escaped:
                    self._escaped = False
                elif char == "|":
                    self._escaped = True
                elif char == "]":
                    candidate = "".join(self._candidate)
                    parsed = parse_message(candidate)
                    if parsed is None:
                        self._ordinary(candidate)
                    else:
                        self._native(parsed)
                    self._candidate.clear()
                    self._in_message = False
                continue
            self._candidate.append(char)
            while self._candidate and not SERVICE_START.startswith("".join(self._candidate)):
                ordinary.append(self._candidate.pop(0))
            if len(self._candidate) == len(SERVICE_START):
                if ordinary:
                    self._ordinary("".join(ordinary))
                    ordinary.clear()
                self._in_message = True
        if ordinary:
            self._ordinary("".join(ordinary))

    def finish(self) -> None:
        if self._candidate:
            self._ordinary("".join(self._candidate))
            self._candidate.clear()
        self._in_message = False
        self._escaped = False


class TeamCityWriter:
    def __init__(self, stdout: TextIO, stderr: TextIO):
        self.stdout = stdout
        self.stderr = stderr
        self.lock = RLock()
        self.run_prefix = "mdl-" + uuid4().hex
        self._streams = {"stdout": StreamState(), "stderr": StreamState()}
        if same_destination(stdout, stderr):
            self._streams["stderr"] = self._streams["stdout"]
        for stream in (stdout, stderr):
            if isinstance(stream, TextIOWrapper):
                stream.reconfigure(encoding="utf-8", errors="replace")

    def record(self, message: ServiceMessage) -> None:
        with self.lock:
            stdout_state = self._streams["stdout"]
            interrupted_control = stdout_state.control != "text"
            stdout_state.finish_control(self.stdout)
            if self._streams["stderr"] is not stdout_state:
                self._streams["stderr"].finish_control(self.stderr)
            if not stdout_state.line_start or interrupted_control:
                self.stdout.write("\n")
            stdout_state.line_start = True
            self.stdout.write(message.serialize() + "\n")
            self.stdout.flush()

    def message(self, text: str, flow: Optional[str]) -> None:
        attributes = {"text": text, "status": "NORMAL"}
        if flow is not None:
            attributes["flowId"] = flow
        self.record(ServiceMessage("message", attributes))

    def forward(self, text: str, stream: str) -> None:
        with self.lock:
            destination = self.stdout if stream == "stdout" else self.stderr
            destination.write(text)
            destination.flush()
            for char in text:
                self._streams[stream].consume(char)

    def sink(self, flow: Optional[str]) -> "TeamCitySink":
        return TeamCitySink(self, flow)


class TeamCitySink(StringIO):
    encoding = "utf-8"

    def __init__(self, writer: TeamCityWriter, flow: Optional[str]):
        super().__init__()
        self._writer = writer
        self._flow = flow

    def write(self, text: str) -> int:
        if text:
            self._writer.message(text, self._flow)
        return len(text)


def standard_writer() -> TeamCityWriter:
    return TeamCityWriter(sys.stdout, sys.stderr)
