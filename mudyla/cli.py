"""Command-line interface for Mudyla."""

import argparse
import json
import platform
import sys
from dataclasses import dataclass
from glob import glob
from pathlib import Path
from typing import Optional

from .ast.models import ParsedDocument, ActionDefinition
from .dag.builder import DAGBuilder
from .dag.compiler import DAGCompiler, CompilationError
from .dag.validator import DAGValidator, ValidationError
from .executor.engine import ExecutionEngine
from .parser.markdown_parser import MarkdownParser
from .cli_args import (
    AXIS_OPTIONS,
    CLIParseError,
    parse_custom_inputs,
    ParsedCLIInputs,
)
from .cli_builder import build_arg_parser
from .axis_wildcards import expand_all_wildcards
from .utils.project_root import find_project_root
from .utils.colors import ColorFormatter
from .utils.context_ids import build_context_mapping, format_action_label
from .utils.output import OutputFormatter
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

        color, output = self._build_formatters(args.no_color)

        try:
            # All arguments (goals, axes, args, flags) are in 'unknown' since we don't
            # define a positional 'goals' parameter in argparse (to preserve order)
            parsed_inputs = parse_custom_inputs([], unknown)
        except CLIParseError as e:
            output.print(f"{output.emoji('❌', '✗')} {color.error('Error:')} {e}")
            return 1

        try:
            setup = self._prepare_execution_setup(args, parsed_inputs, color, output)

            document = setup.document
            goals = setup.goals
            custom_args = setup.custom_args
            axis_values = setup.axis_values
            all_flags = setup.all_flags
            project_root = setup.project_root

            if args.list_actions:
                self._list_actions(document, args.no_color)
                return 0

            parallel_execution = not args.sequential and not args.verbose and not args.github_actions

            output.print(f"{output.emoji('📚', '▸')} {color.dim('Found')} {color.bold(str(len(setup.markdown_files)))} {color.dim('definition file(s) with')} {color.bold(str(len(document.actions)))} {color.dim('actions')}")

            for warning in setup.parsed_inputs.goal_warnings:
                output.print(f"{output.emoji('⚠️', '!')} {color.warning('Warning:')} {warning}")

            # Use the new compiler for multi-context support
            compiler = DAGCompiler(document, setup.parsed_inputs)
            compiler.validate_action_invocations()
            graph = compiler.compile()
            pruned_graph = graph.prune_to_goals()

            # Build context mapping for short IDs (unless full representations requested)
            use_short_ids = not args.full_ctx_reprs
            context_mapping: dict[str, str] = {}
            if use_short_ids:
                context_mapping = build_context_mapping(pruned_graph.nodes.keys())

            # Show execution mode
            if not quiet_mode:
                mode_label = "dry-run" if args.dry_run else ("parallel" if parallel_execution else "sequential")
                output.print(f"{output.emoji('⚙️', '▸')} {color.dim('Execution mode:')} {color.highlight(mode_label)}")

            validator = DAGValidator(document, pruned_graph)
            validator.validate_all(custom_args, all_flags, axis_values)
            if not quiet_mode:
                output.print(f"{output.emoji('✅', '✓')} {color.dim('Built plan graph with')} {color.bold(str(len(pruned_graph.nodes)))} {color.dim('required action(s)')}")

            # Show fully-qualified goals with contexts
            goal_keys = sorted(graph.goals, key=str)
            if use_short_ids and context_mapping:
                goal_strs = [
                    color.format_action_key(format_action_label(goal, use_short_ids=True))
                    for goal in goal_keys
                ]
            else:
                goal_strs = [color.format_action_key(str(goal)) for goal in goal_keys]
            goal_keys_str = ", ".join(goal_strs)

            # Display context mapping if using short IDs
            if use_short_ids and context_mapping:
                output.print(f"\n{output.emoji('🔗', '▸')} {color.bold('Contexts:')}")
                for short_id in sorted(context_mapping.keys()):
                    full_ctx = context_mapping[short_id]
                    # Apply consistent coloring to context ID and full context
                    short_id_colored = color.format_short_context_id(short_id)
                    full_ctx_colored = color.format_context_string(full_ctx)
                    output.print(f"  {short_id_colored}: {full_ctx_colored}")

            output.print(f"\n{output.emoji('🎯', '▸')} {color.dim('Goals:')} {goal_keys_str}")

            execution_order = pruned_graph.get_execution_order()
            if not quiet_mode:
                output.print(f"\n{output.emoji('📋', '▸')} {color.bold('Execution plan:')}")
                self._visualize_execution_plan(pruned_graph, execution_order, goals, color, output, use_short_ids)

            if args.dry_run:
                output.print(f"\n{output.emoji('ℹ️', 'i')} {color.info('Dry run - not executing')}")
                return 0

            previous_run_dir = None
            if args.continue_run:
                runs_dir = project_root / ".mdl" / "runs"
                if runs_dir.exists():
                    run_dirs = sorted([d for d in runs_dir.iterdir() if d.is_dir()])
                    if run_dirs:
                        previous_run_dir = run_dirs[-1]
                        output.print(f"\n{output.emoji('🔄', '▸')} {color.info('Continuing from previous run:')} {color.highlight(previous_run_dir.name)}")
                    else:
                        output.print(f"\n{output.emoji('⚠️', '!')} {color.warning('Warning:')} No previous runs found, starting fresh")
                else:
                    output.print(f"\n{output.emoji('⚠️', '!')} {color.warning('Warning:')} No runs directory found, starting fresh")

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
                keep_run_dir=args.keep_run_dir,
                no_color=args.no_color,
                simple_log=args.simple_log,
                show_dirs=args.show_dirs,
                parallel_execution=parallel_execution,
                use_short_context_ids=use_short_ids,
                context_id_mapping=context_mapping,
            )

            # Print run ID
            run_id = engine.run_directory.name
            output.print(f"\n{output.emoji('🆔', '▸')} {color.dim('Run ID:')} {color.highlight(run_id)}")
            output.print(f"{output.emoji('🚀', '→')} {color.bold('Executing actions...')}")

            result = engine.execute_all()
            if not result.success:
                output.print(f"\n{output.emoji('❌', '✗')} {color.error('Execution failed!')}")
                return 1

            # Get outputs using ActionKeys (with context) instead of just action names
            goal_outputs = result.get_goal_outputs(graph.goals)
            output.print(f"\n{output.emoji('✅', '✓')} {color.success('Execution completed successfully!')}")

            output_json = json.dumps(goal_outputs, indent=2)
            output.print(f"\n{output.emoji('📊', '▸')} {color.bold('Outputs:')}")

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
                output.print(f"\n{output.emoji('💾', '▸')} {color.dim('Outputs saved to:')} {color.highlight(str(out_path))}")

            if args.keep_run_dir:
                output.print(f"\n{output.emoji('📂', '▸')} {color.dim('Run directory:')} {color.highlight(str(result.run_directory))}")

            return 0

        except ValueError as err:
            output.print(f"{output.emoji('❌', '✗')} {color.error('Error:')} {err}")
            if "No goals specified" in str(err):
                self.parser.print_help()
            return 1
        except ValidationError as e:
            try:
                output.print(f"\n{output.emoji('❌', '✗')} {color.error('Validation error:')}\n{e}")
            except (NameError, UnicodeEncodeError):
                print(f"\n[!] Validation error:\n{e}")
            return 1
        except CompilationError as e:
            try:
                output.print(f"\n{output.emoji('❌', '✗')} {color.error('Compilation error:')}\n{e}")
            except (NameError, UnicodeEncodeError):
                print(f"\n[!] Compilation error:\n{e}")
            return 1
        except Exception as e:
            try:
                output.print(f"\n{output.emoji('❌', '✗')} {color.error('Error:')} {e}")
            except (NameError, UnicodeEncodeError):
                print(f"\n[!] Error: {e}")
            import traceback

            traceback.print_exc()
            return 1

    def _apply_platform_defaults(self, args: argparse.Namespace, quiet_mode: bool) -> None:
        """Apply platform specific defaults."""
        system = platform.system()
        if system == "Windows" and not args.without_nix:
            args.without_nix = True
            if not quiet_mode:
                print("Note: Running on Windows - automatically enabling --without-nix mode")

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
            else:
                return 1

            for name in suggestions:
                print(name)
            return 0
        except Exception:
            return 1

    def _build_formatters(self, no_color: bool) -> tuple[ColorFormatter, OutputFormatter]:
        color = ColorFormatter(no_color=no_color)
        output = OutputFormatter(color)
        return color, output

    def _prepare_execution_setup(
        self,
        args: argparse.Namespace,
        parsed_inputs: ParsedCLIInputs,
        color: ColorFormatter,
        output: OutputFormatter,
    ) -> ExecutionSetup:
        """Load markdown definitions and merge CLI inputs with defaults."""
        project_root = find_project_root()
        output.print(f"{color.dim('Project root:')} {color.highlight(str(project_root))}")

        md_files = self._discover_markdown_files(args.defs, project_root)
        if not md_files:
            raise ValueError(f"No markdown files found matching pattern: {args.defs}")

        parser = MarkdownParser()
        document = parser.parse_files(md_files)

        # Expand wildcards in axis specifications
        parsed_inputs = expand_all_wildcards(parsed_inputs, document)

        custom_args = dict(parsed_inputs.custom_args)
        axis_values = dict(parsed_inputs.axis_values)
        goals = list(parsed_inputs.goals)

        self._apply_default_axis_values(document, axis_values, color)
        self._apply_default_argument_values(document, custom_args)

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
        matches = [Path(path) for path in glob(str(pattern), recursive=True)]
        return matches

    def _apply_default_axis_values(
        self,
        document: ParsedDocument,
        axis_values: dict[str, str],
        color: ColorFormatter,
    ) -> None:
        for axis_name, axis_def in document.axis.items():
            if axis_name in axis_values:
                continue
            default_value = axis_def.get_default_value()
            if default_value:
                axis_values[axis_name] = default_value
                # Format axis name and value with consistent colors
                from .utils.colors import Colors
                axis_colored = color.colorize(axis_name, Colors.MAGENTA)
                separator = color.dim(":")
                value_colored = color.colorize(default_value, Colors.YELLOW)
                print(f"{color.dim('Using default axis value:')} {axis_colored}{separator}{value_colored}")

    def _apply_default_argument_values(
        self,
        document: ParsedDocument,
        custom_args: dict[str, str],
    ) -> None:
        for arg_name, arg_def in document.arguments.items():
            if arg_name in custom_args:
                continue
            if arg_def.default_value is not None:
                custom_args[arg_name] = arg_def.default_value

    def _visualize_execution_plan(
        self,
        graph,
        execution_order,
        goals: list[str],
        color,
        output: OutputFormatter,
        use_short_ids: bool = False,
    ) -> None:
        """Visualize execution plan as a tree.

        Args:
            graph: The execution graph
            execution_order: List of action keys in execution order
            goals: List of goal action names
            color: Color formatter
            output: Output formatter
            use_short_ids: Whether to use short context IDs
        """
        # Create a tree-like visualization showing dependencies
        for i, action_key in enumerate(execution_order, 1):
            action_name = format_action_label(action_key, use_short_ids=use_short_ids)
            node = graph.get_node(action_key)
            is_goal = action_key.id.name in goals
            goal_marker = f" {output.emoji('🎯', '[GOAL]')}" if is_goal else ""

            # Format action key with colors using the helper function
            action_colored = color.format_action_key(action_name)
            formatted_label = f"{i:>2}. {action_colored}{goal_marker}"

            if not node.dependencies:
                # No dependencies - just print the action
                output.print(f"  {formatted_label}")
            else:
                # Has dependencies - show them
                dep_names = []
                # Sort dependencies by their action name for deterministic output
                sorted_deps = sorted(node.dependencies, key=lambda d: d.action.id.name)
                for dep in sorted_deps:
                    dep_num = execution_order.index(dep.action) + 1
                    weak_marker = "(weak)" if dep.weak else ""
                    dep_names.append(f"{dep_num}{weak_marker}")

                deps_str = ",".join(dep_names)
                arrow = output.emoji("←", "<-")
                output.print(f"  {formatted_label} {color.dim(f'{arrow} [{deps_str}]')}")

        output.print("")  # Empty line after plan

    def _list_actions(self, document: ParsedDocument, no_color: bool = False) -> None:
        """List all available actions."""
        from .utils.colors import ColorFormatter

        color = ColorFormatter(no_color=no_color)

        # Show available axes first
        if document.axis:
            print(f"\n{color.info('Available axes:')}\n")
            for axis_name in sorted(document.axis.keys()):
                axis_def = document.axis[axis_name]

                # Format values with default marked
                value_strs = []
                for axis_val in axis_def.values:
                    if axis_val.is_default:
                        value_strs.append(f"{color.success(axis_val.value)}*")
                    else:
                        value_strs.append(axis_val.value)

                values_str = ', '.join(value_strs)
                print(f"  {color.highlight(axis_name)}: {values_str}")
            print()

        print(f"{color.info('Available actions:')}\n")

        root_actions, non_root_actions = self._partition_actions(document)
        metadata = {
            name: self._collect_action_metadata(action)
            for name, action in document.actions.items()
        }

        # Display root actions first, then non-root actions
        for action_name in root_actions + non_root_actions:
            action = document.actions[action_name]
            info = metadata[action_name]
            deps = info["dependencies"]
            is_root = len(deps) == 0

            # Format action name
            if is_root:
                # Root actions are bold with goal emoji
                formatted_name = f"🎯 {color.bold(color.highlight(action_name))}"
            else:
                formatted_name = f"  {color.highlight(action_name)}"

            print(formatted_name)

            if action.description:
                for line in action.description.splitlines():
                    stripped_line = line.strip()
                    if stripped_line:
                        print(f"    {color.dim(stripped_line)}")

            if deps:
                dep_str = ', '.join(sorted(deps))
                print(f"    {color.dim('Dependencies:')} {dep_str}")

            args_used = info["args_used"]
            if args_used:
                args_str = ', '.join(sorted(args_used))
                print(f"    {color.dim('Arguments:')} {color.warning(args_str)}")
            flags_used = info["flags_used"]
            if flags_used:
                flags_str = ', '.join(sorted(flags_used))
                print(f"    {color.dim('Flags:')} {color.warning(flags_str)}")

            all_env_vars = info["env_vars"]
            if all_env_vars:
                env_str = ', '.join(sorted(all_env_vars))
                print(f"    {color.dim('Env vars:')} {env_str}")

            inputs_map = info["inputs"]
            if inputs_map:
                input_strs = []
                for act_name in sorted(inputs_map.keys()):
                    vars_str = ', '.join(sorted(inputs_map[act_name]))
                    input_strs.append(f"{color.highlight(act_name)}.{{{vars_str}}}")
                print(f"    {color.dim('Inputs:')} {', '.join(input_strs)}")

            all_returns = info["returns"]
            if all_returns:
                return_strs = [
                    f"{color.success(r.name)}:{color.dim(r.return_type.value)}"
                    for r in all_returns
                ]
                print(f"    {color.dim('Returns:')} {', '.join(return_strs)}")

            # Show versions if action has multiple versions
            if len(action.versions) > 1:
                from .ast.models import AxisCondition, PlatformCondition
                version_strs = []
                for i, version in enumerate(action.versions, 1):
                    cond_parts = []
                    for cond in version.conditions:
                        if isinstance(cond, AxisCondition):
                            cond_parts.append(f"{cond.axis_name}: {cond.axis_value}")
                        elif isinstance(cond, PlatformCondition):
                            cond_parts.append(f"platform: {cond.platform_value}")

                    if cond_parts:
                        version_strs.append(f"{i} ({', '.join(cond_parts)})")
                    else:
                        version_strs.append(str(i))

                print(f"    {color.dim('Versions:')} {', '.join(version_strs)}")

            print()

    def _list_action_names_ordered(self, document: ParsedDocument) -> list[str]:
        """Return action names in the same order as _list_actions prints them."""
        root_actions, non_root_actions = self._partition_actions(document)
        return root_actions + non_root_actions

    def _partition_actions(self, document: ParsedDocument) -> tuple[list[str], list[str]]:
        root_actions: list[str] = []
        non_root_actions: list[str] = []
        for action_name, action in document.actions.items():
            deps = action.get_action_dependencies()
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
