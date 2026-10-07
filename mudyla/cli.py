"""Command-line interface for Mudyla."""

import argparse
import json
import os
import platform
import sys
import time
from dataclasses import dataclass
from glob import glob
from pathlib import Path
from typing import Optional, cast
from rich.console import Group, RenderableType
from rich.text import Text

from .ast.models import ParsedDocument, ActionDefinition
from .dag.compiler import DAGCompiler, CompilationError
from .dag.graph import ActionGraph, ActionKey
from .dag.context import ContextId
from .dag.validator import DAGValidator, ValidationError
from .executor.engine import ExecutionEngine
from .logging.action_logger import LoggerMode, resolve_logger_mode
from .executor.retainer_executor import RetainerExecutor, RetainerResult
from .parser.markdown_parser import MarkdownParser
from .cli_args import (
    AXIS_OPTIONS,
    ActionInvocation,
    ArgValue,
    CLIParseError,
    parse_custom_inputs,
    ParsedCLIInputs,
)
from .cli_builder import build_arg_parser
from .axis_wildcards import expand_all_wildcards
from .utils.project_root import find_project_root
from .logging.formatters import OutputFormatter
from .logging.formatters.details import JsonValue, KeyValueView, action_label, axis_field, contexts_view, duration_text, literal_text, output_view, summary_field
from .logging.formatters.plan import PlanStyle, execution_table, execution_tree, sharing_counts, tree_section
from .logging.formatters.dag import dag_section, execution_dag
from .logging.formatters.sections import section
from .ast.expansions import ArgsExpansion, FlagsExpansion, EnvExpansion, ActionExpansion


@dataclass(frozen=True)
class ExecutionSetup:
    """Prepared state required to run the engine."""

    document: ParsedDocument
    project_root: Path
    markdown_files: list[Path]
    goals: list[str]
    custom_args: dict[str, str]
    axis_values: dict[str, str]
    all_flags: dict[str, bool]
    parsed_inputs: ParsedCLIInputs


