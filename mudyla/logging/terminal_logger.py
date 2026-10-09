"""Whole-run terminal reporting, with mode-specific action presentation."""

from __future__ import annotations

import argparse
import json
import traceback
from abc import ABC, abstractmethod
from enum import Enum
import sys
from collections.abc import Mapping, Sequence, Set
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal, Optional, Protocol, cast

from rich.console import Console, Group, RenderableType
from rich.json import JSON
from rich.text import Text

from ..ast.models import ParsedDocument
from ..ast.expansions import ActionExpansion, ArgsExpansion, EnvExpansion, FlagsExpansion
from ..dag.compiler import CompilationError
from ..dag.display import build_display_edges
from ..dag.graph import ActionGraph, ActionKey
from ..dag.solver.model import DisplayEdges, SolverMode
from ..dag.validator import ValidationError
from .display_session import DisplaySession
from .formatters.branches import BranchTheme
from .formatters.dag import DagLayout, build_dag_layout, dag_section, execution_dag
from .formatters.details import JsonValue, KeyValueView, action_label, axis_field, contexts_view, duration_text, literal_text, output_view, summary_field
from .formatters.output import OutputFormatter, RunInfoField
from .formatters.plan import PlanStyle, execution_table, execution_tree, sharing_counts, tree_section
from .formatters.sections import section
from .formatters.symbols import StatusSymbol

if TYPE_CHECKING:
    from ..executor.engine import ActionResult, ExecutionResult
    from ..executor.retainer_executor import RetainerCompletion, RetainerRequest


class LoggerMode(str, Enum):
    PURE = "pure"
    TABLE = "table"
    SIMPLE = "simple"
    VERBOSE = "verbose"
    GITHUB = "github"
    TEAMCITY = "teamcity"

    @property
    def compact(self) -> bool:
        return self != LoggerMode.TABLE


def resolve_logger_mode(explicit: Optional[str], simple: bool, verbose: bool, github: bool, teamcity: bool) -> LoggerMode:
    if teamcity or explicit == "teamcity":
        if simple or github or explicit not in {None, "raw", "teamcity"}:
            raise ValueError("--teamcity conflicts with the selected logging option")
        return LoggerMode.TEAMCITY
    legacy = LoggerMode.GITHUB if github else LoggerMode.VERBOSE if verbose and explicit != "github" else LoggerMode.SIMPLE if simple else None
    if explicit is None or explicit == "raw":
        return legacy or (LoggerMode.SIMPLE if explicit == "raw" else LoggerMode.PURE)
    selected = LoggerMode(explicit)
    if legacy is not None and selected != legacy:
        raise ValueError("--logger conflicts with the selected legacy logging option")
    return selected


class PlanningReporter(Protocol):
    def report_project(self, project_root: Path, without_nix: bool) -> None: ...
    def report_default_axes(self, values: Mapping[str, str]) -> None: ...
    def report_definitions(self, markdown_files: Sequence[Path], document: ParsedDocument,
                           parallel_execution: bool, dry_run: bool) -> None: ...
    def report_compilation(self, graph: ActionGraph, compiler_elapsed_ms: float) -> None: ...
    def report_plan(self, graph: ActionGraph, execution_order: Sequence[ActionKey],
                    retained_targets: Set[ActionKey]) -> None: ...


