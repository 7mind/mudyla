"""TeamCity blocks and action-scoped parallel child service messages."""

from pathlib import Path
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Optional

from rich.console import Console
from rich.text import Text

from ..dag.graph import ActionKey
from .terminal_logger_simple import SimpleTerminalLogger
from .terminal_logger import LoggerMode
from .formatters.action import action_group_names
from .formatters.failure import failure_details
from .formatters.details import KeyValueRow
from .formatters.result import action_result_fields
from .teamcity import ChildMessages, ServiceMessage

if TYPE_CHECKING:
    from ..executor.engine import ActionResult, ExecutionResult


@dataclass(frozen=True)
class ChildFlow:
    parent: str
    envelope: tuple[str, str]


class TeamCityTerminalLogger(SimpleTerminalLogger):
    receives_suppressed_output = True

    MODE = LoggerMode.TEAMCITY

    def _initialize_actions(self) -> None:
        super()._initialize_actions()
        self._group_names = action_group_names(self._action_keys)
        assert self.output.teamcity_writer is not None
        self._writer = self.output.teamcity_writer
        self._parallel = self.parallel_execution
        self._flows = {key: f"{self._writer.run_prefix}-{index}" for index, key in enumerate(self._action_keys)}
        self._open: dict[ActionKey, str] = {}
        self._children: dict[tuple[ActionKey, str], ChildMessages] = {}
        self._child_blocks: dict[ActionKey, dict[str, list[str]]] = {}
        self._child_flows: dict[ActionKey, dict[str, ChildFlow]] = {}
        self._child_tests: dict[ActionKey, dict[str, str]] = {}
        self._service_messages_enabled = True
        self._disabled_flows: set[str] = set()

    def _flow(self, key: ActionKey) -> Optional[str]:
        return self._flows[key] if self._parallel else None

    def _emit_marker(self, action_key: ActionKey, text: Text) -> None:
        with self._writer.lock:
            self._flush_children(action_key)
            self._writer.message(text.plain, self._flow(action_key))

    def begin_action(self, action_key: ActionKey, command: list[str]) -> None:
        with self._writer.lock:
            name = self._group_names[action_key]
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
                self._finish_structures(action_key)
                self._block("blockClosed", action_key, name)

    def finalize(self) -> None:
        with self._writer.lock:
            for key in list(self._open):
                self.end_action(key)

    def _action_result_fields(self, result: "ExecutionResult") -> list[KeyValueRow]:
        return action_result_fields(result, self._action_order(), self._group_names)

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
        prepared = ServiceMessage(message.name, attributes)
        self._observe(key, prepared)
        self._writer.record(prepared)

    def _observe(self, key: ActionKey, message: ServiceMessage) -> None:
        flow = message.attributes.get("flowId", "")
        if message.name in {"enableServiceMessages", "disableServiceMessages"}:
            enabled = message.name == "enableServiceMessages"
            if "flowId" in message.attributes:
                if enabled:
                    self._disabled_flows.discard(flow)
                else:
                    self._disabled_flows.add(flow)
            else:
                self._service_messages_enabled = enabled
            return
        if not self._service_messages_enabled or flow in self._disabled_flows:
            return
        blocks = self._child_blocks.setdefault(key, {})
        flows = self._child_flows.setdefault(key, {})
        tests = self._child_tests.setdefault(key, {})
        name = message.attributes.get("name", "")
        if message.name == "blockOpened":
            blocks.setdefault(flow, []).append(name)
        elif message.name == "blockClosed":
            opened = blocks.get(flow, [])
            if name in opened:
                index = len(opened) - 1 - opened[::-1].index(name)
                del opened[index:]
        elif message.name == "testStarted":
            if flow in tests:
                self._finish_envelope(key, (flow, tests[flow]))
            tests[flow] = name
        elif message.name == "testFinished":
            self._finish_envelope(key, (flow, name))
            if tests.get(flow) == name:
                del tests[flow]
        elif message.name == "flowStarted":
            parent = message.attributes.get("parent", self._flow(key) or "")
            envelope = (flow, tests[flow]) if flow in tests else (
                (parent, tests[parent]) if parent in tests else flows[parent].envelope if parent in flows else None)
            if envelope is not None:
                flows[flow] = ChildFlow(parent, envelope)
        elif message.name == "flowFinished":
            for child in self._descendants(flows, flow):
                flows.pop(child, None)
                blocks.pop(child, None)
                tests.pop(child, None)

    @staticmethod
    def _descendants(flows: dict[str, ChildFlow], root: str) -> list[str]:
        descendants = [root]
        for parent in descendants:
            descendants.extend(flow for flow, child in flows.items() if child.parent == parent and flow not in descendants)
        return descendants

    def _finish_envelope(self, key: ActionKey, envelope: tuple[str, str]) -> None:
        flows = self._child_flows[key]
        for flow in list(flows):
            if flow in flows and flows[flow].envelope == envelope:
                self._finish_flow(key, flow)

    def _finish_flow(self, key: ActionKey, flow: str) -> None:
        flows = self._child_flows[key]
        blocks = self._child_blocks[key]
        for child in reversed(self._descendants(flows, flow)):
            for name in reversed(blocks.pop(child, [])):
                self._writer.record(ServiceMessage("blockClosed", {"name": name, "flowId": child}))
            if child in flows:
                self._writer.record(ServiceMessage("flowFinished", {"flowId": child}))
                del flows[child]
            self._child_tests[key].pop(child, None)

    def _finish_structures(self, key: ActionKey) -> None:
        flows = self._child_flows.get(key, {})
        for flow in list(flows):
            if flow in flows:
                self._finish_flow(key, flow)
        base = self._flow(key) or ""
        for flow, names in self._child_blocks.pop(key, {}).items():
            if flow != base:
                for name in reversed(names):
                    self._writer.record(ServiceMessage("blockClosed", {"name": name, "flowId": flow}))
        self._child_flows.pop(key, None)
        self._child_tests.pop(key, None)

    def write_output(self, action_key: ActionKey, text: str, stream: Literal["stdout", "stderr"]) -> None:
        with self._writer.lock:
            pair = action_key, stream
            if pair not in self._children:
                if self._parallel:
                    self._children[pair] = ChildMessages(
                        lambda value: self._writer.message(value, self._flows[action_key]),
                        lambda message: self._native(action_key, message), lambda raw: None)
                else:
                    self._children[pair] = ChildMessages(lambda value: self._writer.forward(value, stream),
                        lambda message: self._observe(action_key, message), lambda raw: self._writer.forward(raw, stream))
            self._children[pair].write(text)

    def show_failure(self, action_key: ActionKey, result: "ActionResult", run_directory: Path, suppress_output: bool) -> None:
        flow = self._flow(action_key)
        console = Console(file=self._writer.sink(flow), color_system=None, highlight=False,
                          width=self._output.console.width)
        console.print(failure_details(result, run_directory))
