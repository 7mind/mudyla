"""Incremental framing for GitHub group and command-processing records."""

from dataclasses import dataclass
from typing import Callable, Literal, Optional


MAX_WORKFLOW_COMMAND_CHARACTERS = 1024 * 1024
REGISTERED_COMMANDS = frozenset({"stop-commands", "set-env", "set-output", "save-state", "add-mask", "add-path",
                               "add-matcher", "remove-matcher", "debug", "warning", "error", "notice",
                               "group", "endgroup", "echo"})


def escape_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def resume_command(token: str) -> str:
    record = "::" + token + "::"
    end = record.find("::", 2)
    name = record[2:end].split(" ", 1)[0]
    if not token or "\r" in token or "\n" in token or name.lower() != token.lower():
        raise ValueError("GitHub stop-commands token cannot be resumed with a workflow command")
    return record


@dataclass(frozen=True)
class WorkflowCommand:
    name: str
    data: str
    raw: str


class ChildCommands:
    """Deliver ordinary fragments immediately; retain only possible command records."""

    def __init__(self, ordinary: Callable[[str], None], command: Callable[[WorkflowCommand], None],
                 resume_token: Callable[[], Optional[str]]):
        self._ordinary = ordinary
        self._command = command
        self._resume_token = resume_token
        self._candidate: list[str] = []
        self._kind: Literal["v1", "v2"] = "v2"
        self._header_end: Optional[int] = None
        self._leading = True
        self._pending_cr = False
        self._ordinary_line = False

    def _recognized(self, name: str) -> bool:
        token = self._resume_token()
        return name.lower() in {"group", "endgroup", "stop-commands"} or (
            token is not None and name.lower() == token.lower())

    def _header(self) -> tuple[str, int] | None:
        value = "".join(self._candidate)
        start = 2 if self._kind == "v2" else 3
        end = value.find("::", start) if self._kind == "v2" else value.find("]", start)
        if end < 0:
            return None
        return value[start:end].split(" ", 1)[0], end + (2 if self._kind == "v2" else 1)

    def _literal(self, text: str) -> None:
        self._ordinary(text)
        for char in text:
            if char in "\r\n":
                self._leading = True
            elif not char.isspace():
                self._leading = False

    def _complete(self) -> None:
        raw = "".join(self._candidate)
        header = self._header()
        self._candidate.clear()
        self._header_end = None
        self._pending_cr = False
        if header is None or not self._recognized(header[0]):
            self._literal(raw)
            return
        name, offset = header
        data = raw[offset:].removesuffix("\n").removesuffix("\r")
        mappings = [("%0D", "\r"), ("%0A", "\n")]
        if self._kind == "v1":
            mappings = [("%3B", ";"), *mappings, ("%5D", "]")]
        for escaped, literal in [*mappings, ("%25", "%")]:
            data = data.replace(escaped, literal)
        self._command(WorkflowCommand(name.lower(), data, raw))
        self._leading = raw.endswith(("\r", "\n"))

    def write(self, text: str) -> None:
        ordinary: list[str] = []

        def flush() -> None:
            if ordinary:
                self._literal("".join(ordinary))
                ordinary.clear()

        for char in text:
            if self._pending_cr:
                if char == "\n":
                    self._candidate.append(char)
                    self._complete()
                    continue
                self._complete()
            if self._ordinary_line:
                ordinary.append(char)
                if char in "\r\n":
                    self._ordinary_line = False
                    flush()
                continue
            if self._candidate:
                self._candidate.append(char)
                if len(self._candidate) > MAX_WORKFLOW_COMMAND_CHARACTERS:
                    raise ValueError("GitHub child workflow command exceeds the 1,048,576-character transport limit")
                prefix = "::" if self._kind == "v2" else "##["
                if len(self._candidate) <= len(prefix):
                    while self._candidate and not prefix.startswith("".join(self._candidate)):
                        ordinary.append(self._candidate.pop(0))
                        self._leading = False
                elif char == "\r":
                    self._pending_cr = True
                elif char == "\n":
                    self._complete()
                elif self._header_end is None:
                    header = self._header()
                    if header is not None:
                        if self._recognized(header[0]):
                            self._header_end = header[1]
                        else:
                            ordinary.extend(self._candidate)
                            self._candidate.clear()
                            self._leading = False
                            self._ordinary_line = self._kind == "v1" or header[0].lower() in REGISTERED_COMMANDS
                continue
            if char == "#" or (char == ":" and self._leading):
                flush()
                self._kind = "v1" if char == "#" else "v2"
                self._candidate.append(char)
            else:
                ordinary.append(char)
                if char in "\r\n":
                    flush()
                elif not char.isspace():
                    self._leading = False
        flush()

    def finish(self) -> None:
        if self._candidate:
            self._complete()
