"""Command-line interface for Mudyla."""

import argparse
import json
import platform
import sys
from glob import glob
from pathlib import Path
from typing import Optional

from .ast.models import ParsedDocument
from .dag.builder import DAGBuilder
from .dag.validator import DAGValidator, ValidationError
from .executor.engine import ExecutionEngine
from .parser.markdown_parser import MarkdownParser
from .cli_args import CLIParseError, parse_custom_inputs
from .cli_builder import build_arg_parser
from .utils.project_root import find_project_root
from .utils.colors import ColorFormatter
from .utils.output import OutputFormatter


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
        # Parse known args first to separate goals from flags/args
        args, unknown = self.parser.parse_known_args(argv)
        quiet_mode = args.autocomplete

        # Auto-enable --without-nix on Windows
        if platform.system() == "Windows" and not args.without_nix:
            args.without_nix = True
            if not quiet_mode:
                print("Note: Running on Windows - automatically enabling --without-nix mode")

        # Auto-enable --no-color for GitHub Actions on Windows only
        # GitHub Actions supports ANSI colors on Linux and macOS
        if args.github_actions and platform.system() == "Windows" and not args.no_color:
            args.no_color = True

        try:
            # Auto-complete mode: quiet, returns only action names.
            if args.autocomplete:
                try:
                    project_root = find_project_root()
                    defs_pattern = args.defs
                    if not Path(defs_pattern).is_absolute():
                        defs_pattern = str(Path(project_root) / defs_pattern)
                    md_files = [Path(p) for p in glob(defs_pattern, recursive=True)]
                    if not md_files:
                        return 1
                    parser = MarkdownParser()
                    document = parser.parse_files(md_files)
                    for name in self._list_action_names_ordered(document):
                        print(name)
                    return 0
                except Exception:
                    return 1

            # Create color formatter
            color = ColorFormatter(no_color=args.no_color)
            output = OutputFormatter(color)

            try:
                parsed_inputs = parse_custom_inputs(args.goals, unknown)
            except CLIParseError as e:
                output.print(f"{output.emoji('❌', '✗')} {color.error('Error:')} {e}")
                return 1

            custom_args = parsed_inputs.custom_args
            custom_flags = parsed_inputs.custom_flags
            axis_values = parsed_inputs.axis_values
            goals = parsed_inputs.goals

            parallel_execution = not args.sequential and not args.verbose and not args.github_actions

            # Find project root
            project_root = find_project_root()
            print(f"{color.dim('Project root:')} {color.highlight(str(project_root))}")

            # Resolve defs pattern
            defs_pattern = args.defs
            if not Path(defs_pattern).is_absolute():
                defs_pattern = str(project_root / defs_pattern)

            # Find markdown files
            md_files = []
            for pattern_part in glob(defs_pattern, recursive=True):
                md_files.append(Path(pattern_part))

            if not md_files:
                output.print(f"{output.emoji('❌', '✗')} {color.error('Error:')} No markdown files found matching pattern: {args.defs}")
                return 1

            # Parse markdown files
            parser = MarkdownParser()
            document = parser.parse_files(md_files)

            # Handle --list-actions
            if args.list_actions:
                self._list_actions(document, args.no_color)
                return 0

            output.print(f"{output.emoji('📚', '▸')} {color.dim('Found')} {color.bold(str(len(md_files)))} {color.dim('definition file(s) with')} {color.bold(str(len(document.actions)))} {color.dim('actions')}")

            # Surface goal formatting warnings (if any)
            for warning in parsed_inputs.goal_warnings:
                output.print(f"{output.emoji('⚠️', '!')} {color.warning('Warning:')} {warning}")

            if not goals:
                output.print(f"{output.emoji('❌', '✗')} {color.error('Error:')} No goals specified")
                self.parser.print_help()
                return 1

            output.print(f"{output.emoji('🎯', '▸')} {color.dim('Goals:')} {color.highlight(', '.join(goals))}")

            # Apply default axis values
            for axis_name, axis_def in document.axis.items():
                if axis_name not in axis_values:
                    default_value = axis_def.get_default_value()
                    if default_value:
                        axis_values[axis_name] = default_value
                        print(f"{color.dim('Using default axis value:')} {color.info(axis_name + '=' + default_value)}")

            # Apply default argument values
            for arg_name, arg_def in document.arguments.items():
                if arg_name not in custom_args and arg_def.default_value is not None:
                    custom_args[arg_name] = arg_def.default_value

            # Build DAG
            builder = DAGBuilder(document)
            builder.validate_goals(goals)
            graph = builder.build_graph(goals, axis_values)

            # Prune to goals
            pruned_graph = graph.prune_to_goals()

            # Validate
            validator = DAGValidator(document, pruned_graph)

            # Initialize flags with all defined flags
            all_flags = {name: False for name in document.flags}
            all_flags.update(custom_flags)

            validator.validate_all(custom_args, all_flags, axis_values)
            if not quiet_mode:
                output.print(f"{output.emoji('✅', '✓')} {color.dim('Built plan graph with')} {color.bold(str(len(pruned_graph.nodes)))} {color.dim('required action(s)')}")
                mode_label = "parallel" if parallel_execution else "sequential"
                output.print(f"{output.emoji('⚙️', '▸')} {color.dim('Execution mode:')} {color.highlight(mode_label)}")

            # Show execution plan
            execution_order = pruned_graph.get_execution_order()
            if not quiet_mode:
                output.print(f"\n{output.emoji('📋', '▸')} {color.bold('Execution plan:')}")
                self._visualize_execution_plan(pruned_graph, execution_order, goals, color, output)

            if args.dry_run:
                output.print(f"\n{output.emoji('ℹ️', 'i')} {color.info('Dry run - not executing')}")
                return 0

            # Find previous run if --continue
            previous_run_dir = None
            if args.continue_run:
                runs_dir = project_root / ".mdl" / "runs"
                if runs_dir.exists():
                    # List all run directories (sorted by name, which is timestamp-based)
                    run_dirs = sorted([d for d in runs_dir.iterdir() if d.is_dir()])
                    if run_dirs:
                        previous_run_dir = run_dirs[-1]  # Last run
                        output.print(f"\n{output.emoji('🔄', '▸')} {color.info('Continuing from previous run:')} {color.highlight(previous_run_dir.name)}")
                    else:
                        output.print(f"\n{output.emoji('⚠️', '!')} {color.warning('Warning:')} No previous runs found, starting fresh")
                else:
                    output.print(f"\n{output.emoji('⚠️', '!')} {color.warning('Warning:')} No runs directory found, starting fresh")

            # Execute
            output.print(f"\n{output.emoji('🚀', '→')} {color.bold('Executing actions...')}")

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
                parallel_execution=parallel_execution,
            )

            result = engine.execute_all()

            if not result.success:
                output.print(f"\n{output.emoji('❌', '✗')} {color.error('Execution failed!')}")
                return 1

            # Get goal outputs
            goal_outputs = result.get_goal_outputs(goals)

            # Print outputs
            output.print(f"\n{output.emoji('✅', '✓')} {color.success('Execution completed successfully!')}")

            output_json = json.dumps(goal_outputs, indent=2)
            output.print(f"\n{output.emoji('📊', '▸')} {color.bold('Outputs:')}")

            # Print colorized JSON if colors are enabled
            if not args.no_color:
                from rich.console import Console
                from rich.json import JSON
                console = Console()
                console.print(JSON(output_json))
            else:
                output.print(output_json)

            # Save to file if requested
            if args.out:
                out_path = Path(args.out)
                out_path.write_text(output_json)
                output.print(f"\n{output.emoji('💾', '▸')} {color.dim('Outputs saved to:')} {color.highlight(str(out_path))}")

            # Show run directory if keeping it
            if args.keep_run_dir:
                output.print(f"\n{output.emoji('📂', '▸')} {color.dim('Run directory:')} {color.highlight(str(result.run_directory))}")

            return 0

        except ValidationError as e:
            # Use safe printing for errors
            try:
                output.print(f"\n{output.emoji('❌', '✗')} {color.error('Validation error:')}\n{e}")
            except (NameError, UnicodeEncodeError):
                # Fallback if output not defined or encoding fails
                print(f"\n[!] Validation error:\n{e}")
            return 1
        except Exception as e:
            try:
                output.print(f"\n{output.emoji('❌', '✗')} {color.error('Error:')} {e}")
            except (NameError, UnicodeEncodeError):
                # Fallback if output not defined or encoding fails
                print(f"\n[!] Error: {e}")
            import traceback

            traceback.print_exc()
            return 1

    def _visualize_execution_plan(self, graph, execution_order: list[str], goals: list[str], color, output: OutputFormatter) -> None:
        """Visualize execution plan as a tree.

        Args:
            graph: The execution graph
            execution_order: List of actions in execution order
            goals: List of goal actions
            color: Color formatter
            output: Output formatter
        """
        # Create a tree-like visualization showing dependencies
        for i, action_name in enumerate(execution_order, 1):
            node = graph.get_node(action_name)
            is_goal = action_name in goals
            goal_marker = f" {output.emoji('🎯', '[GOAL]')}" if is_goal else ""

            # Format the action with its number
            action_label = f"{i}. {action_name}{goal_marker}"

            if not node.dependencies:
                # No dependencies - just print the action
                output.print(f"  {color.highlight(action_label)}")
            else:
                # Has dependencies - show them
                dep_names = []
                for dep in sorted(node.dependencies):
                    dep_num = execution_order.index(dep) + 1
                    dep_names.append(f"{dep_num}")

                deps_str = ",".join(dep_names)
                arrow = output.emoji("←", "<-")
                output.print(f"  {color.highlight(action_label)} {color.dim(f'{arrow} [{deps_str}]')}")

        output.print("")  # Empty line after plan

    def _list_actions(self, document: ParsedDocument, no_color: bool = False) -> None:
        """List all available actions."""
        from .utils.colors import ColorFormatter

        color = ColorFormatter(no_color=no_color)
        print(f"\n{color.info('Available actions:')}\n")

        # Separate actions into root (no dependencies) and non-root
        root_actions = []
        non_root_actions = []

        for action_name, action in document.actions.items():
            deps = action.get_action_dependencies()
            if len(deps) == 0:
                root_actions.append(action_name)
            else:
                non_root_actions.append(action_name)

        # Sort each group alphabetically
        root_actions.sort()
        non_root_actions.sort()

        # Display root actions first, then non-root actions
        for action_name in root_actions + non_root_actions:
            action = document.actions[action_name]
            deps = action.get_action_dependencies()
            is_root = len(deps) == 0

            # Format action name
            if is_root:
                # Root actions are bold with goal emoji
                formatted_name = f"🎯 {color.bold(color.highlight(action_name))}"
            else:
                formatted_name = f"  {color.highlight(action_name)}"

            print(formatted_name)

            # Dependencies
            if deps:
                dep_str = ', '.join(sorted(deps))
                print(f"    {color.dim('Dependencies:')} {dep_str}")

            # Arguments and flags used
            args_used = set()
            flags_used = set()
            env_vars_used = set()

            for expansion in action.get_all_expansions():
                from .ast.expansions import ArgsExpansion, FlagsExpansion, EnvExpansion

                if isinstance(expansion, ArgsExpansion):
                    args_used.add(expansion.argument_name)
                elif isinstance(expansion, FlagsExpansion):
                    flags_used.add(expansion.flag_name)
                elif isinstance(expansion, EnvExpansion):
                    env_vars_used.add(expansion.variable_name)

            if args_used:
                args_str = ', '.join(sorted(args_used))
                print(f"    {color.dim('Arguments:')} {color.warning(args_str)}")
            if flags_used:
                flags_str = ', '.join(sorted(flags_used))
                print(f"    {color.dim('Flags:')} {color.warning(flags_str)}")

            # Env vars
            all_env_vars = set(action.required_env_vars.keys()) | env_vars_used
            if all_env_vars:
                env_str = ', '.join(sorted(all_env_vars))
                print(f"    {color.dim('Env vars:')} {env_str}")

            # Inputs (consumed action outputs)
            inputs_map = {}  # action_name -> set of variable names
            for expansion in action.get_all_expansions():
                from .ast.expansions import ActionExpansion
                if isinstance(expansion, ActionExpansion):
                    if expansion.action_name not in inputs_map:
                        inputs_map[expansion.action_name] = set()
                    inputs_map[expansion.action_name].add(expansion.variable_name)

            if inputs_map:
                input_strs = []
                for act_name in sorted(inputs_map.keys()):
                    vars_str = ', '.join(sorted(inputs_map[act_name]))
                    input_strs.append(f"{color.highlight(act_name)}.{{{vars_str}}}")
                print(f"    {color.dim('Inputs:')} {', '.join(input_strs)}")

            # Returns (collect across all versions)
            all_returns = {}  # name -> ReturnDeclaration (deduplicate by name)
            for version in action.versions:
                for ret_decl in version.return_declarations:
                    all_returns[ret_decl.name] = ret_decl

            if all_returns:
                return_strs = [
                    f"{color.success(r.name)}:{color.dim(r.return_type.value)}"
                    for r in all_returns.values()
                ]
                print(f"    {color.dim('Returns:')} {', '.join(return_strs)}")

            # Axis (show if any version has axis conditions)
            axis_names = action.get_required_axis()
            if axis_names:
                axis_str = ', '.join(sorted(axis_names))
                print(f"    {color.dim('Axis:')} {color.warning(axis_str)}")

            print()

    def _list_action_names_ordered(self, document: ParsedDocument) -> list[str]:
        """Return action names in the same order as _list_actions prints them."""
        root_actions = []
        non_root_actions = []

        for action_name, action in document.actions.items():
            deps = action.get_action_dependencies()
            if len(deps) == 0:
                root_actions.append(action_name)
            else:
                non_root_actions.append(action_name)

        root_actions.sort()
        non_root_actions.sort()
        return root_actions + non_root_actions


def main() -> int:
    """Main entry point."""
    cli = CLI()
    return cli.run()


if __name__ == "__main__":
    sys.exit(main())
