"""Execution engine for running actions."""

import json
import os
import concurrent.futures
import shutil
import subprocess
import time
import threading
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..ast.types import ReturnType
from ..ast.models import ActionDefinition, ActionVersion
from ..dag.graph import ActionGraph, ActionKey
from ..utils.colors import ColorFormatter
from ..utils.output import OutputFormatter
from .runtime_registry import RuntimeRegistry
from .bash_runtime import BashRuntime
from .python_runtime import PythonRuntime
from .language_runtime import ExecutionContext, LanguageRuntime


@dataclass
class ActionResult:
    """Result of executing a single action."""

    action_name: str
    success: bool
    outputs: dict[str, Any]
    stdout_path: Path
    stderr_path: Path
    script_path: Path
    start_time: str
    end_time: str
    duration_seconds: float
    exit_code: int = 0
    error_message: Optional[str] = None
    restored: bool = False
    stdout_size: int = 0
    stderr_size: int = 0


@dataclass
class ExecutionResult:
    """Result of executing all actions."""

    success: bool
    action_results: dict[str, ActionResult]
    run_directory: Path

    def get_goal_outputs(self, goals: list[str]) -> dict[str, dict[str, Any]]:
        """Get outputs for goal actions.

        Args:
            goals: List of goal action names

        Returns:
            Dictionary mapping action name to outputs
        """
        result = {}
        for goal in goals:
            # Find matching action results (may be keyed by simple name or "context#name")
            for key, action_result in self.action_results.items():
                # Extract action name from key (format: "name" or "context#name")
                action_name = key.split("#")[-1] if "#" in key else key
                if action_name == goal:
                    result[goal] = action_result.outputs
                    break
        return result


@dataclass
class PreparedAction:
    """Artifacts required to execute an action."""

    action: ActionDefinition
    version: ActionVersion
    runtime: LanguageRuntime
    context: ExecutionContext
    action_dir: Path
    script_path: Path
    stdout_path: Path
    stderr_path: Path
    output_json_path: Path
    exec_cmd: list[str]