class CLI:
    """Command-line interface for Mudyla."""

    def __init__(self):
        self.parser = build_arg_parser()

    def run(self, argv: Optional[list[str]] = None) -> int:
        """Run the CLI.

        Args:
            argv: Command-line arguments (defaults to sys.argv[1:])

        Returns:
            Exit code
        """
        args, unknown = self.parser.parse_known_args(argv)
        quiet_mode = args.autocomplete is not None
        nix_message = self._apply_platform_defaults(args, quiet_mode)

        if args.autocomplete:
            return self._handle_autocomplete(args)

        output = (OutputFormatter(no_color=args.no_color, plain=True, compact=True, teamcity=True)
                  if args.logger == "teamcity" else
                  self._build_formatters(args.no_color, args.logger == "github", LoggerMode(args.logger).compact))
        record_preparation = output.compact or args.logger == "table"
        if record_preparation:
            output.start_recording(defer=True)
        if nix_message is not None:
            output.print_run_field("Using Nix", nix_message, f"Using Nix: {nix_message.plain}")

        try:
            # All arguments (goals, axes, args, flags) are in 'unknown' since we don't
            # define a positional 'goals' parameter in argparse (to preserve order)
            parsed_inputs = parse_custom_inputs([], unknown)
        except CLIParseError as e:
            sym = output.symbols
            output.print(f"{sym.Cross} [bold red]Error:[/bold red] {output.escape(str(e))}")
            output.flush_recording()
            return 1

        sym = output.symbols
        try:
            setup = self._prepare_execution_setup(args, parsed_inputs, output)
            self._validate_required_env(setup.document)

            document = setup.document
            goals = setup.goals
            custom_args = setup.custom_args
            axis_values = setup.axis_values
            all_flags = setup.all_flags
            project_root = setup.project_root

            if args.list_actions:
                self._list_actions(document, output)
                return 0

            parallel_execution = args.parallel or (
                not args.sequential
                and args.logger not in {"verbose", "github", "teamcity"}
                and not document.properties.sequential_execution_default
            )

            output.print_run_field("Definitions", Text.assemble(
                (str(len(setup.markdown_files)), "cyan not dim not bold"), " definition file(s) with ",
                (str(len(document.actions)), "cyan not dim not bold"), " actions"),
                f"{sym.Book} [dim]Found[/dim] [bold]{len(setup.markdown_files)}[/bold] "
                f"[dim]definition file(s) with[/dim] [bold]{len(document.actions)}[/bold] [dim]actions[/dim]"
            )

            for warning in setup.parsed_inputs.goal_warnings:
                output.print_run_field("Warning", literal_text(warning, "yellow not dim"),
                                       f"{sym.Warning} [bold yellow]Warning:[/bold yellow] {output.escape(warning)}")

            # Use the new compiler for multi-context support
            planning_start = time.perf_counter()
            compiler = DAGCompiler(document, setup.parsed_inputs)
            compiler.validate_action_invocations()
            graph = compiler.compile()
            planning_elapsed_ms = (time.perf_counter() - planning_start) * 1000

            use_short_ids = not args.full_ctx_reprs

            # Get unique contexts, with default context first
            all_contexts = {ak.context_id for ak in graph.nodes.keys()}
            default_ctx = [ctx for ctx in all_contexts if str(ctx) == "default"]
            other_contexts = sorted([ctx for ctx in all_contexts if str(ctx) != "default"], key=str)
            unique_contexts = default_ctx + other_contexts

            self._print_contexts(unique_contexts, output, use_short_ids)
            self._print_goals(sorted(graph.goals, key=str), output, use_short_ids)

            # Execute retainers for soft dependencies
            retainer_executor = RetainerExecutor(
                graph=graph,
                document=document,
                project_root=project_root,
                environment_vars=document.environment_vars,
                passthrough_env_vars=document.passthrough_env_vars,
                args=custom_args,
                flags=all_flags,
                axis_values=axis_values,
                without_nix=args.without_nix,
                verbose=args.verbose,
            )
            retained_soft_targets, retainer_results = retainer_executor.execute_retainers()
            self._print_retainer_results(retainer_results, output, use_short_ids, args.verbose)

            pruned_graph = graph.prune_to_goals(retained_soft_targets)

            # Show execution mode
            if not quiet_mode:
                mode_label = "dry-run" if args.dry_run else ("parallel" if parallel_execution else "sequential")
                output.print_run_field("Execution mode", Text(mode_label, style="cyan"),
                                       f"\n{sym.Gear} [dim]Execution mode:[/dim] [bold cyan]{mode_label}[/bold cyan]")

            validator = DAGValidator(document, pruned_graph)
            validator.validate_all(custom_args, all_flags, axis_values)
            if not quiet_mode:
                output.print_run_field("Built plan graph", Text.assemble(
                    (str(len(pruned_graph.nodes)), "cyan not dim not bold"), " required action(s) (planning took ",
                    (f"{planning_elapsed_ms:.0f}ms", "cyan not dim not bold"), ")"),
                    f"{sym.Check} [dim]Built plan graph with[/dim] [bold]{len(pruned_graph.nodes)}[/bold] "
                    f"[dim]required action(s) (planning took {planning_elapsed_ms:.0f}ms)[/dim]"
                )

            execution_order = pruned_graph.get_execution_order()
            static_plan = None
            if not quiet_mode:
                static_plan = self._visualize_execution_plan(pruned_graph, execution_order, goals, output, use_short_ids, args.plan_style)

            if args.dry_run:
                output.print_run_field("Execution", Text("Dry run - not executing"),
                                       f"\n{sym.Info} [blue]Dry run - not executing[/blue]")
                return 0

            previous_run_dir = self._get_previous_run_dir(project_root, output) if args.continue_run else None
            keep_running = (
                args.interactive
                and args.logger in {"pure", "table"}
                and sys.stdin.isatty()
                and (args.force_interactive or (sys.stdout.isatty() and os.environ.get("TERM") not in {"dumb", "unknown"}))
            )

            engine = ExecutionEngine(
                graph=pruned_graph,
                project_root=project_root,
                args=custom_args,
                flags=all_flags,
                environment_vars=document.environment_vars,
                passthrough_env_vars=document.passthrough_env_vars,
                previous_run_directory=previous_run_dir,
                without_nix=args.without_nix,
                no_output_on_fail=args.no_out_on_fail,
                keep_run_dir=args.keep_run_dir or keep_running,
                no_color=args.no_color,
                logger_mode=LoggerMode(args.logger),
                plan_style=args.plan_style,
                force_interactive=args.force_interactive,
                show_dirs=args.show_dirs,
                parallel_execution=parallel_execution,
                use_short_context_ids=use_short_ids,
                keep_running=keep_running,
                timeout_ms=args.timeout_ms,
                output=output,
            )

            # Print run ID
            run_id = engine.run_directory.name
            output.print_run_field("Run ID", Text(run_id, style="cyan"),
                                   f"\n{sym.Id} [dim]Run ID:[/dim] [bold cyan]{run_id}[/bold cyan]")
            if record_preparation:
                engine.run_info = output.stop_recording(exclude=static_plan if args.logger == "pure" else None)

            result = engine.execute_all()

            if output.compact:
                fields = [summary_field("Outcome", Text("Execution completed successfully!" if result.success else "Execution failed!",
                                                        style="green" if result.success else "red"))]
                if result.duration_seconds is not None:
                    fields.append(summary_field("Total wall time", Text(duration_text(result.duration_seconds), style="cyan not dim not bold")))
                restored = [action_label(key, output.context, use_short_ids, True) for key, value in result.action_results.items() if value.restored]
                if restored:
                    fields.append(summary_field("Restored", Text(", ").join(restored)))
                logs = str(result.run_directory) if args.keep_run_dir or not result.success else "use --keep-run-dir to retain artifacts"
                fields.append(summary_field("Logs", literal_text(logs)))
                output.print(Group(Text(""), section("Result:", KeyValueView(fields), None, None), Text("")))

            if not result.success:
                if not output.compact:
                    output.print(f"\n{sym.Cross} [bold red]Execution failed![/bold red]")
                return 1

            # Get outputs using ActionKeys (with context) instead of just action names
            if args.full_output:
                outputs_to_report = result.get_all_outputs(pruned_graph.nodes.keys())
            else:
                outputs_to_report = result.get_goal_outputs(graph.goals)

            if not output.compact:
                output.print(f"\n{sym.Check} [bold green]Execution completed successfully![/bold green]")
            presented_outputs = None
            if output.compact:
                selected = pruned_graph.nodes.keys() if args.full_output else graph.goals
                groups: list[RenderableType] = []
                for key in sorted(selected, key=str):
                    action_result = result.action_results.get(key)
                    if action_result is None or not action_result.outputs:
                        continue
                    records: dict[str, JsonValue] = {}
                    for name, value in action_result.outputs.items():
                        records[name] = ({"type": action_result.output_types[name], "value": cast(JsonValue, value)}
                                         if name in action_result.output_types else cast(JsonValue, value))
                    if groups:
                        groups.append(Text(""))
                    groups.append(section(action_label(key, output.context, use_short_ids, True), output_view(records), None, None))
                presented_outputs = Group(*groups)
            self._print_outputs(outputs_to_report, output, args.no_color, args.out, presented_outputs)

            if args.keep_run_dir and not output.compact:
                output.print(f"\n{sym.Folder} [dim]Run directory:[/dim] [bold cyan]{result.run_directory}[/bold cyan]")

            # Clean up run directory after --it mode (unless --keep-run-dir)
            if keep_running and not args.keep_run_dir and result.run_directory.exists():
                import shutil
                try:
                    shutil.rmtree(result.run_directory)
                except Exception:
                    pass

            return 0

        except ValueError as err:
            output.print(f"{sym.Cross} [bold red]Error:[/bold red] {output.escape(str(err))}")
            if "No goals specified" in str(err):
                self.parser.print_help()
            return 1
        except ValidationError as validation_err:
            try:
                output.print(f"\n{sym.Cross} [bold red]Validation error:[/bold red]\n{output.escape(str(validation_err))}")
            except (NameError, UnicodeEncodeError):
                print(f"\n[!] Validation error:\n{validation_err}")
            return 1
        except CompilationError as comp_err:
            try:
                output.print(f"\n{sym.Cross} [bold red]Compilation error:[/bold red]\n{output.escape(str(comp_err))}")
            except (NameError, UnicodeEncodeError):
                print(f"\n[!] Compilation error:\n{comp_err}")
            return 1
        except Exception as gen_err:
            try:
                output.print(f"\n{sym.Cross} [bold red]Error:[/bold red] {output.escape(str(gen_err))}")
            except (NameError, UnicodeEncodeError):
                print(f"\n[!] Error: {gen_err}")
            import traceback

            traceback.print_exc()
            return 1
        finally:
            output.flush_recording()

    def _validate_required_env(self, document: ParsedDocument) -> None:
        """Validate that all required environment variables are set."""
        missing_vars = [var for var in document.required_env_vars if var not in os.environ]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

    def _print_contexts(
        self,
        contexts: list[ContextId],
        output: OutputFormatter,
        use_short_ids: bool,
    ) -> None:
        """Print the list of execution contexts.

        Args:
            contexts: List of ContextId objects
            output: Output formatter
            use_short_ids: Whether to use short context IDs
        """
        if not contexts:
            return

        sym = output.symbols
        if output.compact or output.recording_preparation:
            output.print("")
            output.print(section("Contexts:", contexts_view(contexts, output.context, use_short_ids), None, None))
            output.print("")
            return
        output.print(f"\n{sym.Link} [bold]Contexts:[/bold]")

        formatted_ids = [
            output.action.context.format_id_with_symbol(ctx, use_short_ids)
            for ctx in contexts
        ]
        max_id_len = max(len(fid.plain) for fid in formatted_ids) if formatted_ids else 0

        for ctx, formatted_id in zip(contexts, formatted_ids):
            padding = " " * (max_id_len - len(formatted_id.plain))
            ctx_line = Text("  ")
            ctx_line.append_text(formatted_id)
            ctx_line.append(padding + " : ")
            ctx_line.append_text(output.action.context.format_full(ctx))
            output.print(ctx_line)

    def _print_goals(
        self,
        goal_keys: list,
        output: OutputFormatter,
        use_short_ids: bool,
    ) -> None:
        """Print the list of goal actions.

        Args:
            goal_keys: List of ActionKey objects representing goals
            output: Output formatter
            use_short_ids: Whether to use short context IDs
        """
        if not goal_keys:
            return

        sym = output.symbols
        if output.compact or output.recording_preparation:
            output.print(section("Goals:", Group(*(action_label(key, output.context, use_short_ids, True) for key in goal_keys)), None, None))
            output.print("")
            return
        output.print(f"\n{sym.Target} [bold]Goals:[/bold]")

        # Format each goal using format_label (context#action format)
        for goal in goal_keys:
            goal_line = Text("  ")
            goal_line.append_text(output.action.format_label(goal, use_short_ids))
            output.print(goal_line)

    def _print_retainer_results(
        self,
        retainer_results: list[RetainerResult],
        output: OutputFormatter,
        use_short_ids: bool,
        verbose: bool,
    ) -> None:
        """Print results from retainer execution.

        Args:
            retainer_results: List of RetainerResult objects
            output: Output formatter
            use_short_ids: Whether to use short context IDs
            verbose: Whether to print verbose output (detailed with stdout)
        """
        if not retainer_results:
            return

        sym = output.symbols

        if output.compact or output.recording_preparation:
            output.print(self._build_retainer_results(retainer_results, output, use_short_ids))
            if verbose:
                for result in retainer_results:
                    if result.stdout:
                        output.print(section(action_label(result.retainer_key, output.context, use_short_ids, True),
                                             literal_text(result.stdout.rstrip()), None, None))
                        output.print("")
            return

        if verbose:
            # Verbose mode: detailed output with stdout
            output.print(f"\n{sym.Refresh} [bold]Retainers:[/bold]")
            for ret_result in retainer_results:
                retainer_label = output.escape(output.action.format_label_plain(ret_result.retainer_key, use_short_ids))
                time_str = f"{ret_result.execution_time_ms:.0f}ms"

                if ret_result.retained:
                    unique_targets = list(dict.fromkeys(ret_result.soft_dep_targets))
                    targets_str = ", ".join(
                        f"[bold cyan]{output.escape(output.action.format_label_plain(t, use_short_ids))}[/bold cyan]"
                        for t in unique_targets
                    )
                    output.print(
                        f"  {sym.Check} [bold cyan]{retainer_label}[/bold cyan] [dim]ran in[/dim] {time_str} "
                        f"[dim]{sym.Arrow} retained[/dim] {targets_str}"
                    )
                else:
                    output.print(
                        f"  {sym.Cross} [bold cyan]{retainer_label}[/bold cyan] [dim]ran in[/dim] {time_str} "
                        f"[dim]{sym.Arrow} retained nothing[/dim]"
                    )

                if ret_result.stdout:
                    for stdout_line in ret_result.stdout.rstrip().split("\n"):
                        output.print(f"    [dim]stdout:[/dim] {output.escape(stdout_line)}")
        else:
            # Non-verbose mode: compact Rich table (same styling as execution plan)
            from rich.table import Table

            no_color = output.no_color
            header_style = "" if no_color else "bold"
            action_style = "" if no_color else "cyan"
            dim_style = "" if no_color else "dim"

            # Print title separately (same pattern as execution plan)
            output.print(f"\n{sym.Refresh} [bold]Retainers:[/bold]")

            table = Table(show_header=True, header_style=header_style)
            table.add_column("Context")
            table.add_column("Retainer", style=action_style)
            table.add_column("Retained", style=action_style)
            table.add_column("Result", justify="center")
            table.add_column("Time", style=dim_style, justify="right")

            ctx_fmt = output.context

            for ret_result in retainer_results:
                retainer_key = ret_result.retainer_key

                # Context column - use formatter with symbol (same as execution plan)
                context_text = ctx_fmt.format_id_with_symbol(retainer_key.context_id, use_short_ids)

                # Retainer column - just the action name (escaped for safety)
                retainer_name = output.escape(retainer_key.id.name)

                # Time column
                time_str = f"{ret_result.execution_time_ms:.0f}ms"

                # Retained and Result columns
                if ret_result.retained:
                    unique_targets = list(dict.fromkeys(ret_result.soft_dep_targets))
                    retained_str = ", ".join(output.escape(t.id.name) for t in unique_targets)
                    result_str = f"[green]{sym.Check}[/green]"
                else:
                    retained_str = "-"
                    result_str = f"[dim]{sym.Cross}[/dim]"

                table.add_row(context_text, retainer_name, retained_str, result_str, time_str)

            output.console.print(table)
            output.print("")

    def _build_retainer_results(self, results: list[RetainerResult], output: OutputFormatter,
                                    use_short_ids: bool) -> Group:
        rows = []
        for result in results:
            row = Text(f"  {output.symbols.Check if result.retained else output.symbols.Cross} ")
            row.append_text(action_label(result.retainer_key, output.context, use_short_ids, True))
            row.append(f" {result.execution_time_ms:.0f}ms: ", style="dim")
            row.append(", ".join(key.id.name for key in dict.fromkeys(result.soft_dep_targets)) if result.retained else "-")
            rows.append(row)
        return Group(section("Retainers:", Group(*rows), None, None), Text("")) if results else Group()

    def _get_previous_run_dir(
        self,
        project_root: Path,
        output: OutputFormatter,
    ) -> Optional[Path]:
        """Get the most recent run directory for --continue-run mode.

        Args:
            project_root: Project root path
            output: Output formatter for messages

        Returns:
            Path to previous run directory, or None if not found
        """
        sym = output.symbols
        runs_dir = project_root / ".mdl" / "runs"

        if not runs_dir.exists():
            output.print_run_field("Warning", Text("No runs directory found, starting fresh", style="yellow not dim"),
                                   f"\n{sym.Warning} [bold yellow]Warning:[/bold yellow] No runs directory found, starting fresh")
            return None

        run_dirs = sorted([d for d in runs_dir.iterdir() if d.is_dir()])
        if not run_dirs:
            output.print_run_field("Warning", Text("No previous runs found, starting fresh", style="yellow not dim"),
                                   f"\n{sym.Warning} [bold yellow]Warning:[/bold yellow] No previous runs found, starting fresh")
            return None

        previous_run_dir = run_dirs[-1]
        output.print_run_field("Continuing from previous run", Text(previous_run_dir.name, style="cyan"),
            f"\n{sym.Refresh} [blue]Continuing from previous run:[/blue] "
            f"[bold cyan]{previous_run_dir.name}[/bold cyan]"
        )
        return previous_run_dir

    def _print_outputs(
        self,
        outputs_to_report: dict,
        output: OutputFormatter,
        no_color: bool,
        out_path: Optional[str],
        presented_outputs: Optional[Group] = None,
    ) -> None:
        """Print nonempty compact output groups or the existing JSON presentation.

        Args:
            outputs_to_report: Dictionary of outputs to report
            output: Output formatter
            no_color: Whether colors are disabled
            out_path: Optional file path to save outputs
            presented_outputs: Typed compact groups; the saved JSON remains unchanged
        """
        sym = output.symbols
        output_json = json.dumps(outputs_to_report, indent=2)
        if presented_outputs is not None:
            if presented_outputs.renderables:
                output.print(section("Outputs:", presented_outputs, None, None))
                output.print("")
        else:
            output.print(f"\n{sym.Chart} [bold]Outputs:[/bold]")
            if not no_color:
                from rich.console import Console
                from rich.json import JSON
                console = Console()
                console.print(JSON(output_json))
            else:
                output.print(output_json)

        if out_path:
            path = Path(out_path)
            path.write_text(output_json, encoding="utf-8")
            output.print(f"\n{sym.Save} [dim]Outputs saved to:[/dim] [bold cyan]{output.escape(str(path))}[/bold cyan]")

    def _apply_platform_defaults(self, args: argparse.Namespace, quiet_mode: bool) -> Optional[Text]:
        """Apply platform specific defaults."""
        args.no_color = args.no_color or bool(os.environ.get("NO_COLOR"))
        try:
            mode = resolve_logger_mode(args.logger, args.simple_log is True, args.verbose, args.github_actions, args.teamcity)
        except ValueError as error:
            self.parser.error(str(error))
        args.logger = mode.value
        if args.plan_style is None:
            args.plan_style = "table" if mode == LoggerMode.TABLE else "dag"
        args.verbose = mode == LoggerMode.VERBOSE
        args.github_actions = mode == LoggerMode.GITHUB
        usable_terminal = sys.stdout.isatty() and sys.stdin.isatty() and os.environ.get("TERM") not in {"dumb", "unknown"}
        if mode == LoggerMode.TABLE and not usable_terminal and not args.force_interactive:
            self.parser.error("--logger table requires an interactive terminal; use pure/simple or --force-interactive")
        system = platform.system()
        
        # Determine Nix usage
        use_nix_env = os.environ.get("MUDYLA_USE_NIX", "").lower()
        
        # Nix is enabled by default only on Linux
        nix_default_on = system == "Linux"
        
        using_nix = False
        reason = ""
        
        if args.force_nix:
            using_nix = True
            reason = "forced with --force-nix"
        elif args.without_nix:
            using_nix = False
            reason = "disabled with --without-nix"
        elif use_nix_env == "force-on":
            using_nix = True
            reason = "forced with MUDYLA_USE_NIX=force-on"
        elif use_nix_env == "force-off":
            using_nix = False
            reason = "disabled with MUDYLA_USE_NIX=force-off"
        else:
            using_nix = nix_default_on
            reason = f"default for {system}"
            
        # Update args
        args.without_nix = not using_nix
        
        if args.github_actions and system == "Windows" and not args.no_color:
            args.no_color = True

        state = "Yes" if using_nix else "No"
        return None if quiet_mode else Text(f"{state} ({reason})")


    def _handle_autocomplete(self, args: argparse.Namespace) -> int:
        """Handle autocomplete mode without noisy output."""
        mode = args.autocomplete or "actions"
        try:
            project_root = find_project_root()
            md_files = self._discover_markdown_files(args.defs, project_root)
            if not md_files:
                return 1

            parser = MarkdownParser()
            document = parser.parse_files(md_files)

            if mode == "actions":
                suggestions = self._list_action_names_ordered(document)
            elif mode == "flags":
                suggestions = self._list_all_flags(document)
            elif mode == "axis-names":
                suggestions = self._list_axis_names(document)
            elif mode == "axis-values":
                axis_name = args.autocomplete_axis
                if not axis_name:
                    return 1
                suggestions = self._list_axis_values(document, axis_name)
            else:
                return 1

            for name in suggestions:
                print(name)
            return 0
        except Exception:
            return 1

    def _list_axis_names(self, document: ParsedDocument) -> list[str]:
        """Return all axis names defined in the document."""
        return sorted(document.axis.keys())

    def _list_axis_values(self, document: ParsedDocument, axis_name: str) -> list[str]:
        """Return all values for a specific axis."""
        if axis_name not in document.axis:
            return []
        axis_def = document.axis[axis_name]
        return [av.value for av in axis_def.values]

    def _build_formatters(self, no_color: bool, plain: bool, compact: bool) -> OutputFormatter:
        """Build the output formatter with all sub-formatters.

        Args:
            no_color: If True, disable colors

        Returns:
            OutputFormatter instance (access action via output.action)
        """
        return OutputFormatter(no_color=no_color, plain=plain, compact=compact)

    def _prepare_execution_setup(
        self,
        args: argparse.Namespace,
        parsed_inputs: ParsedCLIInputs,
        output: OutputFormatter,
    ) -> ExecutionSetup:
        """Load markdown definitions and merge CLI inputs with defaults."""
        project_root = find_project_root()
        output.print_run_field("Project root", literal_text(str(project_root), "cyan"),
                               f"[dim]Project root:[/dim] [bold cyan]{output.escape(str(project_root))}[/bold cyan]")

        md_files = self._discover_markdown_files(args.defs, project_root)
        if not md_files:
            raise ValueError(f"No markdown files found matching pattern: {args.defs}")

        parser = MarkdownParser()
        document = parser.parse_files(md_files)

        # Expand wildcards in axis specifications
        parsed_inputs = expand_all_wildcards(parsed_inputs, document)

        # Resolve argument aliases (e.g., --ml -> --message-local)
        parsed_inputs = self._resolve_argument_aliases(document, parsed_inputs)

        custom_args = dict(parsed_inputs.custom_args)
        axis_values = dict(parsed_inputs.axis_values)
        goals = list(parsed_inputs.goals)

        self._apply_default_axis_values(document, axis_values, output)
        self._apply_default_argument_values(document, custom_args)
        self._normalize_array_arguments(document, custom_args)

        all_flags = {name: False for name in document.flags}
        all_flags.update(parsed_inputs.custom_flags)

        if not goals and not args.list_actions:
            raise ValueError("No goals specified")

        return ExecutionSetup(
            document=document,
            project_root=project_root,
            markdown_files=md_files,
            goals=goals,
            custom_args=custom_args,
            axis_values=axis_values,
            all_flags=all_flags,
            parsed_inputs=parsed_inputs,
        )

    def _discover_markdown_files(self, defs_pattern: str, project_root: Path) -> list[Path]:
        pattern = Path(defs_pattern)
        if not pattern.is_absolute():
            pattern = project_root / defs_pattern
        matches = [
            Path(path) for path in glob(str(pattern), recursive=True)
            if Path(path).is_file() and path.endswith('.md')
        ]
        return matches

    def _apply_default_axis_values(
        self,
        document: ParsedDocument,
        axis_values: dict[str, str],
        output: OutputFormatter,
    ) -> None:
        default_axes: list[Text] = []
        for axis_name, axis_def in document.axis.items():
            if axis_name in axis_values:
                continue
            default_value = axis_def.get_default_value()
            if default_value:
                axis_values[axis_name] = default_value
                if output.compact or output.recording_preparation:
                    default_axes.append(axis_field(axis_name, default_value))
                else:
                    output.print(
                        f"[dim]Using default axis value:[/dim] [magenta]{output.escape(axis_name)}[/magenta]"
                        f"[dim]:[/dim][yellow]{output.escape(default_value)}[/yellow]"
                    )
        if default_axes:
            output.print_run_field("Using default axes", Text(", ").join(default_axes), "")

    def _resolve_argument_aliases(
        self,
        document: ParsedDocument,
        parsed_inputs: ParsedCLIInputs,
    ) -> ParsedCLIInputs:
        """Resolve argument aliases to their canonical names.

        Args:
            document: Parsed document with argument definitions
            parsed_inputs: Parsed CLI inputs (immutable, returns new instance)

        Returns:
            New ParsedCLIInputs with aliases resolved in both global and per-action args
        """
        # Build alias -> canonical name mapping
        alias_to_canonical: dict[str, str] = {}
        for arg_name, arg_def in document.arguments.items():
            if arg_def.alias:
                if arg_def.alias in alias_to_canonical:
                    raise ValueError(
                        f"Duplicate alias '{arg_def.alias}': used by both "
                        f"'args.{alias_to_canonical[arg_def.alias]}' and 'args.{arg_name}'"
                    )
                alias_to_canonical[arg_def.alias] = arg_name

        if not alias_to_canonical:
            return parsed_inputs

        def resolve_args(args: dict[str, ArgValue]) -> dict[str, ArgValue]:
            """Resolve aliases in a dict of arguments."""
            resolved = dict(args)
            for alias, canonical_name in alias_to_canonical.items():
                if alias in resolved:
                    alias_value = resolved.pop(alias)
                    if canonical_name in resolved:
                        # Merge values (both alias and canonical were used)
                        existing = resolved[canonical_name]
                        if isinstance(existing, list):
                            if isinstance(alias_value, list):
                                existing.extend(alias_value)
                            else:
                                existing.append(alias_value)
                        else:
                            if isinstance(alias_value, list):
                                resolved[canonical_name] = [existing] + alias_value
                            else:
                                resolved[canonical_name] = [existing, alias_value]
                    else:
                        resolved[canonical_name] = alias_value
            return resolved

        # Resolve global args
        resolved_global_args = resolve_args(parsed_inputs.global_args)

        # Resolve per-action args
        resolved_invocations = []
        for inv in parsed_inputs.action_invocations:
            resolved_invocations.append(ActionInvocation(
                action_name=inv.action_name,
                args=resolve_args(inv.args),
                flags=inv.flags,
                axes=inv.axes,
            ))

        return ParsedCLIInputs(
            global_args=resolved_global_args,
            global_flags=parsed_inputs.global_flags,
            global_axes=parsed_inputs.global_axes,
            action_invocations=resolved_invocations,
            goal_warnings=parsed_inputs.goal_warnings,
        )

    def _apply_default_argument_values(
        self,
        document: ParsedDocument,
        custom_args: dict[str, ArgValue],
    ) -> None:
        """Apply default values for missing arguments."""
        for arg_name, arg_def in document.arguments.items():
            if arg_name in custom_args:
                continue
            if arg_def.default_value is not None:
                custom_args[arg_name] = arg_def.default_value

    def _normalize_array_arguments(
        self,
        document: ParsedDocument,
        custom_args: dict[str, ArgValue],
    ) -> None:
        """Ensure array arguments are always lists, scalar args are always strings.

        - For array arguments: convert single string to list[str]
        - For scalar arguments: fail if multiple values were provided
        """
        for arg_name, arg_def in document.arguments.items():
            if arg_name not in custom_args:
                continue

            value = custom_args[arg_name]

            if arg_def.is_array:
                # Array argument: ensure it's a list
                if isinstance(value, str):
                    custom_args[arg_name] = [value]
                # Already a list, nothing to do
            else:
                # Scalar argument: must be a single string
                if isinstance(value, list):
                    raise ValueError(
                        f"Argument 'args.{arg_name}' is not an array type but was "
                        f"specified multiple times. Use type 'array[{arg_def.arg_type.element_type.value}]' "
                        f"if you want to specify multiple values."
                    )

    def _compute_sharing_counts(self, graph: ActionGraph, execution_order: list[ActionKey],
                                goals: list[str]) -> dict[ActionKey, int]:
        return sharing_counts(graph, execution_order, goals)

    def _visualize_execution_plan(
        self,
        graph,
        execution_order,
        goals: list[str],
        output: OutputFormatter,
        use_short_ids: bool,
        plan_style: PlanStyle = "dag",
    ) -> Group:
        """Render the selected static plan presentation.

        Args:
            graph: The execution graph
            execution_order: List of action keys in execution order
            goals: List of goal action names
            output: Output formatter
            use_short_ids: Whether to use short context IDs
            plan_style: Table, connected DAG, or dependency tree
        """
        # Compute sharing counts: how many unique goal contexts use each action
        sharing_counts = self._compute_sharing_counts(graph, execution_order, goals)

        if plan_style == "table":
            plan = Group(section("Plan:", execution_table(graph, execution_order, output.context, use_short_ids,
                                                          sharing_counts, output.console.options.ascii_only), None, None), Text(""))
            output.print(plan)
            return plan

        if plan_style == "dag":
            def initial_status(key: ActionKey) -> Text:
                ready = not graph.get_node(key).dependencies
                glyphs = (">", "o") if output.console.options.ascii_only else ("◇", "○")
                return Text(glyphs[0 if ready else 1] + " ", style="cyan" if ready else "dim")

            dag = execution_dag(graph, execution_order, output.context, use_short_ids, sharing_counts,
                                initial_status, lambda key: "dim")
            plan = Group(dag_section(dag, output.console.options.ascii_only), Text(""))
            output.print(plan)
            return plan
        assert plan_style == "tree"
        return self._print_execution_tree(graph, execution_order, output, use_short_ids, sharing_counts)

    def _print_execution_tree(self, graph: ActionGraph, execution_order: list[ActionKey], output: OutputFormatter,
                              use_short_ids: bool, sharing_counts: dict[ActionKey, int]) -> Group:
        plan = Group(tree_section(self._build_execution_tree(graph, execution_order, output, use_short_ids, sharing_counts),
                                  output.console.options.ascii_only), Text(""))
        output.print(plan)
        return plan

    def _build_execution_tree(self, graph: ActionGraph, execution_order: list[ActionKey], output: OutputFormatter,
                              use_short_ids: bool, sharing_counts: dict[ActionKey, int]) -> Group:
        def initial_status(key: ActionKey) -> Text:
            ready = not graph.get_node(key).dependencies
            glyphs = (">", "o") if output.console.options.ascii_only else ("◇", "○")
            return Text(glyphs[0 if ready else 1] + " ", style="cyan" if ready else "dim")

        return execution_tree(graph, execution_order, output.context, use_short_ids, sharing_counts, initial_status)

    def _list_actions(self, document: ParsedDocument, output: OutputFormatter) -> None:
        """List all available actions."""
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

        root_actions, non_root_actions = self._partition_actions(document)
        metadata = {
            name: self._collect_action_metadata(action)
            for name, action in document.actions.items()
        }

        # Display root actions first, then non-root actions
        for action_name in root_actions + non_root_actions:
            action = document.actions[action_name]
            info = metadata[action_name]
            typed_deps = info["typed_dependencies"]
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

            args_used = info["args_used"]
            if args_used:
                escaped_args = ', '.join(output.escape(a) for a in sorted(args_used))
                output.print(f"    [dim]Arguments:[/dim] [bold yellow]{escaped_args}[/bold yellow]")

            flags_used = info["flags_used"]
            if flags_used:
                escaped_flags = ', '.join(output.escape(f) for f in sorted(flags_used))
                output.print(f"    [dim]Flags:[/dim] [bold yellow]{escaped_flags}[/bold yellow]")

            all_env_vars = info["env_vars"]
            if all_env_vars:
                escaped_env = ', '.join(output.escape(e) for e in sorted(all_env_vars))
                output.print(f"    [dim]Env vars:[/dim] {escaped_env}")

            inputs_map = info["inputs"]
            if inputs_map:
                input_parts = []
                for act_name in sorted(inputs_map.keys()):
                    escaped_act = output.escape(act_name)
                    vars_str = ', '.join(output.escape(v) for v in sorted(inputs_map[act_name]))
                    input_parts.append(f"{escaped_act}.{{{vars_str}}}")
                output.print(f"    [dim]Inputs:[/dim] {', '.join(input_parts)}")

            all_returns = info["returns"]
            if all_returns:
                returns_parts = []
                for r in all_returns:
                    escaped_name = output.escape(r.name)
                    escaped_type = output.escape(r.return_type.value)
                    returns_parts.append(f"[bold green]{escaped_name}[/bold green][dim]:{escaped_type}[/dim]")
                output.print(f"    [dim]Returns:[/dim] {', '.join(returns_parts)}")

            # Show versions if action has multiple versions
            if len(action.versions) > 1:
                from .ast.models import AxisCondition, PlatformCondition
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

    def _list_action_names_ordered(self, document: ParsedDocument) -> list[str]:
        """Return action names in the same order as _list_actions prints them."""
        root_actions, non_root_actions = self._partition_actions(document)
        return root_actions + non_root_actions

    def _partition_actions(self, document: ParsedDocument) -> tuple[list[str], list[str]]:
        root_actions: list[str] = []
        non_root_actions: list[str] = []
        for action_name, action in document.actions.items():
            deps = action.get_typed_action_dependencies()
            if len(deps) == 0:
                root_actions.append(action_name)
            else:
                non_root_actions.append(action_name)
        root_actions.sort()
        non_root_actions.sort()
        return root_actions, non_root_actions

    def _list_cli_flag_options(self) -> list[str]:
        """Return CLI-level flag options (long form only, excluding autocomplete)."""
        cli_flags: set[str] = set()
        for action in self.parser._actions:
            for option in action.option_strings:
                if not option.startswith("--"):
                    continue
                if option == "--autocomplete":
                    continue
                cli_flags.add(option)
        # Add all axis option aliases
        for axis_opt in AXIS_OPTIONS:
            cli_flags.add(axis_opt)
        return sorted(cli_flags)

    def _list_all_flags(self, document: ParsedDocument) -> list[str]:
        """Return combined list of CLI and document flags (prefixed with --)."""
        document_flags = {f"--{flag_name}" for flag_name in document.flags.keys()}
        all_flags = set(self._list_cli_flag_options())
        all_flags.update(document_flags)
        return sorted(all_flags)

    def _collect_action_metadata(self, action: ActionDefinition) -> dict[str, object]:
        args_used: set[str] = set()
        flags_used: set[str] = set()
        env_vars_used: set[str] = set()
        inputs: dict[str, set[str]] = {}

        for expansion in action.get_all_expansions():
            if isinstance(expansion, ArgsExpansion):
                args_used.add(expansion.argument_name)
            elif isinstance(expansion, FlagsExpansion):
                flags_used.add(expansion.flag_name)
            elif isinstance(expansion, EnvExpansion):
                env_vars_used.add(expansion.variable_name)
            elif isinstance(expansion, ActionExpansion):
                inputs.setdefault(expansion.action_name, set()).add(expansion.variable_name)

        returns_map: dict[str, object] = {}
        for version in action.versions:
            for ret_decl in version.return_declarations:
                returns_map[ret_decl.name] = ret_decl

        all_env_vars = set(action.required_env_vars.keys()) | env_vars_used

        return {
            "dependencies": action.get_action_dependencies(),
            "typed_dependencies": action.get_typed_action_dependencies(),
            "args_used": args_used,
            "flags_used": flags_used,
            "env_vars": all_env_vars,
            "inputs": inputs,
            "returns": list(returns_map.values()),
        }


def main() -> int:
    """Main entry point."""
    cli = CLI()
    try:
        return cli.run()
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
