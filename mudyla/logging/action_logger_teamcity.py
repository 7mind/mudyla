"""TeamCity blocks and action-scoped parallel child service messages."""

from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

from rich.console import Console
from rich.text import Text

from ..dag.graph import ActionKey
from .action_logger_simple import ActionLoggerSimple
from .formatters import OutputFormatter
from .formatters.failure import failure_details
from .teamcity import ChildMessages, ServiceMessage

if TYPE_CHECKING:
    from ..executor.engine import ActionResult


class ActionLoggerTeamCity(ActionLoggerSimple):
    receives_suppressed_output = True

    def __init__(self, action_keys: list[ActionKey], output: OutputFormatter, use_short_ids: bool,
                 *, parallel: bool):
        super().__init__(action_keys, output, use_short_ids)
        assert output.teamcity_writer is not None
        self._writer = output.teamcity_writer
        self._parallel = parallel
        self._flows = {key: f"{self._writer.run_prefix}-{index}" for index, key in enumerate(action_keys)}
        self._open: dict[ActionKey, str] = {}
        self._children: dict[tuple[ActionKey, str], ChildMessages] = {}

    def _flow(self, key: ActionKey) -> Optional[str]:
        return self._flows[key] if self._parallel else None

    def _emit_marker(self, action_key: ActionKey, text: Text) -> None:
        with self._writer.lock:
            self._flush_children(action_key)
            self._writer.message(text.plain, self._flow(action_key))

    def begin_action(self, action_key: ActionKey, command: list[str]) -> None:
        with self._writer.lock:
            name = self._identity(action_key).plain
            self._open[action_key] = name
            self._block("blockOpened", action_key, name)
            super().begin_action(action_key, command)

    def _block(self, event: str, key: ActionKey, name: str) -> None:
        attributes = {"name": name}
        flow = self._flow(key)
        if flow is not None:
            attributes["flowId"] = flow
        self._writer.record(ServiceMessage(event, attributes))

    def end_action(self, action_key: ActionKey) -> None:
        with self._writer.lock:
            self._flush_children(action_key)
            name = self._open.pop(action_key, None)
            if name is not None:
                self._block("blockClosed", action_key, name)

    def finalize(self) -> None:
        with self._writer.lock:
            for key in list(self._open):
                self.end_action(key)

    def _flush_children(self, key: ActionKey) -> None:
        for stream in ("stdout", "stderr"):
            parser = self._children.pop((key, stream), None)
            if parser is not None:
                parser.finish()

    def _native(self, key: ActionKey, message: ServiceMessage) -> None:
        action_flow = self._flows[key]

        def namespace(child: str) -> str:
            return action_flow + "/" + child.encode("utf-8", errors="surrogatepass").hex() if child else action_flow

        attributes = dict(message.attributes)
        flow = namespace(attributes.get("flowId", ""))
        attributes["flowId"] = flow
        if message.name == "flowStarted":
            parent = attributes.get("parent", "")
            if parent or flow != action_flow:
                attributes["parent"] = namespace(parent)
            else:
                attributes.pop("parent", None)
        self._writer.record(ServiceMessage(message.name, attributes))

    def write_output(self, action_key: ActionKey, text: str, stream: Literal["stdout", "stderr"]) -> None:
        with self._writer.lock:
            if not self._parallel:
                self._writer.forward(text, stream)
                return
            pair = action_key, stream
            if pair not in self._children:
                self._children[pair] = ChildMessages(
                    lambda value: self._writer.message(value, self._flows[action_key]),
                    lambda message: self._native(action_key, message))
            self._children[pair].write(text)

    def show_failure(self, action_key: ActionKey, result: "ActionResult", run_directory: Path, suppress_output: bool) -> None:
        flow = self._flow(action_key)
        console = Console(file=self._writer.sink(flow), color_system=None, highlight=False,
                          width=self._output.console.width)
        console.print(failure_details(result, run_directory))