class ExecutionEngine:
    """Engine for executing actions in a DAG with context support.

    NOTE: In the multi-context system, axis_values are no longer global.
    Each action has its own context embedded in its ActionKey. The args
    and flags parameters are kept for backward compatibility but may be
    overridden per-action in the future.
    """

    def __init__(
        self,
        graph: ActionGraph,
        project_root: Path,
        args: dict[str, str],
        flags: dict[str, bool],
        axis_values: dict[str, str],  # Deprecated - kept for backward compat
        environment_vars: dict[str, str],
        passthrough_env_vars: list[str],
        run_directory: Optional[Path] = None,
        previous_run_directory: Optional[Path] = None,
        github_actions: bool = False,
        without_nix: bool = False,
        verbose: bool = False,
        keep_run_dir: bool = False,
        no_color: bool = False,
        simple_log: bool = False,
        parallel_execution: bool = True,
    ):
        self.graph = graph
        self.project_root = project_root
        self.args = args  # May be global or per-context
        self.flags = flags  # May be global or per-context
        # Note: axis_values parameter kept for compatibility but not used
        # Each action's axes are now in its ActionKey.context_id
        self.environment_vars = environment_vars
        self.passthrough_env_vars = passthrough_env_vars
        self.previous_run_directory = previous_run_directory
        self.github_actions = github_actions
        self.without_nix = without_nix
        self.verbose = verbose
        self.keep_run_dir = keep_run_dir
        self.no_color = no_color
        self.simple_log = simple_log
        self.parallel_execution = parallel_execution

        # Determine if we should use rich table
        # Use simple log if: --simple-log, --no-color, --github-actions, --verbose, or not a TTY
        self.use_rich_table = not (simple_log or no_color or github_actions or verbose)

        # Create color formatter and output formatter
        self.color = ColorFormatter(no_color=no_color)
        self.output = OutputFormatter(self.color)

        # Register built-in runtimes once.
        for runtime_cls in (BashRuntime, PythonRuntime):
            RuntimeRegistry.ensure_registered(runtime_cls)

        # Generate run directory with nanosecond-grained timestamp
        if run_directory is None:
            # Use nanosecond timestamp for ordering
            now = datetime.now()
            timestamp = now.strftime("%Y%m%d-%H%M%S")
            nanoseconds = time.time_ns() % 1_000_000_000  # Get nanoseconds within current second
            run_id = f"{timestamp}-{nanoseconds:09d}"
            self.run_directory = project_root / ".mdl" / "runs" / run_id
        else:
            self.run_directory = run_directory

        self.run_directory.mkdir(parents=True, exist_ok=True)

    def _print_action_start(self, action_name: str) -> None:
        """Print action start message.

        Args:
            action_name: Name of the action starting
        """
        if not self.github_actions:
            action_colored = self.color.format_action_key(action_name)
            self.output.print(f"{self.color.dim('start:')} {action_colored}")

    def _print_action_completion(self, result: ActionResult) -> None:
        """Print action completion message with duration.

        Args:
            result: The completed action result
        """
        if not self.github_actions:
            action_colored = self.color.format_action_key(result.action_name)
            duration_str = f"{result.duration_seconds:.1f}s"
            restored_str = " (restored from previous run)" if result.restored else ""
            if result.success:
                emoji = self.output.emoji('♻️', '✓') if result.restored else ""
                self.output.print(f"{emoji} {self.color.dim('done:')} {action_colored} {self.color.dim(f'({duration_str})')}{self.color.dim(restored_str)}")
            else:
                self.output.print(f"{self.color.error('failed:')} {action_colored} {self.color.dim(f'({duration_str})')}{self.color.dim(restored_str)}")

    def _print_action_failure(self, result: ActionResult) -> None:
        """Print diagnostic information for a failed action.

        Args:
            result: The failed action result
        """
        error_msg = f"Action '{result.action_name}' failed!"
        self.output.print(f"\n{self.output.emoji('❌', '✗')} {self.color.error(error_msg)}")
        self.output.print(f"{self.output.emoji('📂', '▸')} {self.color.dim('Run directory:')} {self.color.highlight(str(self.run_directory))}")
        self.output.print(f"\n{self.output.emoji('📄', '▸')} {self.color.dim('Stdout:')} {self.color.info(str(result.stdout_path))}")
        if result.stdout_path.exists():
            self.output.print(result.stdout_path.read_text())
        self.output.print(f"\n{self.output.emoji('📄', '▸')} {self.color.dim('Stderr:')} {self.color.info(str(result.stderr_path))}")
        if result.stderr_path.exists():
            self.output.print(result.stderr_path.read_text())
        if result.error_message:
            self.output.print(f"\n{self.output.emoji('❌', '✗')} {self.color.error('Error:')} {result.error_message}")

    def execute_all(self) -> ExecutionResult:
        """Execute all actions in the graph.

        Returns:
            Execution result
        """
        if self.parallel_execution:
            return self._execute_in_parallel()

        # Track total execution time
        graph_start_time = time.time()

        # Get execution order
        try:
            execution_order = self.graph.get_execution_order()
        except ValueError as e:
            return ExecutionResult(
                success=False,
                action_results={},
                run_directory=self.run_directory,
            )

        # Setup rich table if enabled
        table_manager = None
        if self.use_rich_table:
            from .task_table import TaskTableManager
            # Convert ActionKey list to action names for table display
            action_names = [str(key) for key in execution_order]
            table_manager = TaskTableManager(action_names, no_color=self.no_color)
            table_manager.start()

        # Store table manager for access by _execute_action
        self._current_table_manager = table_manager

        try:
            # Execute actions in order
            action_outputs: dict[str, dict[str, Any]] = {}
            action_results: dict[str, ActionResult] = {}
            restored_actions: list[str] = []

            for action_key in execution_order:
                action_key_str = str(action_key)
                node = self.graph.get_node(action_key)

                # Update table or print start message
                if table_manager:
                    table_manager.mark_running(action_key_str)
                else:
                    self._print_action_start(action_key_str)

                # Execute action with its ActionKey
                result = self._execute_action(action_key, action_outputs)
                action_results[action_key_str] = result

                # Track restored actions
                if result.restored:
                    restored_actions.append(action_key_str)

                # Update table or print completion message
                if table_manager:
                    # Update sizes first
                    table_manager.update_output_sizes(action_key_str, result.stdout_size, result.stderr_size)
                    # Then update status
                    if result.restored:
                        table_manager.mark_restored(action_key_str, result.duration_seconds)
                    elif result.success:
                        table_manager.mark_done(action_key_str, result.duration_seconds)
                    else:
                        table_manager.mark_failed(action_key_str, result.duration_seconds)
                else:
                    self._print_action_completion(result)

                if not result.success:
                    # Action failed - stop execution
                    if table_manager:
                        table_manager.stop()
                    self._print_action_failure(result)

                    return ExecutionResult(
                        success=False,
                        action_results=action_results,
                        run_directory=self.run_directory,
                    )

                # Store outputs for dependent actions (keyed by ActionKey string)
                action_outputs[action_key_str] = result.outputs

        finally:
            if table_manager:
                table_manager.stop()

        return self._finalize_successful_execution(action_results, restored_actions, graph_start_time)

    def _execute_in_parallel(self) -> ExecutionResult:
        """Execute actions using a dependency-aware thread pool."""
        # Track total execution time
        graph_start_time = time.time()

        # Use ActionKey throughout for type safety
        pending_deps: dict[ActionKey, set[ActionKey]] = {
            key: {dep.action for dep in node.dependencies} for key, node in self.graph.nodes.items()
        }
        dependents: dict[ActionKey, set[ActionKey]] = {
            key: {dep.action for dep in node.dependents} for key, node in self.graph.nodes.items()
        }
        ready = [key for key, deps in pending_deps.items() if len(deps) == 0]
        scheduled: set[ActionKey] = set()
        completed: set[ActionKey] = set()
        action_outputs: dict[str, dict[str, Any]] = {}
        action_results: dict[str, ActionResult] = {}
        lock = threading.Lock()
        running: dict[concurrent.futures.Future[ActionResult], ActionKey] = {}
        max_workers = max(1, min(32, os.cpu_count() or 1))

        # Setup rich table if enabled
        # Get execution order for table display
        table_manager = None
        if self.use_rich_table:
            try:
                execution_order = self.graph.get_execution_order()
                from .task_table import TaskTableManager
                # Convert ActionKey list to action names for table display
                action_names = [str(key) for key in execution_order]
                table_manager = TaskTableManager(action_names, no_color=self.no_color)
                table_manager.start()
            except ValueError:
                # If we can't get execution order, skip table
                pass

        # Store table manager for access by _execute_action
        self._current_table_manager = table_manager

        # Track restored actions
        restored_actions: list[str] = []

        def submit_action(executor: concurrent.futures.ThreadPoolExecutor, action_key: ActionKey):
            action_key_str = str(action_key)
            scheduled.add(action_key)
            # Update table or print start message
            if table_manager:
                table_manager.mark_running(action_key_str)
            else:
                self._print_action_start(action_key_str)
            snapshot_outputs = dict(action_outputs)
            # Pass ActionKey directly to _execute_action
            future = executor.submit(self._execute_action, action_key, snapshot_outputs)
            running[future] = action_key

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                for key in ready:
                    submit_action(executor, key)

                while running:
                    done, _ = concurrent.futures.wait(
                        running.keys(),
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )
                    for future in done:
                        action_key = running.pop(future)
                        action_key_str = str(action_key)
                        try:
                            result = future.result()
                        except Exception as exc:  # pragma: no cover - defensive
                            result = ActionResult(
                                action_name=action_key_str,
                                success=False,
                                outputs={},
                                stdout_path=Path("/dev/null"),
                                stderr_path=Path("/dev/null"),
                                script_path=Path("/dev/null"),
                                start_time="",
                                end_time="",
                                duration_seconds=0.0,
                                exit_code=-1,
                                error_message=f"Execution error: {exc}",
                            )

                        action_results[action_key_str] = result

                        # Track restored actions
                        if result.restored:
                            with lock:
                                restored_actions.append(action_key_str)

                        # Update table or print completion message
                        if table_manager:
                            # Update sizes first
                            table_manager.update_output_sizes(action_key_str, result.stdout_size, result.stderr_size)
                            # Then update status
                            if result.restored:
                                table_manager.mark_restored(action_key_str, result.duration_seconds)
                            elif result.success:
                                table_manager.mark_done(action_key_str, result.duration_seconds)
                            else:
                                table_manager.mark_failed(action_key_str, result.duration_seconds)
                        else:
                            self._print_action_completion(result)

                        if not result.success:
                            # Action failed - stop execution
                            if table_manager:
                                table_manager.stop()
                            self._print_action_failure(result)
                            executor.shutdown(cancel_futures=True)
                            return ExecutionResult(
                                success=False,
                                action_results=action_results,
                                run_directory=self.run_directory,
                            )

                        # Store outputs and mark as completed
                        with lock:
                            action_outputs[action_key_str] = result.outputs
                            completed.add(action_key)

                        # Schedule dependent actions
                        for dependent_key in dependents.get(action_key, set()):
                            if dependent_key in completed or dependent_key in scheduled:
                                continue
                            pending_deps[dependent_key].discard(action_key)
                            if len(pending_deps[dependent_key]) == 0:
                                submit_action(executor, dependent_key)

        except KeyboardInterrupt:
            for future in running:
                future.cancel()
            if table_manager:
                table_manager.stop()
            return ExecutionResult(
                success=False,
                action_results=action_results,
                run_directory=self.run_directory,
            )
        finally:
            if table_manager:
                table_manager.stop()

        return self._finalize_successful_execution(action_results, restored_actions, graph_start_time)

    def _execute_action(
        self, action_key: ActionKey, action_outputs: dict[str, dict[str, Any]]
    ) -> ActionResult:
        """Execute a single action identified by its ActionKey."""
        action_key_str = str(action_key)

        # Determine directory name using same logic as preparation
        contexts_in_graph = {node.key.context_id for node in self.graph.nodes.values()}
        use_context_in_dirname = len(contexts_in_graph) > 1
        if use_context_in_dirname:
            action_dirname = action_key_str.replace(":", "_")
        else:
            action_dirname = action_key.id.name

        if self._can_restore_from_previous(action_dirname):
            return self._restore_from_previous(action_dirname, action_key_str)

        try:
            prepared = self._prepare_action_execution(action_key, action_outputs)
        except ValueError as err:
            now_iso = datetime.now().isoformat()
            return ActionResult(
                action_name=action_key_str,
                success=False,
                outputs={},
                stdout_path=Path("/dev/null"),
                stderr_path=Path("/dev/null"),
                script_path=Path("/dev/null"),
                start_time=now_iso,
                end_time=now_iso,
                duration_seconds=0.0,
                exit_code=-1,
                error_message=str(err),
            )

        return self._run_prepared_action(prepared)

    def _prepare_action_execution(
        self, action_key: ActionKey, action_outputs: dict[str, dict[str, Any]]
    ) -> PreparedAction:
        """Prepare an action for execution.

        Args:
            action_key: The ActionKey identifying the action (includes context)
            action_outputs: Outputs from previously executed actions

        Returns:
            PreparedAction with all execution artifacts
        """
        node = self.graph.get_node(action_key)
        action = node.action
        version = node.selected_version

        if version is None:
            raise ValueError("No valid version selected")

        # Determine if we need context in directory name (for multi-context executions)
        # If there are multiple distinct contexts in the graph, include context to avoid collisions
        contexts_in_graph = {node.key.context_id for node in self.graph.nodes.values()}
        use_context_in_dirname = len(contexts_in_graph) > 1

        if use_context_in_dirname:
            # Include context in directory name for multi-context executions
            # E.g., "platform:jvm+scala:2.12#build" becomes "platform_jvm+scala_2.12#build"
            action_key_str = str(action_key)
            safe_dir_name = action_key_str.replace(":", "_")
        else:
            # Single context - use simple action name for backward compatibility
            safe_dir_name = action_key.id.name

        action_dir = self.run_directory / safe_dir_name
        action_dir.mkdir(parents=True, exist_ok=True)

        runtime = RuntimeRegistry.get(version.language)

        # Use per-action args/flags if available (multi-context mode)
        # Otherwise fall back to engine's global args/flags
        action_args = node.args if node.args is not None else self.args
        action_flags = node.flags if node.flags is not None else self.flags

        context = self._build_execution_context(
            action_dir, action_outputs, action_args, action_flags, action_key
        )

        output_json_path = action_dir / "output.json"
        rendered = runtime.prepare_script(
            version=version,
            context=context,
            output_json_path=output_json_path,
            working_dir=action_dir,
        )

        script_ext = ".sh" if version.language == "bash" else ".py"
        script_path = action_dir / f"script{script_ext}"
        script_path.write_text(rendered.content)
        script_path.chmod(0o755)

        stdout_path = action_dir / "stdout.log"
        stderr_path = action_dir / "stderr.log"

        base_exec_cmd = runtime.get_execution_command(script_path)
        exec_cmd = self._build_exec_command(action, base_exec_cmd)

        return PreparedAction(
            action=action,
            version=version,
            runtime=runtime,
            context=context,
            action_dir=action_dir,
            script_path=script_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            output_json_path=output_json_path,
            exec_cmd=exec_cmd,
        )

    def _build_execution_context(
        self,
        action_dir: Path,
        action_outputs: dict[str, dict[str, Any]],
        args: dict[str, str],
        flags: dict[str, bool],
        action_key: ActionKey,
    ) -> ExecutionContext:
        """Build execution context with provided args and flags.

        Args:
            action_dir: Action directory path
            action_outputs: Outputs from previous actions (keyed by ActionKey string)
            args: Arguments for this action (may be per-action or global)
            flags: Flags for this action (may be per-action or global)
            action_key: The ActionKey for this action (to filter outputs by context)

        Returns:
            ExecutionContext for the action
        """
        # Transform action_outputs to use simple action names for same-context actions
        # This allows ${action.dependency-name.var} to work in scripts
        context_filtered_outputs: dict[str, dict[str, Any]] = {}
        for output_key_str, outputs in action_outputs.items():
            # Parse the output key to extract action name and context
            # Format is either "action-name" or "context#action-name"
            if "#" in output_key_str:
                context_str, action_name = output_key_str.split("#", 1)
                # Only include outputs from the same context
                if context_str == str(action_key.context_id):
                    context_filtered_outputs[action_name] = outputs
            else:
                # Old format without context (backward compatibility)
                context_filtered_outputs[output_key_str] = outputs

        return ExecutionContext(
            system_vars={
                "project-root": str(self.project_root),
                "run-dir": str(self.run_directory),
                "action-dir": str(action_dir),
            },
            env_vars=dict(os.environ) | self.environment_vars,
            args=args,
            flags=flags,
            action_outputs=context_filtered_outputs,
        )

    def _build_exec_command(
        self, action: ActionDefinition, base_exec_cmd: list[str]
    ) -> list[str]:
        if self.without_nix:
            return base_exec_cmd

        env_vars_to_keep = set(self.passthrough_env_vars)
        env_vars_to_keep.update(action.required_env_vars.keys())

        exec_cmd = ["nix", "develop", "--ignore-environment"]
        for var in sorted(env_vars_to_keep):
            exec_cmd.extend(["--keep", var])
        exec_cmd.extend(["--command"] + base_exec_cmd)
        return exec_cmd

    def _run_prepared_action(self, prepared: PreparedAction) -> ActionResult:
        action_name = prepared.action.name

        if self.github_actions:
            print(f"::group::{action_name}")

        if self.github_actions or self.verbose:
            print(f"{self.color.dim('Command:')} {' '.join(prepared.exec_cmd)}")

        start_time = datetime.now()
        start_time_iso = start_time.isoformat()

        stdout_size = 0
        stderr_size = 0
        table_manager = getattr(self, "_current_table_manager", None)

        try:
            with open(prepared.stdout_path, "w") as stdout_file, open(
                prepared.stderr_path, "w"
            ) as stderr_file:
                process = subprocess.Popen(
                    prepared.exec_cmd,
                    cwd=str(self.project_root),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=os.environ.copy(),
                    text=True,
                    bufsize=1,
                )

                size_lock = threading.Lock()

                def stream_output(pipe, console_stream, file_stream, is_stdout: bool):
                    nonlocal stdout_size, stderr_size
                    if pipe:
                        for line in pipe:
                            if self.github_actions or self.verbose:
                                console_stream.write(line)
                                console_stream.flush()

                            file_stream.write(line)
                            file_stream.flush()

                            line_bytes = len(line.encode("utf-8"))
                            with size_lock:
                                if is_stdout:
                                    stdout_size += line_bytes
                                else:
                                    stderr_size += line_bytes

                                if table_manager and self.use_rich_table:
                                    table_manager.update_output_sizes(
                                        action_name, stdout_size, stderr_size
                                    )

                stdout_thread = threading.Thread(
                    target=stream_output,
                    args=(process.stdout, sys.stdout, stdout_file, True),
                )
                stderr_thread = threading.Thread(
                    target=stream_output,
                    args=(process.stderr, sys.stderr, stderr_file, False),
                )

                stdout_thread.start()
                stderr_thread.start()

                returncode = process.wait()
                stdout_thread.join()
                stderr_thread.join()

            if self.github_actions:
                print("::endgroup::")

            if prepared.stdout_path.exists():
                stdout_size = prepared.stdout_path.stat().st_size
            if prepared.stderr_path.exists():
                stderr_size = prepared.stderr_path.stat().st_size

            if table_manager and self.use_rich_table:
                table_manager.update_output_sizes(action_name, stdout_size, stderr_size)

            end_time = datetime.now()
            end_time_iso = end_time.isoformat()
            duration = (end_time - start_time).total_seconds()

            if returncode != 0:
                self._write_action_meta(
                    prepared.action_dir,
                    action_name,
                    success=False,
                    start_time=start_time_iso,
                    end_time=end_time_iso,
                    duration=duration,
                    exit_code=returncode,
                    error_message=f"Script exited with code {returncode}",
                    stdout_size=stdout_size,
                    stderr_size=stderr_size,
                )
                return ActionResult(
                    action_name=action_name,
                    success=False,
                    outputs={},
                    stdout_path=prepared.stdout_path,
                    stderr_path=prepared.stderr_path,
                    script_path=prepared.script_path,
                    start_time=start_time_iso,
                    end_time=end_time_iso,
                    duration_seconds=duration,
                    exit_code=returncode,
                    error_message=f"Script exited with code {returncode}",
                    stdout_size=stdout_size,
                    stderr_size=stderr_size,
                )

            if not prepared.output_json_path.exists():
                self._write_action_meta(
                    prepared.action_dir,
                    action_name,
                    success=False,
                    start_time=start_time_iso,
                    end_time=end_time_iso,
                    duration=duration,
                    exit_code=returncode,
                    error_message="No output.json generated",
                    stdout_size=stdout_size,
                    stderr_size=stderr_size,
                )
                return ActionResult(
                    action_name=action_name,
                    success=False,
                    outputs={},
                    stdout_path=prepared.stdout_path,
                    stderr_path=prepared.stderr_path,
                    script_path=prepared.script_path,
                    start_time=start_time_iso,
                    end_time=end_time_iso,
                    duration_seconds=duration,
                    exit_code=returncode,
                    error_message="No output.json generated",
                    stdout_size=stdout_size,
                    stderr_size=stderr_size,
                )

            outputs = self._parse_outputs(
                prepared.output_json_path, prepared.version.return_declarations
            )

            for ret_decl in prepared.version.return_declarations:
                if ret_decl.return_type not in (ReturnType.FILE, ReturnType.DIRECTORY):
                    continue

                output_value = outputs.get(ret_decl.name)
                if output_value is None:
                    return self._report_missing_output(
                        prepared,
                        action_name,
                        start_time_iso,
                        end_time_iso,
                        duration,
                        stdout_size,
                        stderr_size,
                        outputs,
                        f"Output '{ret_decl.name}' not found",
                    )

                path = Path(output_value)
                if not path.is_absolute():
                    path = self.project_root / path

                if not path.exists():
                    return self._report_missing_output(
                        prepared,
                        action_name,
                        start_time_iso,
                        end_time_iso,
                        duration,
                        stdout_size,
                        stderr_size,
                        outputs,
                        f"{ret_decl.return_type.value.capitalize()} '{ret_decl.name}' does not exist: {output_value}",
                    )

                if ret_decl.return_type == ReturnType.FILE and not path.is_file():
                    return self._report_missing_output(
                        prepared,
                        action_name,
                        start_time_iso,
                        end_time_iso,
                        duration,
                        stdout_size,
                        stderr_size,
                        outputs,
                        f"Output '{ret_decl.name}' is not a file: {output_value}",
                    )

            self._write_action_meta(
                prepared.action_dir,
                action_name,
                success=True,
                start_time=start_time_iso,
                end_time=end_time_iso,
                duration=duration,
                exit_code=0,
                stdout_size=stdout_size,
                stderr_size=stderr_size,
            )

            return ActionResult(
                action_name=action_name,
                success=True,
                outputs=outputs,
                stdout_path=prepared.stdout_path,
                stderr_path=prepared.stderr_path,
                script_path=prepared.script_path,
                start_time=start_time_iso,
                end_time=end_time_iso,
                duration_seconds=duration,
                exit_code=0,
                stdout_size=stdout_size,
                stderr_size=stderr_size,
            )

        except Exception as e:
            end_time = datetime.now()
            end_time_iso = end_time.isoformat()
            duration = (end_time - start_time).total_seconds()

            exc_stdout_size = (
                prepared.stdout_path.stat().st_size if prepared.stdout_path.exists() else 0
            )
            exc_stderr_size = (
                prepared.stderr_path.stat().st_size if prepared.stderr_path.exists() else 0
            )

            error_msg = f"Execution error: {e}"
            self._write_action_meta(
                prepared.action_dir,
                action_name,
                success=False,
                start_time=start_time_iso,
                end_time=end_time_iso,
                duration=duration,
                exit_code=-1,
                error_message=error_msg,
                stdout_size=exc_stdout_size,
                stderr_size=exc_stderr_size,
            )

            return ActionResult(
                action_name=action_name,
                success=False,
                outputs={},
                stdout_path=prepared.stdout_path,
                stderr_path=prepared.stderr_path,
                script_path=prepared.script_path,
                start_time=start_time_iso,
                end_time=end_time_iso,
                duration_seconds=duration,
                exit_code=-1,
                error_message=error_msg,
                stdout_size=exc_stdout_size,
                stderr_size=exc_stderr_size,
            )

    def _report_missing_output(
        self,
        prepared: PreparedAction,
        action_name: str,
        start_time_iso: str,
        end_time_iso: str,
        duration: float,
        stdout_size: int,
        stderr_size: int,
        outputs: dict[str, Any],
        error_msg: str,
    ) -> ActionResult:
        self._write_action_meta(
            prepared.action_dir,
            action_name,
            success=False,
            start_time=start_time_iso,
            end_time=end_time_iso,
            duration=duration,
            exit_code=0,
            error_message=error_msg,
            stdout_size=stdout_size,
            stderr_size=stderr_size,
        )
        return ActionResult(
            action_name=action_name,
            success=False,
            outputs=outputs,
            stdout_path=prepared.stdout_path,
            stderr_path=prepared.stderr_path,
            script_path=prepared.script_path,
            start_time=start_time_iso,
            end_time=end_time_iso,
            duration_seconds=duration,
            exit_code=0,
            error_message=error_msg,
            stdout_size=stdout_size,
            stderr_size=stderr_size,
        )
    def _write_action_meta(
        self,
        action_dir: Path,
        action_name: str,
        success: bool,
        start_time: str,
        end_time: str,
        duration: float,
        exit_code: int,
        error_message: Optional[str] = None,
        stdout_size: int = 0,
        stderr_size: int = 0,
    ) -> None:
        """Write meta.json for an action."""
        meta = {
            "action_name": action_name,
            "success": success,
            "start_time": start_time,
            "end_time": end_time,
            "duration_seconds": duration,
            "exit_code": exit_code,
            "stdout_size": stdout_size,
            "stderr_size": stderr_size,
        }
        if error_message:
            meta["error_message"] = error_message

        (action_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    def _finalize_successful_execution(
        self,
        action_results: dict[str, ActionResult],
        restored_actions: list[str],
        graph_start_time: float,
    ) -> ExecutionResult:
        graph_duration = time.time() - graph_start_time

        if restored_actions and not self.github_actions:
            restored_list = ", ".join(restored_actions)
            self.output.print(f"\n{self.output.emoji('♻️', '▸')} {self.color.dim('restored from previous run:')} {self.color.highlight(restored_list)}")

        if not self.keep_run_dir:
            try:
                shutil.rmtree(self.run_directory)
            except Exception as e:
                emoji = "!" if self.no_color else "⚠️"
                print(f"{emoji} {self.color.warning('Warning:')} Failed to clean up run directory: {e}")

        if not self.github_actions:
            self.output.print(f"\n{self.color.dim('Total wall time:')} {self.color.highlight(f'{graph_duration:.1f}s')}")

        return ExecutionResult(
            success=True,
            action_results=action_results,
            run_directory=self.run_directory,
        )

    def _can_restore_from_previous(self, action_name: str) -> bool:
        """Check if an action can be restored from previous run.

        Args:
            action_name: Action name

        Returns:
            True if action was successful in previous run
        """
        if self.previous_run_directory is None:
            return False

        prev_action_dir = self.previous_run_directory / action_name
        prev_meta_path = prev_action_dir / "meta.json"

        if not prev_meta_path.exists():
            return False

        try:
            meta = json.loads(prev_meta_path.read_text())
            return meta.get("success", False)
        except Exception:
            return False

    def _restore_from_previous(self, action_dirname: str, action_key_str: str) -> ActionResult:
        """Restore action from previous run.

        Args:
            action_dirname: Directory name for the action (may include context)
            action_key_str: Full ActionKey string for result

        Returns:
            Action result from previous run
        """
        prev_action_dir = self.previous_run_directory / action_dirname
        prev_meta_path = prev_action_dir / "meta.json"
        prev_output_path = prev_action_dir / "output.json"

        # Load metadata
        meta = json.loads(prev_meta_path.read_text())

        # Copy entire action directory to current run
        current_action_dir = self.run_directory / action_dirname
        shutil.copytree(prev_action_dir, current_action_dir)

        # Parse outputs - need to find node by matching action name
        # Search through graph nodes to find one with matching action name
        version = None
        for node in self.graph.nodes.values():
            # Extract action name from key string (format: "name" or "context#name")
            action_name = action_key_str.split("#")[-1] if "#" in action_key_str else action_key_str
            if node.action.name == action_name or node.key.id.name == action_name:
                version = node.selected_version
                break

        outputs = {}
        if prev_output_path.exists() and version is not None:
            outputs = self._parse_outputs(prev_output_path, version.return_declarations)

        return ActionResult(
            action_name=action_key_str,  # Use full key string for result
            success=True,
            outputs=outputs,
            stdout_path=current_action_dir / "stdout.log",
            stderr_path=current_action_dir / "stderr.log",
            script_path=current_action_dir / "script.sh",
            start_time=meta.get("start_time", ""),
            end_time=meta.get("end_time", ""),
            duration_seconds=meta.get("duration_seconds", 0.0),
            exit_code=meta.get("exit_code", 0),
            restored=True,
            stdout_size=meta.get("stdout_size", 0),
            stderr_size=meta.get("stderr_size", 0),
        )

    def _parse_outputs(
        self, output_json_path: Path, return_declarations
    ) -> dict[str, Any]:
        """Parse outputs from output.json.

        Args:
            output_json_path: Path to output.json
            return_declarations: Expected return declarations

        Returns:
            Dictionary of outputs
        """
        try:
            data = json.loads(output_json_path.read_text())

            # Extract just the values
            outputs = {}
            for name, info in data.items():
                outputs[name] = info["value"]

            return outputs
        except Exception as e:
            raise ValueError(f"Failed to parse output.json: {e}")
