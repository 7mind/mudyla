"""Retainer progress and completed decisions on the run's display session."""

import threading
import time
from dataclasses import dataclass, field, replace
from typing import Literal

from rich.console import Group, RenderableType
from rich.text import Text

from ..dag.graph import ActionKey
from ..executor.retainer_executor import RetainerCompletion, RetainerOutcome, RetainerRequest
from .terminal_logger import LoggerMode
from .display_session import DisplaySession
from .formatters.details import action_label
from .formatters.output import OutputFormatter
from .formatters.sections import section
from .formatters.symbols import StatusSymbol
from .terminal_output import LatestLine

PLANNING_REFRESH_SECONDS = 1 / 24
PLANNING_STOP_SECONDS = 1.0


@dataclass(frozen=True)
class PlanningFacts:
    compiler_elapsed_ms: float
    retained_keys: frozenset[ActionKey]
    action_count: int | None

    def summary(self) -> Text:
        retained = f"{len(self.retained_keys)} retained"
        if self.action_count is None:
            return Text(f"{retained}, planning took {self.compiler_elapsed_ms:.0f}ms", style="dim")
        actions = "action" if self.action_count == 1 else "actions"
        return Text(f"{self.action_count} {actions} with {retained}, planned in {self.compiler_elapsed_ms:.0f}ms", style="dim")


@dataclass
class RetainerRow:
    request: RetainerRequest
    completion: RetainerCompletion | None
    latest: str
    stream: Literal["stdout", "stderr"]
    stdout: LatestLine = field(default_factory=LatestLine)
    stderr: LatestLine = field(default_factory=LatestLine)

    def receive(self, text: str, stream: Literal["stdout", "stderr"]) -> None:
        state = self.stdout if stream == "stdout" else self.stderr
        latest = state.consume(state.plain(text))
        if latest is not None:
            self.latest = latest
        self.stream = stream


