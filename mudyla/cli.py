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
from typing import Optional
from rich.text import Text

from .ast.models import ParsedDocument, ActionDefinition
from .dag.builder import DAGBuilder
from .dag.compiler import DAGCompiler, CompilationError
from .dag.graph import ActionKey, Dependency
from .dag.validator import DAGValidator, ValidationError
from .executor.engine import ExecutionEngine
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
from .utils.output import OutputFormatter
from .utils.action_formatter import ActionFormatter
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
        self._apply_platform_defaults(args, quiet_mode)

        if args.autocomplete:
            return self._handle_autocomplete(args)

        action_fmt, output = self._build_formatters(args.no_color)
        from rich.text import Text

        try:
            # All arguments (goals, axes, args, flags) are in 'unknown' since we don't
            # define a positional 'goals' parameter in argparse (to preserve order)
            parsed_inputs = parse_custom_inputs([], unknown)
        except CLIParseError as e:
            sym = output.symbols
            output.print(f"{sym.Cross} [bold red]Error:[/bold red] {e}")
            return 1

        sym = output.symbols
        try:
            setup = self._prepare_execution_setup(args, parsed_inputs, action_fmt, output)
            self._validate_required_env(setup.document)

            document = setup.document
            goals = setup.goals
            custom_args = setup.custom_args
            axis_values = setup.axis_values
            all_flags = setup.all_flags
            project_root = setup.project_root

            if args.list_actions:
                self._list_actions(document, args.no_color)
                return 0

            parallel_execution = args.parallel or (
                not args.sequential
                and not args.verbose
                and not args.github_actions
                and not document.properties.sequential_execution_default
            )

            output.print(
                f"{sym.Book} [dim]Found[/dim] [bold]{len(setup.markdown_files)}[/bold] "
                f"[dim]definition file(s) with[/dim] [bold]{len(document.actions)}[/bold] [dim]actions[/dim]"
            )

            for warning in setup.parsed_inputs.goal_warnings:
                output.print(f"{sym.Warning} [bold yellow]Warning:[/bold yellow] {warning}")

            # Use the new compiler for multi-context support
            planning_start = time.perf_counter()
            compiler = DAGCompiler(document, setup.parsed_inputs)
            compiler.validate_action_invocations()
            graph = compiler.compile()
            planning_elapsed_ms = (time.perf_counter() - planning_start) * 1000

            use_short_ids = not args.full_ctx_reprs

            # Get unique contexts from action keys, with default context first
            all_contexts = {ak.context_id for ak in graph.nodes.keys()}
            default_ctx = [ctx for ctx in all_contexts if str(ctx) == "default"]
            other_contexts = sorted([ctx for ctx in all_contexts if str(ctx) != "default"], key=str)
            unique_contexts = default_ctx + other_contexts

            # Show contexts BEFORE retainer execution
            if len(unique_contexts) > 0:
                output.print(f"\n{sym.Link} [bold]Contexts:[/bold]")

                # Format all contexts to calculate max width for padding
                formatted_ids = [
                    action_fmt.context_formatter.format_id_with_symbol(ctx, use_short_ids)
                    for ctx in unique_contexts
                ]
                max_id_len = max(len(fid.plain) for fid in formatted_ids) if formatted_ids else 0

                for ctx, formatted_id in zip(unique_contexts, formatted_ids):
                    padding = " " * (max_id_len - len(formatted_id.plain))
                    ctx_line = Text("  ")
                    ctx_line.append_text(formatted_id)
                    ctx_line.append(padding + " : ")
                    ctx_line.append_text(action_fmt.context_formatter.format_full(ctx))
                    output.print(ctx_line)

            # Show goals BEFORE retainer execution
            goal_keys = sorted(graph.goals, key=str)
            goal_texts = [action_fmt.format_label(goal, use_short_ids) for goal in goal_keys]
            goals_line = Text()
            goals_line.append(f"\n{sym.Target} ")
            goals_line.append("Goals: ", style="dim")
            for i, goal_text in enumerate(goal_texts):
                if i > 0:
                    goals_line.append(", ")
                goals_line.append_text(goal_text)
            output.print(goals_line)

            # Execute retainers for soft dependencies to determine which to retain
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

            # Log retainer results with context info
            if retainer_results:
                output.print(f"\n{sym.Refresh} [bold]Retainers:[/bold]")

            for ret_result in retainer_results:
                retainer_label = action_fmt.format_label_plain(ret_result.retainer_key, use_short_ids)
                time_str = f"{ret_result.execution_time_ms:.0f}ms"

                if ret_result.retained:
                    unique_targets = list(dict.fromkeys(ret_result.soft_dep_targets))
                    targets_str = ", ".join(
                        f"[bold cyan]{action_fmt.format_label_plain(t, use_short_ids)}[/bold cyan]"
                        for t in unique_targets
                    )
                    output.print(f"  [bold cyan]{retainer_label}[/bold cyan] [dim]ran in[/dim] {time_str} [dim]{output.symbols.Arrow} retained[/dim] {targets_str}")
                else:
                    output.print(f"  [bold cyan]{retainer_label}[/bold cyan] [dim]ran in[/dim] {time_str} [dim]{output.symbols.Arrow} retained nothing[/dim]")

                if args.verbose and (ret_result.stdout or ret_result.stderr):
                    if ret_result.stdout:
                        for stdout_line in ret_result.stdout.rstrip().split("\n"):
                            output.print(f"    [dim]stdout:[/dim] {stdout_line}")
                    if ret_result.stderr:
                        for stderr_line in ret_result.stderr.rstrip().split("\n"):
                            output.print(f"    [dim]stderr:[/dim] {stderr_line}")

            pruned_graph = graph.prune_to_goals(retained_soft_targets)

            # Show execution mode
            if not quiet_mode:
                mode_label = "dry-run" if args.dry_run else ("parallel" if parallel_execution else "sequential")
                output.print(f"\n{sym.Gear} [dim]Execution mode:[/dim] [bold cyan]{mode_label}[/bold cyan]")

            validator = DAGValidator(document, pruned_graph)
            validator.validate_all(custom_args, all_flags, axis_values)
            if not quiet_mode:
                output.print(
                    f"{sym.Check} [dim]Built plan graph with[/dim] [bold]{len(pruned_graph.nodes)}[/bold] "
                    f"[dim]required action(s) (planning took {planning_elapsed_ms:.0f}ms)[/dim]"
                )

            execution_order = pruned_graph.get_execution_order()
            if not quiet_mode:
                output.print(f"\n{sym.Clipboard} [bold]Execution plan:[/bold]")
                self._visualize_execution_plan(pruned_graph, execution_order, goals, action_fmt, output, use_short_ids)

            if args.dry_run:
                output.print(f"\n{sym.Info} [blue]Dry run - not executing[/blue]")
                return 0

            previous_run_dir = None
            if args.continue_run:
                runs_dir = project_root / ".mdl" / "runs"
                if runs_dir.exists():
                    run_dirs = sorted([d for d in runs_dir.iterdir() if d.is_dir()])
                    if run_dirs:
                        previous_run_dir = run_dirs[-1]
                        output.print(
                            f"\n{sym.Refresh} [blue]Continuing from previous run:[/blue] "
                            f"[bold cyan]{previous_run_dir.name}[/bold cyan]"
                        )
                    else:
                        output.print(f"\n{sym.Warning} [bold yellow]Warning:[/bold yellow] No previous runs found, starting fresh")
                else:
                    output.print(f"\n{sym.Warning} [bold yellow]Warning:[/bold yellow] No runs directory found, starting fresh")

            # --it flag keeps process running after completion for reviewing
            keep_running = (
                args.interactive
                and not args.verbose
                and not args.github_actions
                and sys.stdout.isatty()
            )

            engine = ExecutionEngine(
                graph=pruned_graph,
                project_root=project_root,
                args=custom_args,
                flags=all_flags,
                axis_values=axis_values,
                environment_vars=document.environment_vars,
                passthrough_env_vars=document.passthrough_env_vars,
                previous_run_directory=previous_run_dir,
                github_actions=args.github_actions,
                without_nix=args.without_nix,
                verbose=args.verbose,
                no_output_on_fail=args.no_out_on_fail,
                keep_run_dir=args.keep_run_dir or keep_running,
                no_color=args.no_color,
                simple_log=args.simple_log,
                show_dirs=args.show_dirs,
                parallel_execution=parallel_execution,
                use_short_context_ids=use_short_ids,
                keep_running=keep_running,
            )

            # Print run ID
            run_id = engine.run_directory.name
            output.print(f"\n{sym.Id} [dim]Run ID:[/dim] [bold cyan]{run_id}[/bold cyan]")

            result = engine.execute_all()

            if not result.success:
                output.print(f"\n{sym.Cross} [bold red]Execution failed![/bold red]")
                return 1

            # Get outputs using ActionKeys (with context) instead of just action names
            if args.full_output:
                outputs_to_report = result.get_all_outputs(pruned_graph.nodes.keys())
            else:
                outputs_to_report = result.get_goal_outputs(graph.goals)

            output.print(f"\n{sym.Check} [bold green]Execution completed successfully![/bold green]")

            output_json = json.dumps(outputs_to_report, indent=2)
            output.print(f"\n{sym.Chart} [bold]Outputs:[/bold]")

            if not args.no_color:
                from rich.console import Console
                from rich.json import JSON
                console = Console()
                console.print(JSON(output_json))
            else:
                output.print(output_json)

            if args.out:
                out_path = Path(args.out)
                out_path.write_text(output_json)
                output.print(f"\n{sym.Save} [dim]Outputs saved to:[/dim] [bold cyan]{out_path}[/bold cyan]")

            if args.keep_run_dir:
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
            output.print(f"{sym.Cross} [bold red]Error:[/bold red] {err}")
            if "No goals specified" in str(err):
                self.parser.print_help()
            return 1
        except ValidationError as validation_err:
            try:
                output.print(f"\n{sym.Cross} [bold red]Validation error:[/bold red]\n{validation_err}")
            except (NameError, UnicodeEncodeError):
                print(f"\n[!] Validation error:\n{validation_err}")
            return 1
        except CompilationError as comp_err:
            try:
                output.print(f"\n{sym.Cross} [bold red]Compilation error:[/bold red]\n{comp_err}")
            except (NameError, UnicodeEncodeError):
                print(f"\n[!] Compilation error:\n{comp_err}")
            return 1
        except Exception as gen_err:
            try:
                output.print(f"\n{sym.Cross} [bold red]Error:[/bold red] {gen_err}")
            except (NameError, UnicodeEncodeError):
                print(f"\n[!] Error: {gen_err}")
            import traceback

            traceback.print_exc()
            return 1

    def _validate_required_env(self, document: ParsedDocument):
        missing_vars = []
        for var in document.required_env_vars:
            if var not in os.environ:
                missing_vars.append(var)
        
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

    def _apply_platform_defaults(self, args: argparse.Namespace, quiet_mode: bool) -> None:
        """Apply platform specific defaults."""
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
        
        if not quiet_mode:
            state = "Yes" if using_nix else "No"
            print(f"Using Nix: {state} ({reason})")

        if args.github_actions and system == "Windows" and not args.no_color:
            args.no_color = True


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

    def _build_formatters(self, no_color: bool) -> tuple[ActionFormatter, OutputFormatter]:
        output = OutputFormatter(no_color=no_color)
        action_fmt = ActionFormatter(no_color=no_color)
        return action_fmt, output

    def _prepare_execution_setup(
        self,
        args: argparse.Namespace,
        parsed_inputs: ParsedCLIInputs,
        action_fmt: ActionFormatter,
        output: OutputFormatter,
    ) -> ExecutionSetup:
        """Load markdown definitions and merge CLI inputs with defaults."""
        project_root = find_project_root()
        output.print(f"[dim]Project root:[/dim] [bold cyan]{project_root}[/bold cyan]")

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
        for axis_name, axis_def in document.axis.items():
            if axis_name in axis_values:
                continue
            default_value = axis_def.get_default_value()
            if default_value:
                axis_values[axis_name] = default_value
                output.print(
                    f"[dim]Using default axis value:[/dim] [magenta]{axis_name}[/magenta]"
                    f"[dim]:[/dim][yellow]{default_value}[/yellow]"
                )

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

        def resolve_args(args: dict[str, ArgValue], context: str) -> dict[str, ArgValue]:
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
        resolved_global_args = resolve_args(parsed_inputs.global_args, "global scope")

        # Resolve per-action args
        resolved_invocations = []
        for inv in parsed_inputs.action_invocations:
            resolved_invocations.append(ActionInvocation(
                action_name=inv.action_name,
                args=resolve_args(inv.args, f"action '{inv.action_name}'"),
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

    def _compute_sharing_counts(
        self,
        graph,
        execution_order,
        goals: list[str],
    ) -> dict:
        """Compute how many unique goal contexts use each action.

        An action is "shared" if multiple goal contexts depend on it (directly or
        transitively). This helps visualize context reduction benefits.

        Args:
            graph: The execution graph
            execution_order: List of action keys in execution order
            goals: List of goal action names

        Returns:
            Dictionary mapping ActionKey to count of goal contexts that use it
        """
        from .dag.graph import ActionKey

        # Find all goal action keys
        goal_keys = [ak for ak in execution_order if ak.id.name in goals]

        # For each action, collect which goal contexts reach it
        action_to_goal_contexts: dict[ActionKey, set[str]] = {}

        def collect_reachable_goals(action_key: ActionKey, visited: set[ActionKey]) -> set[str]:
            """Recursively find all goal contexts that depend on this action."""
            if action_key in visited:
                return set()
            visited.add(action_key)

            contexts: set[str] = set()

            # If this is a goal, add its context
            if action_key.id.name in goals:
                contexts.add(str(action_key.context_id))

            # Check all dependents (actions that depend on this one)
            node = graph.get_node(action_key)
            for dep in node.dependents:
                contexts.update(collect_reachable_goals(dep.action, visited))

            return contexts

        # Compute for each action
        for action_key in execution_order:
            contexts = collect_reachable_goals(action_key, set())
            action_to_goal_contexts[action_key] = contexts

        # Convert to counts
        return {ak: len(contexts) for ak, contexts in action_to_goal_contexts.items()}

    def _visualize_execution_plan(
        self,
        graph,
        execution_order,
        goals: list[str],
        action_fmt: ActionFormatter,
        output: OutputFormatter,
        use_short_ids: bool = False,
    ) -> None:
        """Visualize execution plan as a rich table.

        Args:
            graph: The execution graph
            execution_order: List of action keys in execution order
            goals: List of goal action names
            action_fmt: Action formatter
            output: Output formatter
            use_short_ids: Whether to use short context IDs
        """
        from rich.table import Table

        sym = output.symbols
        no_color = output.no_color

        # Compute sharing counts: how many unique goal contexts use each action
        sharing_counts = self._compute_sharing_counts(graph, execution_order, goals)

        # Styles conditional on no_color
        header_style = "" if no_color else "bold"
        action_style = "" if no_color else "cyan"
        dim_style = "" if no_color else "dim"
        blue_style = "" if no_color else "blue"

        table = Table(show_header=True, header_style=header_style)
        table.add_column("#", justify="right")
        table.add_column("Context")
        table.add_column("Action", style=action_style)
        table.add_column("Goal", justify="center")
        table.add_column("Deps", style=dim_style)
        table.add_column("Shared", justify="right", style=blue_style)

        ctx_fmt = action_fmt.context_formatter

        for i, action_key in enumerate(execution_order, 1):
            node = graph.get_node(action_key)
            is_goal = action_key.id.name in goals

            # Number column
            num_str = str(i)

            # Context column - use formatter for colored output
            context_text = ctx_fmt.format_id_with_symbol(action_key.context_id, use_short_ids)

            # Action column
            action_str = action_key.id.name

            # Goal column
            goal_str = sym.Target if is_goal else ""

            # Dependencies column
            if node.dependencies:
                sorted_deps = sorted(node.dependencies, key=lambda d: d.action.id.name)
                dep_parts = []
                for dep in sorted_deps:
                    if dep.action not in execution_order:
                        continue  # Skip dependencies not in execution order (e.g., pruned soft deps)
                    dep_num = execution_order.index(dep.action) + 1
                    if dep.weak:
                        dep_parts.append(f"~{dep_num}")
                    elif dep.soft:
                        dep_parts.append(f"?{dep_num}")  # Show soft deps with question mark
                    else:
                        dep_parts.append(str(dep_num))
                deps_str = ", ".join(dep_parts) if dep_parts else "-"
            else:
                deps_str = "-"

            # Shared column - show how many contexts share this action
            share_count = sharing_counts.get(action_key, 1)
            shared_str = str(share_count) if share_count > 1 else "-"

            table.add_row(num_str, context_text, action_str, goal_str, deps_str, shared_str)

        # Use output's console to respect no_color setting
        output.console.print(table)
        output.print("")  # Empty line after plan

    def _list_actions(self, document: ParsedDocument, no_color: bool = False) -> None:
        """List all available actions."""
        output = OutputFormatter(no_color=no_color)
        sym = output.symbols

        # Show available axes first
        if document.axis:
            output.print("\n[blue]Available axes:[/blue]\n")

            for axis_name in sorted(document.axis.keys()):
                axis_def = document.axis[axis_name]
                values_parts = []
                for axis_val in axis_def.values:
                    if axis_val.is_default:
                        values_parts.append(f"[bold green]{axis_val.value}[/bold green]*")
                    else:
                        values_parts.append(axis_val.value)
                output.print(f"  [bold cyan]{axis_name}[/bold cyan]: {', '.join(values_parts)}")
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
            if is_root:
                output.print(f"{sym.Target} [bold cyan]{action_name}[/bold cyan]")
            else:
                output.print(f"  [bold cyan]{action_name}[/bold cyan]")

            if action.description:
                for desc_line in action.description.splitlines():
                    stripped_line = desc_line.strip()
                    if stripped_line:
                        output.print(f"    [dim]{stripped_line}[/dim]")

            if typed_deps:
                dep_strs = []
                for dep_name in sorted(typed_deps.keys()):
                    dep_type = typed_deps[dep_name]
                    if dep_type == "weak":
                        dep_strs.append(f"~{dep_name}")
                    elif dep_type == "soft":
                        dep_strs.append(f"?{dep_name}")
                    else:
                        dep_strs.append(dep_name)
                output.print(f"    [dim]Dependencies:[/dim] {', '.join(dep_strs)}")

            args_used = info["args_used"]
            if args_used:
                output.print(f"    [dim]Arguments:[/dim] [bold yellow]{', '.join(sorted(args_used))}[/bold yellow]")

            flags_used = info["flags_used"]
            if flags_used:
                output.print(f"    [dim]Flags:[/dim] [bold yellow]{', '.join(sorted(flags_used))}[/bold yellow]")

            all_env_vars = info["env_vars"]
            if all_env_vars:
                output.print(f"    [dim]Env vars:[/dim] {', '.join(sorted(all_env_vars))}")

            inputs_map = info["inputs"]
            if inputs_map:
                input_parts = []
                for act_name in sorted(inputs_map.keys()):
                    vars_str = ', '.join(sorted(inputs_map[act_name]))
                    input_parts.append(f"{act_name}.{{{vars_str}}}")
                output.print(f"    [dim]Inputs:[/dim] {', '.join(input_parts)}")

            all_returns = info["returns"]
            if all_returns:
                returns_parts = []
                for r in all_returns:
                    returns_parts.append(f"[bold green]{r.name}[/bold green][dim]:{r.return_type.value}[/dim]")
                output.print(f"    [dim]Returns:[/dim] {', '.join(returns_parts)}")

            # Show versions if action has multiple versions
            if len(action.versions) > 1:
                from .ast.models import AxisCondition, PlatformCondition
                version_strs = []
                for ver_i, version in enumerate(action.versions, 1):
                    cond_parts = []
                    for cond in version.conditions:
                        if isinstance(cond, AxisCondition):
                            cond_parts.append(f"{cond.axis_name}: {cond.axis_value}")
                        elif isinstance(cond, PlatformCondition):
                            cond_parts.append(f"platform: {cond.platform_value}")

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
    return cli.run()


if __name__ == "__main__":
    sys.exit(main())