class TerminalLogger(ABC):
    MODE: LoggerMode

    def __init__(self, *, no_color: bool, force_interactive: bool,
                 interactive: bool, fullscreen: bool, show_dirs: bool, use_short_ids: bool,
                 plan_style: PlanStyle, plan_minimize: bool, dag_solver: SolverMode | None,
                 console: Console | None) -> None:
        from .retainer_display import RetainerDisplay
        mode = self.MODE
        self.mode = mode
        self.output = OutputFormatter(no_color=no_color, plain=mode in {LoggerMode.GITHUB, LoggerMode.TEAMCITY},
                                      compact=mode.compact, teamcity=mode == LoggerMode.TEAMCITY,
                                      console=console, force_interactive=force_interactive)
        self.console = self.output.console
        self._plain_stdout = sys.stdout
        self._plain_stderr = sys.stderr
        self.no_color = self.output.no_color
        self._output = self.output
        self.session = DisplaySession(self.console) if mode in {LoggerMode.PURE, LoggerMode.TABLE} else None
        self.keep_running = interactive and self.session is not None and sys.stdin.isatty() and (
            force_interactive or self.console.is_terminal and not self.console.is_dumb_terminal)
        self.force_interactive = force_interactive
        self.fullscreen = fullscreen or self.keep_running
        self.show_dirs = show_dirs
        self.use_short_ids = use_short_ids
        self.plan_style = plan_style
        self.plan_minimize = plan_minimize
        self.dag_solver = dag_solver
        self.retainers = RetainerDisplay(self.output, mode, use_short_ids, session=self.session,
                                         fullscreen=fullscreen or self.keep_running)
        self.graph: ActionGraph | None = None
        self.execution_order: list[ActionKey] | None = None
        self.layout: DagLayout | None = None
        self.display: DisplayEdges | None = None
        self._actions_initialized = False
        self._project_root: Path | None = None
        self.static_plan: Group | None = None
        self.parallel_execution = False
        self.started = False
        self.finished = False
        self.run_id: str | None = None

    def report_help(self, parser: argparse.ArgumentParser) -> None:
        self._plain_stdout.write(parser.format_help())
        self._plain_stdout.flush()

    def report_usage_error(self, parser: argparse.ArgumentParser, error: argparse.ArgumentError) -> None:
        self._plain_stderr.write(parser.format_usage())
        self._plain_stderr.write(f"{parser.prog}: error: {error}\n")
        self._plain_stderr.flush()

    def report_completion(self, suggestions: Sequence[str]) -> None:
        for name in suggestions:
            self._plain_stdout.write(name + "\n")
        self._plain_stdout.flush()

    def report_traceback(self, error: Exception) -> None:
        self.finish_run()
        traceback.print_exception(type(error), error, error.__traceback__, file=self._plain_stderr)

    def start_run(self, run_id: str, nix_message: str | None) -> None:
        assert not self.started, "Run logger already started"
        self.started = True
        self.run_id = run_id
        self.output.start_recording(defer=True)
        self.output.print_run_field(RunInfoField.RUN_ID, Text(run_id, style="cyan"),
            f"{self.output.symbols.Id} [dim]Run ID:[/dim] [bold cyan]{run_id}[/bold cyan]")
        if nix_message is not None:
            self.output.print_run_field(RunInfoField.USING_NIX, Text(nix_message), f"Using Nix: {nix_message}")

    def report_project(self, project_root: Path, without_nix: bool) -> None:
        self._project_root = project_root
        self.output.print_run_field(RunInfoField.PROJECT_ROOT, literal_text(str(project_root), "cyan"),
            f"[dim]Project root:[/dim] [bold cyan]{self.output.escape(str(project_root))}[/bold cyan]")
        self.output.flush_recording()

    def report_default_axes(self, values: Mapping[str, str]) -> None:
        if values:
            self.output.print_run_field(RunInfoField.DEFAULT_AXES, Text(", ").join(
                axis_field(name, value) for name, value in values.items()), "")

    def report_definitions(self, markdown_files: Sequence[Path], document: ParsedDocument,
                           parallel_execution: bool, dry_run: bool) -> None:
        self.parallel_execution = parallel_execution
        label = "dry-run" if dry_run else "parallel" if parallel_execution else "sequential"
        self.output.print_run_field(RunInfoField.EXECUTION_MODE, Text(label, style="cyan"),
            f"\n{self.output.symbols.Gear} [dim]Execution mode:[/dim] [bold cyan]{label}[/bold cyan]")
        self.output.print_run_field(RunInfoField.DEFINITIONS, Text.assemble(
            (str(len(markdown_files)), "cyan not dim not bold"), " definition file(s) with ",
            (str(len(document.actions)), "cyan not dim not bold"), " actions"),
            f"{self.output.symbols.Book} [dim]Found[/dim] [bold]{len(markdown_files)}[/bold] "
            f"[dim]definition file(s) with[/dim] [bold]{len(document.actions)}[/bold] [dim]actions[/dim]")
        self.output.flush_recording()

    def report_warning(self, message: str) -> None:
        if self.output.recording_preparation:
            self.output.print_run_field(RunInfoField.WARNING, literal_text(message, "yellow not dim"),
                f"{self.output.symbols.Warning} [bold yellow]Warning:[/bold yellow] {self.output.escape(message)}")
        else:
            self.output.print_warning(message)

    def report_compilation(self, graph: ActionGraph, compiler_elapsed_ms: float) -> None:
        from .retainer_display import PlanningFacts
        self.retainers.set_planning_facts(PlanningFacts(compiler_elapsed_ms, frozenset(), None))
        contexts = {key.context_id for key in graph.nodes}
        ordered = [ctx for ctx in contexts if str(ctx) == "default"] + sorted(
            (ctx for ctx in contexts if str(ctx) != "default"), key=str)
        if ordered:
            self.output.print("")
            self.output.print(section("Contexts:", contexts_view(ordered, self.output.context, self.use_short_ids), None, None))
            self.output.print("")

    def start_retainers(self) -> None:
        assert self.retainers.facts is not None, "Compilation not reported"

    def begin_retainer(self, request: RetainerRequest) -> None:
        self.retainers.begin_retainer(request)

    def retainer_output(self, key: ActionKey, text: str, stream: Literal["stdout", "stderr"]) -> None:
        self.retainers.retainer_output(key, text, stream)

    def end_retainer(self, completion: RetainerCompletion) -> None:
        self.retainers.end_retainer(completion)

    def finish_retainers(self) -> None:
        history = self.retainers.finish()
        if self.retainers.rows:
            self.output.remember(history)
            if self.retainers.live:
                self.output.print_immediate(history)

    def report_goals(self, keys: Sequence[ActionKey]) -> None:
        if keys:
            self.output.print(section("Goals:", Group(*(action_label(key, self.output.context, self.use_short_ids, True)
                               for key in keys)), None, None))
            self.output.print("")

    def report_plan(self, graph: ActionGraph, execution_order: Sequence[ActionKey],
                    retained_targets: Set[ActionKey]) -> None:
        assert self.graph is None, "Final plan reported twice"
        self.graph = graph
        self.execution_order = list(execution_order)
        facts = self.retainers.complete_plan(len(execution_order))
        assert facts.retained_keys == frozenset(retained_targets)
        if self.plan_style == "dag":
            assert self.dag_solver is not None
            self.layout = build_dag_layout(graph, self.execution_order, mode=self.dag_solver, minimize=self.plan_minimize)
            self.display = self.layout.display
        elif self.plan_style == "tree":
            self.display = build_display_edges(graph, tuple(execution_order), full=not self.plan_minimize)
        self.static_plan = self._plan()

    def _initial_status(self, key: ActionKey) -> Text:
        assert self.graph is not None
        ready = not self.graph.get_node(key).dependencies
        glyph = self.output.symbols.status(StatusSymbol.READY if ready else StatusSymbol.WAITING, now=0)
        return Text(glyph + " ", style="cyan" if ready else "dim")

    def _plan(self) -> Group:
        assert self.graph is not None and self.execution_order is not None and self.retainers.facts is not None
        counts = sharing_counts(self.graph, self.execution_order, [key.id.name for key in self.graph.goals])
        toolbar = self.retainers.facts.summary()
        if self.plan_style == "table":
            return Group(section("Plan:", execution_table(self.graph, self.execution_order, self.output.context,
                self.use_short_ids, counts, self.console.options.ascii_only), toolbar, None), Text(""))
        if self.plan_style == "tree":
            assert self.display is not None
            tree = execution_tree(self.graph, self.execution_order, self.output.context, self.use_short_ids,
                                  counts, self._initial_status, display=self.display)
            return Group(tree_section(tree, self.output.symbols, toolbar=toolbar), Text(""))
        assert self.layout is not None
        dag = execution_dag(self.graph, self.execution_order, self.output.context, self.use_short_ids,
            counts, self._initial_status, lambda key: "dim", lambda: BranchTheme.DISABLED if self.output.no_color
            or self.console.no_color or self.console.color_system is None else BranchTheme.TERMINAL, layout=self.layout)
        return Group(dag_section(dag, self.output.symbols, toolbar=toolbar), Text(""))

    def report_dry_run(self) -> None:
        assert self.static_plan is not None
        self.output.print(self.static_plan)
        self.output.print_run_field(RunInfoField.EXECUTION, Text("Dry run - not executing"),
            f"\n{self.output.symbols.Info} [blue]Dry run - not executing[/blue]")

    def report_continuation(self, previous: Path | None, message: str | None) -> None:
        if previous is None:
            assert message is not None
            self.report_warning(message)
        else:
            self.output.print_run_field(RunInfoField.CONTINUATION, Text(previous.name, style="cyan"),
                f"\n{self.output.symbols.Refresh} [blue]Continuing from previous run:[/blue] [bold cyan]{previous.name}[/bold cyan]")

    def start_actions(self, run_directory: Path, action_dirs: Mapping[ActionKey, Path], *,
                      kill_callback: Callable[[], None],
                      input_callback: Callable[[ActionKey, Optional[str]], Optional[str]]) -> None:
        assert not self._actions_initialized and self.static_plan is not None and self.execution_order is not None
        assert self.retainers.facts is not None and self.retainers.finished and self._project_root is not None
        assert run_directory.name == self.run_id, "Execution directory differs from the reported run identity"
        if self.mode == LoggerMode.PURE:
            self.output.remember(self.static_plan)
        else:
            self.output.print(self.static_plan)
        self._run_info = self.output.stop_recording(
            exclude=self.static_plan if self.mode == LoggerMode.PURE else None, emit=False)
        self.run_directory = run_directory
        self.action_dirs_map = {
            self.output.action.format_label_plain(key, self.use_short_ids): str(path.relative_to(self._project_root))
            for key, path in action_dirs.items()}
        self.planning_summary = self.retainers.facts.summary()
        self._initialize_actions()
        self._actions_initialized = True
        self.set_kill_callback(kill_callback)
        self.set_input_callback(input_callback)
        self.start()

    @abstractmethod
    def _initialize_actions(self) -> None:
        """Initialize action state once from the authoritative final plan."""
        pass

    def _action_order(self) -> Sequence[ActionKey]:
        assert self.execution_order is not None
        return self.execution_order

    def report_execution_time(self, duration: float, restored: Sequence[ActionKey]) -> None:
        if self.mode != LoggerMode.GITHUB and not self.output.compact:
            if restored:
                labels = ", ".join(self.output.escape(str(key)) for key in restored)
                self.output.print(f"\n{self.output.symbols.Recycle} [dim]restored from previous run:[/dim] [bold cyan]{labels}[/bold cyan]")
            self.output.print(f"\n[dim]Total wall time:[/dim] [bold cyan]{duration:.1f}s[/bold cyan]")

    def report_outcome(self, result: "ExecutionResult", keep_run_dir: bool) -> None:
        self.finish_actions()
        if self.output.compact:
            fields = [summary_field("Outcome", Text("Execution completed successfully!" if result.success else "Execution failed!",
                                                   style="green" if result.success else "red"))]
            if result.duration_seconds is not None:
                fields.append(summary_field("Total wall time", Text(duration_text(result.duration_seconds), style="cyan not dim not bold")))
            restored = [action_label(key, self.output.context, self.use_short_ids, True)
                        for key, value in result.action_results.items() if value.restored]
            if restored:
                fields.append(summary_field("Restored", Text(", ").join(restored)))
            logs = str(result.run_directory) if keep_run_dir or not result.success else "use --keep-run-dir to retain artifacts"
            fields.append(summary_field("Logs", literal_text(logs)))
            self.output.print(Group(Text(""), section("Result:", KeyValueView(fields), None, None), Text("")))
        else:
            self.output.print(f"\n{self.output.symbols.Check if result.success else self.output.symbols.Cross} "
                f"[bold {'green' if result.success else 'red'}]Execution {'completed successfully' if result.success else 'failed'}![/bold {'green' if result.success else 'red'}]")

    def report_outputs(self, result: "ExecutionResult", selected: Sequence[ActionKey], *,
                       full_output: bool, out_path: Path | None) -> None:
        outputs = result.get_all_outputs(list(selected)) if full_output else result.get_goal_outputs(list(selected))
        serialized = json.dumps(outputs, indent=2)
        if self.output.compact:
            groups: list[RenderableType] = []
            for key in sorted(selected, key=str):
                value = result.action_results.get(key)
                if value is None or not value.outputs:
                    continue
                records: dict[str, JsonValue] = {}
                for name, content in value.outputs.items():
                    records[name] = ({"type": value.output_types[name], "value": cast(JsonValue, content)}
                                     if name in value.output_types else cast(JsonValue, content))
                if groups:
                    groups.append(Text(""))
                groups.append(section(action_label(key, self.output.context, self.use_short_ids, True), output_view(records), None, None))
            if groups:
                self.output.print(section("Outputs:", Group(*groups), None, None))
                self.output.print("")
        else:
            self.output.print(f"\n{self.output.symbols.Chart} [bold]Outputs:[/bold]")
            self.output.print(serialized if self.output.no_color else JSON(serialized))
        if out_path is not None:
            out_path.write_text(serialized, encoding="utf-8")
            self.output.print(f"\n{self.output.symbols.Save} [dim]Outputs saved to:[/dim] [bold cyan]{self.output.escape(str(out_path))}[/bold cyan]")

    def report_run_directory(self, directory: Path) -> None:
        if not self.output.compact:
            self.output.print(f"\n{self.output.symbols.Folder} [dim]Run directory:[/dim] [bold cyan]{self.output.escape(str(directory))}[/bold cyan]")

    def finish_actions(self) -> None:
        if self._actions_initialized:
            self.stop()

    def report_available_actions(self, document: ParsedDocument) -> None:
        """List all available actions."""
        output = self.output
        sym = output.symbols

        # Show available axes first
        if document.axis:
            output.print("\n[blue]Available axes:[/blue]\n")

            for axis_name in sorted(document.axis.keys()):
                axis_def = document.axis[axis_name]
                values_parts = []
                for axis_val in axis_def.values:
                    escaped_val = output.escape(axis_val.value)
                    if axis_val.is_default:
                        values_parts.append(f"[bold green]{escaped_val}[/bold green]*")
                    else:
                        values_parts.append(escaped_val)
                output.print(f"  [bold cyan]{output.escape(axis_name)}[/bold cyan]: {', '.join(values_parts)}")
            output.print("")

        output.print("[blue]Available actions:[/blue]\n")

        ordered = sorted(document.actions, key=lambda name: (bool(document.actions[name].get_typed_action_dependencies()), name))

        # Display root actions first, then non-root actions
        for action_name in ordered:
            action = document.actions[action_name]
            typed_deps = action.get_typed_action_dependencies()
            args_used: set[str] = set()
            flags_used: set[str] = set()
            all_env_vars = set(action.required_env_vars)
            inputs_map: dict[str, set[str]] = {}
            for expansion in action.get_all_expansions():
                if isinstance(expansion, ArgsExpansion):
                    args_used.add(expansion.argument_name)
                elif isinstance(expansion, FlagsExpansion):
                    flags_used.add(expansion.flag_name)
                elif isinstance(expansion, EnvExpansion):
                    all_env_vars.add(expansion.variable_name)
                elif isinstance(expansion, ActionExpansion):
                    inputs_map.setdefault(expansion.action_name, set()).add(expansion.variable_name)
            returns_map = {declaration.name: declaration for version in action.versions for declaration in version.return_declarations}
            all_returns = list(returns_map.values())
            is_root = len(typed_deps) == 0

            # Format action name
            escaped_action_name = output.escape(action_name)
            if is_root:
                output.print(f"{sym.Target} [bold cyan]{escaped_action_name}[/bold cyan]")
            else:
                output.print(f"  [bold cyan]{escaped_action_name}[/bold cyan]")

            if action.description:
                for desc_line in action.description.splitlines():
                    stripped_line = desc_line.strip()
                    if stripped_line:
                        output.print(f"    [dim]{output.escape(stripped_line)}[/dim]")

            if typed_deps:
                dep_strs = []
                for dep_name in sorted(typed_deps.keys()):
                    escaped_dep = output.escape(dep_name)
                    dep_type = typed_deps[dep_name]
                    if dep_type == "weak":
                        dep_strs.append(f"~{escaped_dep}")
                    elif dep_type == "soft":
                        dep_strs.append(f"?{escaped_dep}")
                    else:
                        dep_strs.append(escaped_dep)
                output.print(f"    [dim]Dependencies:[/dim] {', '.join(dep_strs)}")

            if args_used:
                escaped_args = ', '.join(output.escape(a) for a in sorted(args_used))
                output.print(f"    [dim]Arguments:[/dim] [bold yellow]{escaped_args}[/bold yellow]")

            if flags_used:
                escaped_flags = ', '.join(output.escape(f) for f in sorted(flags_used))
                output.print(f"    [dim]Flags:[/dim] [bold yellow]{escaped_flags}[/bold yellow]")

            if all_env_vars:
                escaped_env = ', '.join(output.escape(e) for e in sorted(all_env_vars))
                output.print(f"    [dim]Env vars:[/dim] {escaped_env}")

            if inputs_map:
                input_parts = []
                for act_name in sorted(inputs_map.keys()):
                    escaped_act = output.escape(act_name)
                    vars_str = ', '.join(output.escape(v) for v in sorted(inputs_map[act_name]))
                    input_parts.append(f"{escaped_act}.{{{vars_str}}}")
                output.print(f"    [dim]Inputs:[/dim] {', '.join(input_parts)}")

            if all_returns:
                returns_parts = []
                for r in all_returns:
                    escaped_name = output.escape(r.name)
                    escaped_type = output.escape(r.return_type.value)
                    returns_parts.append(f"[bold green]{escaped_name}[/bold green][dim]:{escaped_type}[/dim]")
                output.print(f"    [dim]Returns:[/dim] {', '.join(returns_parts)}")

            # Show versions if action has multiple versions
            if len(action.versions) > 1:
                from ..ast.models import AxisCondition, PlatformCondition
                version_strs = []
                for ver_i, version in enumerate(action.versions, 1):
                    cond_parts = []
                    for cond in version.conditions:
                        if isinstance(cond, AxisCondition):
                            escaped_axis = output.escape(cond.axis_name)
                            escaped_val = output.escape(cond.axis_value)
                            cond_parts.append(f"{escaped_axis}: {escaped_val}")
                        elif isinstance(cond, PlatformCondition):
                            escaped_plat = output.escape(cond.platform_value)
                            cond_parts.append(f"platform: {escaped_plat}")

                    if cond_parts:
                        version_strs.append(f"{ver_i} ({', '.join(cond_parts)})")
                    else:
                        version_strs.append(str(ver_i))

                output.print(f"    [dim]Versions:[/dim] {', '.join(version_strs)}")

            output.print("")


    def report_error(self, error: Exception) -> None:
        try:
            self.finish_run()
        except BaseException as cleanup_error:
            error.add_note(f"Run display cleanup failed: {cleanup_error}")
        title = "Validation error" if isinstance(error, ValidationError) else "Compilation error" if isinstance(error, CompilationError) else "Error"
        if isinstance(error, ValueError):
            self.output.print(f"{self.output.symbols.Cross} [bold red]Error:[/bold red] {self.output.escape(str(error))}")
        else:
            try:
                self.output.print(f"\n{self.output.symbols.Cross} [bold red]{title}:[/bold red]\n{self.output.escape(str(error))}")
            except (NameError, UnicodeEncodeError):
                print(f"\n[!] {title}: {error}")

    def finish_run(self) -> None:
        if not self.finished:
            self.finished = True
            try:
                try:
                    self.finish_actions()
                finally:
                    self.retainers.close()
            finally:
                self.output.flush_recording()


    receives_suppressed_output = False

    def begin_action(self, action_key: ActionKey, command: list[str]) -> None:
        pass

    def end_action(self, action_key: ActionKey) -> None:
        pass

    def finalize(self) -> None:
        """Finish remaining records after execution workers have stopped."""
        pass

    @abstractmethod
    def show_failure(self, action_key: ActionKey, result: "ActionResult", run_directory: Path, suppress_output: bool) -> None:
        pass

    @abstractmethod
    def mark_running(self, action_key: ActionKey, action_dir: Optional[Path] = None) -> None:
        """Mark an action as running.

        Args:
            action_key: The action key being started
            action_dir: Optional path to action directory
        """
        pass

    @abstractmethod
    def mark_done(self, action_key: ActionKey, duration: float) -> None:
        """Mark an action as done.

        Args:
            action_key: The action key that completed
            duration: Execution duration in seconds
        """
        pass

    @abstractmethod
    def mark_failed(self, action_key: ActionKey, duration: float) -> None:
        """Mark an action as failed.

        Args:
            action_key: The action key that failed
            duration: Execution duration in seconds
        """
        pass

    @abstractmethod
    def mark_restored(
        self, action_key: ActionKey, duration: float, action_dir: Optional[Path] = None
    ) -> None:
        """Mark an action as restored from previous run.

        Args:
            action_key: The action key that was restored
            duration: Original execution duration in seconds
            action_dir: Optional path to action directory
        """
        pass

    @abstractmethod
    def update_output_sizes(
        self, action_key: ActionKey, stdout_size: int, stderr_size: int
    ) -> None:
        """Update stdout and stderr sizes for an action.

        Args:
            action_key: The action key to update
            stdout_size: Size of stdout in bytes
            stderr_size: Size of stderr in bytes
        """
        pass

    def write_output(self, action_key: ActionKey, text: str, stream: Literal["stdout", "stderr"]) -> None:
        """Receive captured output; file-backed viewers need no additional delivery."""
        pass

    def uses_terminal_input(self) -> bool:
        return False

    def set_input_callback(self, callback: Callable[[ActionKey, Optional[str]], Optional[str]]) -> None:
        pass

    def report_input_error(self, action_key: ActionKey, message: str) -> None:
        pass

    @abstractmethod
    def set_kill_callback(self, callback: Callable[[], None]) -> None:
        """Set callback to be called when user requests kill.

        Args:
            callback: Function to call to terminate running processes
        """
        pass

    @abstractmethod
    def is_kill_requested(self) -> bool:
        """Check if user has requested to kill execution.

        Returns:
            True if kill was requested
        """
        pass

    @abstractmethod
    def start(self) -> None:
        """Start the logger display."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Stop the logger display."""
        pass

    @abstractmethod
    def wait_for_quit(self) -> None:
        """Wait for user to quit (for interactive modes with --it flag)."""
        pass