class RetainerDisplay:
    def __init__(self, output: OutputFormatter, mode: LoggerMode, use_short_ids: bool,
                 *, session: DisplaySession | None, fullscreen: bool) -> None:
        self.output = output
        self.mode = mode
        self.use_short_ids = use_short_ids
        self.session = session
        self.fullscreen = fullscreen
        self.rows: dict[ActionKey, RetainerRow] = {}
        self.facts: PlanningFacts | None = None
        self.lock = threading.RLock()
        self.stop_refresh = threading.Event()
        self.refresh_thread: threading.Thread | None = None
        self.display_error: BaseException | None = None
        self.finished = False
        self.closed = False
        self._append_title_printed = False
        self.live = (session is not None and output.console.is_terminal
                     and not output.console.is_dumb_terminal)
        if session is not None:
            assert session.console is output.console

    def _check_error(self) -> None:
        if self.display_error is not None:
            raise self.display_error

    def set_planning_facts(self, facts: PlanningFacts) -> None:
        assert self.facts is None, "Compiler facts already supplied"
        self.facts = facts

    def complete_plan(self, action_count: int) -> PlanningFacts:
        assert self.facts is not None, "Compiler facts not supplied"
        self.facts = replace(self.facts, action_count=action_count)
        return self.facts

    def begin_retainer(self, request: RetainerRequest) -> None:
        with self.lock:
            self._check_error()
            assert request.key not in self.rows, "Retainer started twice"
            self.rows[request.key] = RetainerRow(request, None, "", "stdout")
            if self.live and self.refresh_thread is None:
                self.refresh_thread = threading.Thread(target=self._refresh_loop, name="retainer-display", daemon=True)
                self.refresh_thread.start()
            self._refresh()

    def retainer_output(self, key: ActionKey, text: str, stream: Literal["stdout", "stderr"]) -> None:
        with self.lock:
            self._check_error()
            row = self.rows[key]
            assert row.completion is None, "Output after retainer completion"
            row.receive(text, stream)
            self._refresh()

    def end_retainer(self, completion: RetainerCompletion) -> None:
        with self.lock:
            self._check_error()
            row = self.rows[completion.request.key]
            assert row.completion is None, "Retainer completed twice"
            row.completion = completion
            if completion.outcome != RetainerOutcome.SUCCEEDED and completion.result.stderr:
                row.stderr = LatestLine()
                row.latest = ""
                row.receive(completion.result.stderr, "stderr")
            if self.facts is not None:
                retained = frozenset(decision.target for decision in completion.decisions if decision.retained)
                self.facts = replace(self.facts, retained_keys=self.facts.retained_keys | retained)
            if self.live:
                self._refresh()
            else:
                self.output.print_immediate(self._pure_section([row], include_heading=not self._append_title_printed))
                self._append_title_printed = True

    def _parent(self, row: RetainerRow) -> tuple[Text, Text]:
        if row.completion is None:
            symbol, style = StatusSymbol.RUNNING, "yellow"
            elapsed_ms = (time.perf_counter() - row.request.started_perf_counter) * 1000
        else:
            succeeded = row.completion.outcome == RetainerOutcome.SUCCEEDED
            symbol, style = (StatusSymbol.DONE, "green") if succeeded else (StatusSymbol.FAILED, "red")
            elapsed_ms = row.completion.result.execution_time_ms
        label = Text()
        label.append(self.output.symbols.status(symbol, now=time.time()) + " ", style=style)
        label.append_text(action_label(row.request.key, self.output.context, self.use_short_ids, True))
        return label, Text(f"{elapsed_ms:.0f}ms", style="dim")

    def _failure_lines(self, row: RetainerRow) -> list[Text]:
        if row.completion is None or row.completion.outcome == RetainerOutcome.SUCCEEDED:
            return []
        lines = []
        for value, style in ((row.completion.result.stdout, ""), (row.completion.result.stderr, "red")):
            for line in value.splitlines():
                text = Text()
                text.append("    | ", style="dim")
                text.append_text(self.output.display_text(line, style))
                lines.append(text)
        return lines

    def _pure_section(self, rows: list[RetainerRow], *, include_heading: bool) -> Group:
        rendered: list[RenderableType] = []
        for row in rows:
            label, elapsed = self._parent(row)
            label.append("  ")
            label.append_text(elapsed)
            label.append(": ")
            label.append_text(self.output.latest_message(row.latest, row.stream))
            rendered.append(label)
            rendered.extend(self._failure_lines(row))
            decisions = ({decision.target: decision.retained for decision in row.completion.decisions}
                         if row.completion is not None else {})
            for target, retained in decisions.items():
                marker = self.output.symbols.status(StatusSymbol.DONE if retained else StatusSymbol.READY, now=0)
                child = Text("  ")
                child.append(marker + " ", style="green" if retained else "dim")
                child.append("retained " if retained else "ignored ")
                child.append_text(action_label(target, self.output.context, self.use_short_ids, True))
                rendered.append(child)
        body = Group(*rendered)
        return Group(section("Retainers:", body, None, None) if include_heading else body, Text(""))

    def _table_section(self) -> Group:
        table = self.output.table()
        for name in ("Retainer", "Status", "Elapsed", "Result"):
            table.add_column(name, overflow="fold")
        for row in self.rows.values():
            label, elapsed = self._parent(row)
            if row.completion is None:
                status = Text("Running", style="yellow")
                result: RenderableType = self.output.latest_message(row.latest, row.stream)
            else:
                status = Text(row.completion.outcome.value, style="green" if row.completion.outcome == RetainerOutcome.SUCCEEDED else "red")
                retained = [action_label(target, self.output.context, self.use_short_ids, True)
                            for target in dict.fromkeys(decision.target for decision in row.completion.decisions if decision.retained)]
                result = (Group(self.output.latest_message(row.latest, row.stream), *self._failure_lines(row))
                          if row.completion.outcome != RetainerOutcome.SUCCEEDED else
                          Text(", ").join(retained) if retained else self.output.latest_message("", "stdout"))
            table.add_row(label, status, elapsed, result)
        return Group(section("Retainers:", table, None, None), Text(""))

    def render(self) -> Group:
        with self.lock:
            progress = self._table_section() if self.mode == LoggerMode.TABLE else self._pure_section(list(self.rows.values()), include_heading=True)
            if self.facts is not None:
                progress = Group(section("Plan:", self.facts.summary(), None, None), Text(""), progress)
            return Group(self.output.preparation_snapshot(), progress) if self.fullscreen else progress

    def _refresh(self) -> None:
        if self.live:
            assert self.session is not None
            self.session.update(self.render(), fullscreen=self.fullscreen)

    def _refresh_loop(self) -> None:
        try:
            while not self.stop_refresh.wait(PLANNING_REFRESH_SECONDS):
                with self.lock:
                    self._refresh()
        except BaseException as error:
            self.display_error = error
            self.stop_refresh.set()

    def finish(self) -> Group:
        if not self.finished:
            self.stop_refresh.set()
            if self.refresh_thread is not None:
                self.refresh_thread.join(PLANNING_STOP_SECONDS)
                if self.refresh_thread.is_alive():
                    raise RuntimeError("Retainer display did not stop")
            self.finished = True
            self._check_error()
            if self.session is not None:
                self.session.clear()
        history = (self._table_section() if self.live and self.mode == LoggerMode.TABLE else
                   self._pure_section(list(self.rows.values()), include_heading=True)) if self.rows else Group()
        return history

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.finish()
        finally:
            if self.session is not None:
                self.session.close(discard=True)
