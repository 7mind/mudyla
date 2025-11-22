"""Execution engine for running actions."""

import json
import os
import concurrent.futures
import shutil
import subprocess
import time
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..ast.types import ReturnType
from ..dag.graph import ActionGraph
from ..utils.colors import ColorFormatter
from ..utils.output import OutputFormatter
from .runtime_registry import RuntimeRegistry
from .bash_runtime import BashRuntime
from .python_runtime import PythonRuntime


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
        return {
            goal: self.action_results[goal].outputs
            for goal in goals
            if goal in self.action_results
        }


class ExecutionEngine:
    """Engine for executing actions in a DAG."""

    def __init__(
        self,
        graph: ActionGraph,
        project_root: Path,
        args: dict[str, str],
        flags: dict[str, bool],
        axis_values: dict[str, str],
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
        self.args = args
        self.flags = flags
        self.axis_values = axis_values
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
            RuntimeRegistry.register(runtime_cls)

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
            self.output.print(f"{self.color.dim('start:')} {self.color.highlight(action_name)}")

    def _print_action_completion(self, result: ActionResult) -> None:
        """Print action completion message with duration.

        Args:
            result: The completed action result
        """
        if not self.github_actions:
            duration_str = f"{result.duration_seconds:.1f}s"
            restored_str = " (restored from previous run)" if result.restored else ""
            if result.success:
                emoji = self.output.emoji('♻️', '✓') if result.restored else ""
                self.output.print(f"{emoji} {self.color.dim('done:')} {self.color.highlight(result.action_name)} {self.color.dim(f'({duration_str})')}{self.color.dim(restored_str)}")
            else:
                self.output.print(f"{self.color.error('failed:')} {self.color.highlight(result.action_name)} {self.color.dim(f'({duration_str})')}{self.color.dim(restored_str)}")

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
            table_manager = TaskTableManager(execution_order, no_color=self.no_color)
            table_manager.start()

        # Store table manager for access by _execute_action
        self._current_table_manager = table_manager

        try:
            # Execute actions in order
            action_outputs: dict[str, dict[str, Any]] = {}
            action_results: dict[str, ActionResult] = {}
            restored_actions: list[str] = []

            for action_name in execution_order:
                node = self.graph.get_node(action_name)

                # Update table or print start message
                if table_manager:
                    table_manager.mark_running(action_name)
                else:
                    self._print_action_start(action_name)

                # Execute action
                result = self._execute_action(node.action.name, action_outputs)
                action_results[action_name] = result

                # Track restored actions
                if result.restored:
                    restored_actions.append(action_name)

                # Update table or print completion message
                if table_manager:
                    # Update sizes first
                    table_manager.update_output_sizes(action_name, result.stdout_size, result.stderr_size)
                    # Then update status
                    if result.restored:
                        table_manager.mark_restored(action_name, result.duration_seconds)
                    elif result.success:
                        table_manager.mark_done(action_name, result.duration_seconds)
                    else:
                        table_manager.mark_failed(action_name, result.duration_seconds)
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

                # Store outputs for dependent actions
                action_outputs[action_name] = result.outputs

        finally:
            if table_manager:
                table_manager.stop()

        # Calculate total wall time
        graph_duration = time.time() - graph_start_time

        # Print summary of restored actions
        if restored_actions and not self.github_actions:
            restored_list = ", ".join(restored_actions)
            self.output.print(f"\n{self.output.emoji('♻️', '▸')} {self.color.dim('restored from previous run:')} {self.color.highlight(restored_list)}")

        # Success - clean up run directory if not keeping it
        if not self.keep_run_dir:
            try:
                shutil.rmtree(self.run_directory)
            except Exception as e:
                # Don't fail on cleanup errors, just warn
                emoji = "!" if self.no_color else "⚠️"
                print(f"{emoji} {self.color.warning('Warning:')} Failed to clean up run directory: {e}")

        # Print total wall time
        if not self.github_actions:
            self.output.print(f"\n{self.color.dim('Total wall time:')} {self.color.highlight(f'{graph_duration:.1f}s')}")

        return ExecutionResult(
            success=True,
            action_results=action_results,
            run_directory=self.run_directory,
        )

    def _execute_in_parallel(self) -> ExecutionResult:
        """Execute actions using a dependency-aware thread pool."""
        # Track total execution time
        graph_start_time = time.time()

        pending_deps: dict[str, set[str]] = {
            name: set(node.dependencies) for name, node in self.graph.nodes.items()
        }
        dependents: dict[str, set[str]] = {
            name: set(node.dependents) for name, node in self.graph.nodes.items()
        }
        ready = [name for name, deps in pending_deps.items() if len(deps) == 0]
        scheduled: set[str] = set()
        completed: set[str] = set()
        action_outputs: dict[str, dict[str, Any]] = {}
        action_results: dict[str, ActionResult] = {}
        lock = threading.Lock()
        running: dict[concurrent.futures.Future[ActionResult], str] = {}
        max_workers = max(1, min(32, os.cpu_count() or 1))

        # Setup rich table if enabled
        # Get execution order for table display
        table_manager = None
        if self.use_rich_table:
            try:
                execution_order = self.graph.get_execution_order()
                from .task_table import TaskTableManager
                table_manager = TaskTableManager(execution_order, no_color=self.no_color)
                table_manager.start()
            except ValueError:
                # If we can't get execution order, skip table
                pass

        # Store table manager for access by _execute_action
        self._current_table_manager = table_manager

        # Track restored actions
        restored_actions: list[str] = []

        def submit_action(executor: concurrent.futures.ThreadPoolExecutor, action_name: str):
            scheduled.add(action_name)
            # Update table or print start message
            if table_manager:
                table_manager.mark_running(action_name)
            else:
                self._print_action_start(action_name)
            snapshot_outputs = dict(action_outputs)
            future = executor.submit(self._execute_action, action_name, snapshot_outputs)
            running[future] = action_name

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                for name in ready:
                    submit_action(executor, name)

                while running:
                    done, _ = concurrent.futures.wait(
                        running.keys(),
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )
                    for future in done:
                        action_name = running.pop(future)
                        try:
                            result = future.result()
                        except Exception as exc:  # pragma: no cover - defensive
                            result = ActionResult(
                                action_name=action_name,
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

                        action_results[action_name] = result

                        # Track restored actions
                        if result.restored:
                            with lock:
                                restored_actions.append(action_name)

                        # Update table or print completion message
                        if table_manager:
                            # Update sizes first
                            table_manager.update_output_sizes(action_name, result.stdout_size, result.stderr_size)
                            # Then update status
                            if result.restored:
                                table_manager.mark_restored(action_name, result.duration_seconds)
                            elif result.success:
                                table_manager.mark_done(action_name, result.duration_seconds)
                            else:
                                table_manager.mark_failed(action_name, result.duration_seconds)
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

                        with lock:
                            action_outputs[action_name] = result.outputs
                            completed.add(action_name)

                        for dependent in dependents.get(action_name, set()):
                            if dependent in completed or dependent in scheduled:
                                continue
                            pending_deps[dependent].discard(action_name)
                            if len(pending_deps[dependent]) == 0:
                                submit_action(executor, dependent)

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

        # Calculate total wall time
        graph_duration = time.time() - graph_start_time

        # Print summary of restored actions
        if restored_actions and not self.github_actions:
            restored_list = ", ".join(restored_actions)
            self.output.print(f"\n{self.output.emoji('♻️', '▸')} {self.color.dim('restored from previous run:')} {self.color.highlight(restored_list)}")

        # Success - clean up run directory if not keeping it
        if not self.keep_run_dir:
            try:
                shutil.rmtree(self.run_directory)
            except Exception as e:
                emoji = "!" if self.no_color else "⚠️"
                print(f"{emoji} {self.color.warning('Warning:')} Failed to clean up run directory: {e}")

        # Print total wall time
        if not self.github_actions:
            self.output.print(f"\n{self.color.dim('Total wall time:')} {self.color.highlight(f'{graph_duration:.1f}s')}")

        return ExecutionResult(
            success=True,
            action_results=action_results,
            run_directory=self.run_directory,
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

    def _restore_from_previous(self, action_name: str) -> ActionResult:
        """Restore action from previous run.

        Args:
            action_name: Action name

        Returns:
            Action result from previous run
        """
        prev_action_dir = self.previous_run_directory / action_name
        prev_meta_path = prev_action_dir / "meta.json"
        prev_output_path = prev_action_dir / "output.json"

        # Load metadata
        meta = json.loads(prev_meta_path.read_text())

        # Copy entire action directory to current run
        current_action_dir = self.run_directory / action_name
        shutil.copytree(prev_action_dir, current_action_dir)

        # Parse outputs
        node = self.graph.get_node(action_name)
        version = node.selected_version
        outputs = {}
        if prev_output_path.exists() and version is not None:
            outputs = self._parse_outputs(prev_output_path, version.return_declarations)

        return ActionResult(
            action_name=action_name,
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

    def _execute_action(
        self, action_name: str, action_outputs: dict[str, dict[str, Any]]
    ) -> ActionResult:
        """Execute a single action.

        Args:
            action_name: Action name
            action_outputs: Outputs from previously executed actions

        Returns:
            Action result
        """
        # Check if we can restore from previous run
        if self._can_restore_from_previous(action_name):
            return self._restore_from_previous(action_name)

        node = self.graph.get_node(action_name)
        action = node.action
        version = node.selected_version

        if version is None:
            now_iso = datetime.now().isoformat()
            return ActionResult(
                action_name=action_name,
                success=False,
                outputs={},
                stdout_path=Path("/dev/null"),
                stderr_path=Path("/dev/null"),
                script_path=Path("/dev/null"),
                start_time=now_iso,
                end_time=now_iso,
                duration_seconds=0.0,
                exit_code=-1,
                error_message="No valid version selected",
            )

        # Create action directory
        action_dir = self.run_directory / action_name
        action_dir.mkdir(parents=True, exist_ok=True)

        # Get appropriate language runtime
        runtime = RuntimeRegistry.get(version.language)

        # Prepare execution context
        from mudyla.executor.language_runtime import ExecutionContext

        context = ExecutionContext(
            system_vars={
                "project-root": str(self.project_root),
                "run-dir": str(self.run_directory),
                "action-dir": str(action_dir),
            },
            env_vars=dict(os.environ) | self.environment_vars,
            args=self.args,
            flags=self.flags,
            action_outputs=action_outputs,
        )

        # Prepare script using language runtime
        output_json_path = action_dir / "output.json"
        rendered = runtime.prepare_script(
            version=version,
            context=context,
            output_json_path=output_json_path,
            working_dir=action_dir,
        )

        # Determine script extension based on language
        script_ext = ".sh" if version.language == "bash" else ".py"
        script_path = action_dir / f"script{script_ext}"
        script_path.write_text(rendered.content)
        script_path.chmod(0o755)

        # Prepare output paths
        stdout_path = action_dir / "stdout.log"
        stderr_path = action_dir / "stderr.log"

        # Get execution command from language runtime
        base_exec_cmd = runtime.get_execution_command(script_path)

        # Build execution command
        if self.without_nix:
            # Run directly without Nix
            exec_cmd = base_exec_cmd
        else:
            # Run under Nix develop environment with clean environment
            # Collect all environment variables that should be kept
            env_vars_to_keep = set()

            # Add global passthrough env vars
            env_vars_to_keep.update(self.passthrough_env_vars)

            # Add action-specific required env vars
            env_vars_to_keep.update(action.required_env_vars.keys())

            # Build command with --ignore-environment and --keep for each var
            exec_cmd = ["nix", "develop", "--ignore-environment"]
            for var in sorted(env_vars_to_keep):
                exec_cmd.extend(["--keep", var])
            exec_cmd.extend(["--command"] + base_exec_cmd)

        # Execute
        if self.github_actions:
            print(f"::group::{action_name}")

        # Print command in verbose/CI modes
        if self.github_actions or self.verbose:
            print(f"{self.color.dim('Command:')} {' '.join(exec_cmd)}")

        # Record start time
        start_time = datetime.now()
        start_time_iso = start_time.isoformat()

        try:
            # Track output sizes
            stdout_size = 0
            stderr_size = 0

            # Get table manager from execution context if available
            table_manager = None
            if hasattr(self, '_current_table_manager'):
                table_manager = self._current_table_manager

            if self.github_actions or self.verbose:
                # Stream output to console AND write to files
                import sys
                import threading

                with open(stdout_path, "w") as stdout_file, open(
                    stderr_path, "w"
                ) as stderr_file:
                    process = subprocess.Popen(
                        exec_cmd,
                        cwd=str(self.project_root),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=os.environ.copy(),
                        text=True,
                        bufsize=1,
                    )

                    # Use nonlocal to track sizes across threads
                    size_lock = threading.Lock()

                    def stream_output(pipe, console_stream, file_stream, is_stdout: bool):
                        """Stream output from pipe to both console and file."""
                        nonlocal stdout_size, stderr_size
                        if pipe:
                            for line in pipe:
                                console_stream.write(line)
                                console_stream.flush()
                                file_stream.write(line)

                                # Track size and update table
                                line_bytes = len(line.encode('utf-8'))
                                with size_lock:
                                    if is_stdout:
                                        stdout_size += line_bytes
                                    else:
                                        stderr_size += line_bytes

                                    # Update table manager if available
                                    if table_manager and self.use_rich_table:
                                        table_manager.update_output_sizes(
                                            action_name, stdout_size, stderr_size
                                        )

                    # Start threads to read stdout and stderr simultaneously
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

                    # Wait for process to complete
                    returncode = process.wait()

                    # Wait for output threads to finish
                    stdout_thread.join()
                    stderr_thread.join()

                if self.github_actions:
                    print("::endgroup::")
            else:
                # Normal mode - only write to files
                with open(stdout_path, "w") as stdout_file, open(
                    stderr_path, "w"
                ) as stderr_file:
                    result = subprocess.run(
                        exec_cmd,
                        cwd=str(self.project_root),
                        stdout=stdout_file,
                        stderr=stderr_file,
                        env=os.environ.copy(),
                    )
                    returncode = result.returncode

            # Get final sizes from files
            if stdout_path.exists():
                stdout_size = stdout_path.stat().st_size
            if stderr_path.exists():
                stderr_size = stderr_path.stat().st_size

            # Update table manager with final sizes
            if table_manager and self.use_rich_table:
                table_manager.update_output_sizes(action_name, stdout_size, stderr_size)

            # Record end time
            end_time = datetime.now()
            end_time_iso = end_time.isoformat()
            duration = (end_time - start_time).total_seconds()

            if returncode != 0:
                # Write failure meta.json
                self._write_action_meta(
                    action_dir,
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
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    script_path=script_path,
                    start_time=start_time_iso,
                    end_time=end_time_iso,
                    duration_seconds=duration,
                    exit_code=returncode,
                    error_message=f"Script exited with code {returncode}",
                    stdout_size=stdout_size,
                    stderr_size=stderr_size,
                )

            # Parse outputs
            if not output_json_path.exists():
                # Write failure meta.json
                self._write_action_meta(
                    action_dir,
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
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    script_path=script_path,
                    start_time=start_time_iso,
                    end_time=end_time_iso,
                    duration_seconds=duration,
                    exit_code=returncode,
                    error_message="No output.json generated",
                    stdout_size=stdout_size,
                    stderr_size=stderr_size,
                )

            outputs = self._parse_outputs(output_json_path, version.return_declarations)

            # Validate file/directory outputs
            for ret_decl in version.return_declarations:
                if ret_decl.return_type in (ReturnType.FILE, ReturnType.DIRECTORY):
                    output_value = outputs.get(ret_decl.name)
                    if output_value is None:
                        error_msg = f"Output '{ret_decl.name}' not found"
                        self._write_action_meta(
                            action_dir,
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
                            stdout_path=stdout_path,
                            stderr_path=stderr_path,
                            script_path=script_path,
                            start_time=start_time_iso,
                            end_time=end_time_iso,
                            duration_seconds=duration,
                            exit_code=0,
                            error_message=error_msg,
                            stdout_size=stdout_size,
                            stderr_size=stderr_size,
                        )

                    path = Path(output_value)
                    # Resolve relative paths relative to project root
                    if not path.is_absolute():
                        path = self.project_root / path

                    if not path.exists():
                        error_msg = (
                            f"{ret_decl.return_type.value.capitalize()} "
                            f"'{ret_decl.name}' does not exist: {output_value}"
                        )
                        self._write_action_meta(
                            action_dir,
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
                            stdout_path=stdout_path,
                            stderr_path=stderr_path,
                            script_path=script_path,
                            start_time=start_time_iso,
                            end_time=end_time_iso,
                            duration_seconds=duration,
                            exit_code=0,
                            error_message=error_msg,
                            stdout_size=stdout_size,
                            stderr_size=stderr_size,
                        )

                    if ret_decl.return_type == ReturnType.FILE and not path.is_file():
                        error_msg = f"Output '{ret_decl.name}' is not a file: {output_value}"
                        self._write_action_meta(
                            action_dir,
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
                            stdout_path=stdout_path,
                            stderr_path=stderr_path,
                            script_path=script_path,
                            start_time=start_time_iso,
                            end_time=end_time_iso,
                            duration_seconds=duration,
                            exit_code=0,
                            error_message=error_msg,
                            stdout_size=stdout_size,
                            stderr_size=stderr_size,
                        )

                    if (
                        ret_decl.return_type == ReturnType.DIRECTORY
                        and not path.is_dir()
                    ):
                        error_msg = f"Output '{ret_decl.name}' is not a directory: {output_value}"
                        self._write_action_meta(
                            action_dir,
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
                            stdout_path=stdout_path,
                            stderr_path=stderr_path,
                            script_path=script_path,
                            start_time=start_time_iso,
                            end_time=end_time_iso,
                            duration_seconds=duration,
                            exit_code=0,
                            error_message=error_msg,
                            stdout_size=stdout_size,
                            stderr_size=stderr_size,
                        )

            # Write success meta.json
            self._write_action_meta(
                action_dir,
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
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                script_path=script_path,
                start_time=start_time_iso,
                end_time=end_time_iso,
                duration_seconds=duration,
                exit_code=0,
                stdout_size=stdout_size,
                stderr_size=stderr_size,
            )

        except Exception as e:
            # Record end time for exception case
            end_time = datetime.now()
            end_time_iso = end_time.isoformat()
            duration = (end_time - start_time).total_seconds()

            # Try to get sizes from files if they exist
            exc_stdout_size = 0
            exc_stderr_size = 0
            if stdout_path.exists():
                exc_stdout_size = stdout_path.stat().st_size
            if stderr_path.exists():
                exc_stderr_size = stderr_path.stat().st_size

            error_msg = f"Execution error: {e}"
            self._write_action_meta(
                action_dir,
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
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                script_path=script_path,
                start_time=start_time_iso,
                end_time=end_time_iso,
                duration_seconds=duration,
                exit_code=-1,
                error_message=error_msg,
                stdout_size=exc_stdout_size,
                stderr_size=exc_stderr_size,
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
