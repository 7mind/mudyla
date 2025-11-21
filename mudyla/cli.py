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
from .utils.project_root import find_project_root
from .utils.colors import ColorFormatter
from .utils.output import OutputFormatter


class CLI:
    """Command-line interface for Mudyla."""

    def __init__(self):
        self.parser = argparse.ArgumentParser(
            description="Mudyla - Multimodal Dynamic Launcher",
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )

        self.parser.add_argument(
            "--defs",
            type=str,
            default=".mdl/defs/**/*.md",
            help="Glob pattern for markdown definition files (default: .mdl/defs/**/*.md)",
        )

        self.parser.add_argument(
            "--out",
            type=str,
            help="Output JSON file path (optional, always prints to stdout)",
        )

        self.parser.add_argument(
            "--list-actions",
            action="store_true",
            help="List all available actions and exit",
        )

        self.parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show execution plan without executing",
        )

        self.parser.add_argument(
            "--continue",
            dest="continue_run",
            action="store_true",
            help="Continue from last run (skip successful actions)",
        )

        self.parser.add_argument(
            "--github-actions",
            dest="github_actions",
            action="store_true",
            help="Enable GitHub Actions integration (collapsible groups, streaming output)",
        )

        self.parser.add_argument(
            "--without-nix",
            dest="without_nix",
            action="store_true",
            help="Run without Nix (execute bash scripts directly, auto-enabled on Windows)",
        )

        self.parser.add_argument(
            "--verbose",
            dest="verbose",
            action="store_true",
            help="Stream action output to console in real-time (without GitHub Actions markers)",
        )

        self.parser.add_argument(
            "--keep-run-dir",
            dest="keep_run_dir",
            action="store_true",
            help="Keep the run directory after successful execution (for debugging)",
        )

        self.parser.add_argument(
            "--no-color",
            dest="no_color",
            action="store_true",
            help="Disable colored output (auto-enabled for GitHub Actions)",
        )

        self.parser.add_argument(
            "goals",
            nargs="*",
            help="Goal actions to execute (format: :action-name)",
        )

    def run(self, argv: Optional[list[str]] = None) -> int:
        """Run the CLI.

        Args:
            argv: Command-line arguments (defaults to sys.argv[1:])

        Returns:
            Exit code
        """
        # Parse known args first to separate goals from flags/args
        args, unknown = self.parser.parse_known_args(argv)

        # Auto-enable --without-nix on Windows
        if platform.system() == "Windows" and not args.without_nix:
            args.without_nix = True
            print("Note: Running on Windows - automatically enabling --without-nix mode")

        # Auto-enable --no-color for GitHub Actions
        if args.github_actions and not args.no_color:
            args.no_color = True

        # Create color formatter
        color = ColorFormatter(no_color=args.no_color)
        output = OutputFormatter(color)

        # Parse custom arguments, flags, and axis
        custom_args = {}
        custom_flags = {}
        axis_values = {}

        i = 0
        while i < len(unknown):
            arg = unknown[i]

            if arg.startswith("--axis"):
                # Format: --axis name=value or --axis=name=value
                # The value part may be in args.goals, so we handle it there
                # If it's --axis=name=value format, handle it here
                if "=" in arg:
                    rest = arg[6:]  # Remove "--axis"
                    if rest.startswith("="):
                        rest = rest[1:]
                    if "=" in rest:
                        axis_name, axis_value = rest.split("=", 1)
                        axis_values[axis_name.strip()] = axis_value.strip()
                # Otherwise, the value (name=value) will be in args.goals and handled below

            elif arg.startswith("--"):
                # Could be flag or argument
                arg_name = arg[2:]
                if "=" in arg_name:
                    # Argument: --name=value
                    name, value = arg_name.split("=", 1)
                    custom_args[name] = value
                else:
                    # Flag: --name
                    custom_flags[arg_name] = True

            i += 1

        # Filter out axis specifications and misplaced arguments from goals
        # (they might have been consumed by argparse as positional args)
        filtered_goals = []
        for goal in args.goals:
            if goal.startswith("--"):
                # This is an argument or flag that ended up in goals
                if "=" in goal:
                    # Argument: --name=value
                    arg_name = goal[2:].split("=", 1)[0]
                    arg_value = goal[2:].split("=", 1)[1]
                    custom_args[arg_name] = arg_value
                else:
                    # Flag: --name
                    flag_name = goal[2:]
                    custom_flags[flag_name] = True
            elif "=" in goal and not goal.startswith(":"):
                # Axis specification (format: name=value without leading : or --)
                axis_name, axis_value = goal.split("=", 1)
                axis_values[axis_name.strip()] = axis_value.strip()
            else:
                # Actual goal
                filtered_goals.append(goal)
        args.goals = filtered_goals

        try:
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

            output.print(f"{output.emoji('📚', '▸')} {color.dim('Found')} {color.bold(str(len(md_files)))} {color.dim('definition file(s) with')} {color.bold(str(len(document.actions)))} {color.dim('actions')}")

            # Handle --list-actions
            if args.list_actions:
                self._list_actions(document)
                return 0

            # Parse goals
            goals = []
            for goal_spec in args.goals:
                if goal_spec.startswith(":"):
                    goals.append(goal_spec[1:])
                else:
                    output.print(f"{output.emoji('⚠️', '!')} {color.warning('Warning:')} Goal should start with ':', got: {goal_spec}")
                    goals.append(goal_spec)

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
            output.print(f"{output.emoji('✅', '✓')} {color.dim('Built plan graph with')} {color.bold(str(len(pruned_graph.nodes)))} {color.dim('required action(s)')}")

            # Show execution plan
            execution_order = pruned_graph.get_execution_order()
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

    def _list_actions(self, document: ParsedDocument) -> None:
        """List all available actions."""
        print("\nAvailable actions:\n")

        for action_name in sorted(document.actions.keys()):
            action = document.actions[action_name]
            print(f"  {action_name}")

            # Dependencies
            deps = action.get_action_dependencies()
            if deps:
                print(f"    Dependencies: {', '.join(sorted(deps))}")

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
                print(f"    Arguments: {', '.join(sorted(args_used))}")
            if flags_used:
                print(f"    Flags: {', '.join(sorted(flags_used))}")

            # Env vars
            all_env_vars = set(action.required_env_vars.keys()) | env_vars_used
            if all_env_vars:
                print(f"    Env vars: {', '.join(sorted(all_env_vars))}")

            # Returns
            if action.versions:
                returns = action.versions[0].return_declarations
                if returns:
                    return_strs = [
                        f"{r.name}:{r.return_type.value}" for r in returns
                    ]
                    print(f"    Returns: {', '.join(return_strs)}")

            # Axis
            if action.is_multi_version:
                axis_names = action.get_required_axis()
                print(f"    Axis: {', '.join(sorted(axis_names))}")

            print()


def main() -> int:
    """Main entry point."""
    cli = CLI()
    return cli.run()


if __name__ == "__main__":
    sys.exit(main())