def create_terminal_logger(mode: LoggerMode, *, no_color: bool, force_interactive: bool,
                           interactive: bool, fullscreen: bool, show_dirs: bool, use_short_ids: bool,
                           plan_style: PlanStyle, plan_minimize: bool, dag_solver: SolverMode | None,
                           console: Console | None) -> TerminalLogger:
    from .terminal_logger_github import GitHubTerminalLogger
    from .terminal_logger_simple import SimpleTerminalLogger
    from .terminal_logger_teamcity import TeamCityTerminalLogger
    from .terminal_logger_verbose import VerboseTerminalLogger
    from .terminal_logger_table import TableTerminalLogger
    from .terminal_logger_pure import PureTerminalLogger
    implementations: dict[LoggerMode, type[TerminalLogger]] = {
        LoggerMode.PURE: PureTerminalLogger, LoggerMode.TABLE: TableTerminalLogger,
        LoggerMode.SIMPLE: SimpleTerminalLogger, LoggerMode.VERBOSE: VerboseTerminalLogger,
        LoggerMode.GITHUB: GitHubTerminalLogger, LoggerMode.TEAMCITY: TeamCityTerminalLogger}
    return implementations[mode](no_color=no_color, force_interactive=force_interactive,
        interactive=interactive, fullscreen=fullscreen, show_dirs=show_dirs, use_short_ids=use_short_ids,
        plan_style=plan_style, plan_minimize=plan_minimize, dag_solver=dag_solver, console=console)
