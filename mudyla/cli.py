"""Command-line interface for Mudyla."""

import argparse
import os
import platform
import sys
import time
from dataclasses import dataclass
from glob import glob
from pathlib import Path
from typing import Optional

from .ast.models import ParsedDocument
from .dag.compiler import DAGCompiler, CompilationError
from .dag.graph import ActionGraph, ActionKey
from .dag.validator import DAGValidator, ValidationError
from .executor.engine import ExecutionEngine, create_run_id
from .logging.terminal_logger import LoggerMode, PlanningReporter, TerminalLogger, create_terminal_logger, resolve_logger_mode
from .executor.retainer_executor import RetainerExecutor
from .executor.process import process_factory
from .parser.markdown_parser import MarkdownParser
from .cli_args import (
    AXIS_OPTIONS,
    ActionInvocation,
    ArgValue,
    CLIParseError,
    parse_custom_inputs,
    ParsedCLIInputs,
)
from .cli_builder import HelpRequested, build_arg_parser
from .axis_wildcards import expand_all_wildcards
from .utils.project_root import find_project_root
from .dag.solver.model import SolverMode


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
        """Orchestrate planning and execution through the same run logger."""
        try:
            args, unknown = self.parser.parse_known_args(argv)
            nix_message = self._apply_platform_defaults(args, args.autocomplete is not None)
        except (HelpRequested, argparse.ArgumentError) as error:
            defaults, _ = self.parser.parse_known_args([])
            logger = self._create_logger(defaults)
            try:
                if isinstance(error, HelpRequested):
                    logger.report_help(self.parser)
                    return 0
                logger.report_usage_error(self.parser, error)
                return 2
            finally:
                logger.finish_run()
        logger = self._create_logger(args)
        if args.autocomplete:
            try:
                return self._handle_autocomplete(args, logger)
            finally:
                logger.finish_run()
        run_id = create_run_id()
        logger.start_run(run_id, nix_message)
        try:
            parsed_inputs = parse_custom_inputs([], unknown)
            processes = process_factory()
            setup = self._prepare_execution_setup(args, parsed_inputs, logger)
            self._validate_required_env(setup.document)
            document = setup.document
            if args.list_actions:
                logger.report_available_actions(document)
                return 0
            parallel = args.parallel or (not args.sequential
                and args.logger not in {"verbose", "github", "teamcity"}
                and not document.properties.sequential_execution_default)
            logger.report_definitions(setup.markdown_files, document, parallel, args.dry_run)
            for warning in setup.parsed_inputs.goal_warnings:
                logger.report_warning(warning)
            planning_start = time.perf_counter()
            compiler = DAGCompiler(document, setup.parsed_inputs)
            compiler.validate_action_invocations()
            graph = compiler.compile()
            logger.report_compilation(graph, (time.perf_counter() - planning_start) * 1000)
            logger.start_retainers()
            retainer_executor = RetainerExecutor(
                graph=graph, document=document, project_root=setup.project_root,
                environment_vars=document.environment_vars, passthrough_env_vars=document.passthrough_env_vars,
                args=setup.custom_args, flags=setup.all_flags, axis_values=setup.axis_values,
                observer=logger, processes=processes, without_nix=args.without_nix, verbose=args.verbose)
            retained_targets, _ = retainer_executor.execute_retainers()
            logger.finish_retainers()
            logger.report_goals(sorted(graph.goals, key=str))
            pruned_graph = graph.prune_to_goals(retained_targets)
            validator = DAGValidator(document, pruned_graph)
            validator.validate_all(setup.custom_args, setup.all_flags, setup.axis_values)
            execution_order = pruned_graph.get_execution_order()
            logger.report_plan(pruned_graph, execution_order, retained_targets)
            if args.dry_run:
                logger.report_dry_run()
                return 0
            previous = self._get_previous_run_dir(setup.project_root, logger) if args.continue_run else None
            engine = ExecutionEngine(graph=pruned_graph, project_root=setup.project_root,
                args=setup.custom_args, flags=setup.all_flags, environment_vars=document.environment_vars,
                passthrough_env_vars=document.passthrough_env_vars,
                run_directory=setup.project_root / ".mdl" / "runs" / run_id, previous_run_directory=previous,
                without_nix=args.without_nix, no_output_on_fail=args.no_out_on_fail,
                keep_run_dir=args.keep_run_dir or logger.keep_running, parallel_execution=parallel,
                keep_running=logger.keep_running, timeout_ms=args.timeout_ms, processes=processes, logger=logger)
            result = engine.execute_all()
            logger.report_outcome(result, args.keep_run_dir)
            if not result.success:
                return 1
            selected = list(pruned_graph.nodes) if args.full_output else list(graph.goals)
            logger.report_outputs(result, selected, full_output=args.full_output,
                                  out_path=Path(args.out) if args.out else None)
            if args.keep_run_dir:
                logger.report_run_directory(result.run_directory)
            return 0
        except BaseException as error:
            if not isinstance(error, Exception):
                logger.finish_run()
                raise
            logger.report_error(error)
            if isinstance(error, ValueError) and "No goals specified" in str(error):
                logger.report_help(self.parser)
            if not isinstance(error, (ValueError, CLIParseError, ValidationError, CompilationError)):
                logger.report_traceback(error)
            return 1
        finally:
            logger.finish_run()

    def _create_logger(self, args: argparse.Namespace) -> TerminalLogger:
        solver: SolverMode | None = ("auto" if args.plan_style == "dag" and args.plan_dag_solver in {None, "grid-auto"}
                                    else args.plan_dag_solver)
        return create_terminal_logger(LoggerMode(args.logger) if args.logger is not None else LoggerMode.PURE,
            no_color=args.no_color, force_interactive=args.force_interactive, interactive=args.interactive,
            fullscreen=args.fullscreen, show_dirs=args.show_dirs, use_short_ids=not args.full_ctx_reprs,
            plan_style=args.plan_style, plan_minimize=args.plan_minimize, dag_solver=solver,
            console=None)

    def _validate_required_env(self, document: ParsedDocument) -> None:
        """Validate that all required environment variables are set."""
        missing_vars = [var for var in document.required_env_vars if var not in os.environ]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

    def _get_previous_run_dir(
        self,
        project_root: Path,
        logger: TerminalLogger,
    ) -> Optional[Path]:
        """Get the most recent run directory for --continue-run mode.

        Args:
            project_root: Project root path
            logger: Terminal logger for continuation messages

        Returns:
            Path to previous run directory, or None if not found
        """
        runs_dir = project_root / ".mdl" / "runs"

        if not runs_dir.exists():
            logger.report_continuation(None, "No runs directory found, starting fresh")
            return None

        run_dirs = sorted([d for d in runs_dir.iterdir() if d.is_dir()])
        if not run_dirs:
            logger.report_continuation(None, "No previous runs found, starting fresh")
            return None

        previous_run_dir = run_dirs[-1]
        logger.report_continuation(previous_run_dir, None)
        return previous_run_dir

    def _apply_platform_defaults(self, args: argparse.Namespace, quiet_mode: bool) -> Optional[str]:
        """Apply platform specific defaults."""
        args.no_color = args.no_color or bool(os.environ.get("NO_COLOR"))
        try:
            mode = resolve_logger_mode(args.logger, args.simple_log is True, args.verbose, args.github_actions, args.teamcity)
        except ValueError as error:
            self.parser.error(str(error))
        args.logger = mode.value
        if args.plan_style != 'dag' and args.plan_dag_solver is not None:
            self.parser.error('--plan-dag-solver requires --plan dag')
        if args.plan_style == 'dag' and args.plan_dag_solver is None:
            args.plan_dag_solver = 'grid-auto'
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
        return None if quiet_mode else f"{state} ({reason})"


    def _handle_autocomplete(self, args: argparse.Namespace, logger: TerminalLogger) -> int:
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

            logger.report_completion(suggestions)
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

    def _prepare_execution_setup(
        self,
        args: argparse.Namespace,
        parsed_inputs: ParsedCLIInputs,
        logger: PlanningReporter,
    ) -> ExecutionSetup:
        """Load markdown definitions and merge CLI inputs with defaults."""
        project_root = find_project_root()
        logger.report_project(project_root, args.without_nix)

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

        self._apply_default_axis_values(document, axis_values, logger)
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
        logger: PlanningReporter,
    ) -> None:
        default_axes: dict[str, str] = {}
        for axis_name, axis_def in document.axis.items():
            if axis_name in axis_values:
                continue
            default_value = axis_def.get_default_value()
            if default_value:
                axis_values[axis_name] = default_value
                default_axes[axis_name] = default_value
        if default_axes:
            logger.report_default_axes(default_axes)

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

    def _list_action_names_ordered(self, document: ParsedDocument) -> list[str]:
        """Return sorted root action names followed by sorted dependent action names."""
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


def main() -> int:
    """Main entry point."""
    cli = CLI()
    try:
        return cli.run()
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
