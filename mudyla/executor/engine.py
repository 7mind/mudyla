"""Execution engine for running actions."""

import json
import os
import concurrent.futures
import shutil
import signal
import subprocess
import time
import threading
import sys
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..ast.types import ReturnType
from ..ast.models import ActionDefinition, ActionVersion
from ..dag.graph import ActionGraph, ActionKey
from ..formatters import OutputFormatter
from ..formatters.action import truncate_dirname
from .runtime_registry import RuntimeRegistry
from .runtime_bash import BashRuntime
from .runtime_python import PythonRuntime
from .language_runtime import ExecutionContext, LanguageRuntime
from .action_logger import ActionLogger


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
class SubprocessResult:
    """Result of running a subprocess."""

    returncode: int
    stdout_size: int
    stderr_size: int


@dataclass
class ExecutionResult:
    """Result of executing all actions."""

    success: bool
    action_results: dict[ActionKey, ActionResult]
    run_directory: Path

    def get_goal_outputs(self, goal_keys: list[ActionKey]) -> dict[str, dict[str, Any]]:
        """Get outputs for goal actions.

        Args:
            goal_keys: List of ActionKeys representing goals

        Returns:
            Dictionary with nested structure by axes, then action name
            Format: {axis1-name: {axis1-value: {axis2-name: {axis2-value: {action-name: {...}}}}}}
        """
        return self._build_nested_outputs(goal_keys)

    def get_all_outputs(self, all_keys: list[ActionKey]) -> dict[str, dict[str, Any]]:
        """Get outputs for all actions, not just goals.

        Args:
            all_keys: List of all ActionKeys in the graph

        Returns:
            Dictionary with nested structure by axes, then action name
            Format: {axis1-name: {axis1-value: {axis2-name: {axis2-value: {action-name: {...}}}}}}
        """
        return self._build_nested_outputs(all_keys)

    def _build_nested_outputs(self, action_keys: list[ActionKey]) -> dict[str, dict[str, Any]]:
        """Build nested output structure for given action keys.

        Args:
            action_keys: List of ActionKeys to include

        Returns:
            Dictionary with nested structure by axes, args, flags, then action name
        """
        result: dict[str, Any] = {}
        for action_key in action_keys:
            if action_key not in self.action_results:
                warnings.warn(f"No results found for action {action_key}", stacklevel=2)
                continue

            action_result = self.action_results[action_key]
            action_name = action_key.id.name
            context_id = action_key.context_id

            # Collect all differentiation components in order: axes, args, flags
            path_components: list[tuple[str, str]] = []
            for name, value in context_id.axis_values:
                path_components.append((name, value))

            for name, value in context_id.args:
                str_value = ",".join(value) if isinstance(value, tuple) else value
                path_components.append((f"args.{name}", str_value))

            for name, value in context_id.flags:
                path_components.append((f"flags.{name}", str(value).lower()))

            # Build nested structure
            current: dict[str, Any] = result
            for comp_name, comp_value in path_components:
                if comp_name not in current:
                    current[comp_name] = {}
                current = current[comp_name]
                if comp_value not in current:
                    current[comp_value] = {}
                current = current[comp_value]

            if action_name in current:
                raise RuntimeError(
                    f"Internal error: duplicate output for action '{action_name}' in context '{context_id}'. "
                    "This indicates a bug in context isolation."
                )
            current[action_name] = action_result.outputs

        return result


@dataclass
class PreparedAction:
    """Artifacts required to execute an action."""

    action_key: ActionKey
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

    Each action has its own context embedded in its ActionKey. The args
    and flags parameters may be global or overridden per-action.
    """

    def __init__(
        self,
        graph: ActionGraph,
        project_root: Path,
        args: dict[str, str],
        flags: dict[str, bool],
        environment_vars: dict[str, str],
        passthrough_env_vars: list[str],
        run_directory: Optional[Path] = None,
        previous_run_directory: Optional[Path] = None,
        github_actions: bool = False,
        without_nix: bool = False,
        verbose: bool = False,
        no_output_on_fail: bool = False,
        keep_run_dir: bool = False,
        no_color: bool = False,
        simple_log: bool = False,
        show_dirs: bool = False,
        parallel_execution: bool = True,
        use_short_context_ids: bool = False,
        keep_running: bool = False,
        timeout_ms: Optional[int] = None,
    ):
        self.graph = graph
        self.project_root = project_root
        self.args = args
        self.flags = flags
        self.environment_vars = environment_vars
        self.passthrough_env_vars = passthrough_env_vars
        self.previous_run_directory = previous_run_directory
        self.github_actions = github_actions
        self.without_nix = without_nix
        self.verbose = verbose
        self.no_output_on_fail = no_output_on_fail
        self.keep_run_dir = keep_run_dir
        self.no_color = no_color
        self.simple_log = simple_log
        self.show_dirs = show_dirs
        self.parallel_execution = parallel_execution
        self.use_short_context_ids = use_short_context_ids
        self.keep_running = keep_running
        self.timeout_ms = timeout_ms

        # Create output formatter (includes all sub-formatters)
        self.output = OutputFormatter(no_color=no_color)

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

        # Kill signal for graceful termination from interactive table
        self._kill_event = threading.Event()
        self._current_logger: Optional["ActionLogger"] = None
        self._current_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
        self._running_processes: set[subprocess.Popen] = set()
        self._processes_lock = threading.Lock()
        self._timeout_timer: Optional[threading.Timer] = None

    def _start_timeout_timer(self) -> None:
        """Start a background timer that kills all processes on timeout.

        Does nothing if no timeout is configured.
        """
        if self.timeout_ms is None:
            return

        timeout_seconds = self.timeout_ms / 1000.0

        def on_timeout() -> None:
            if self._current_logger:
                self._current_logger.stop()
            self.output.print_warning(f"Timeout of {self.timeout_ms}ms exceeded, killing all processes")
            self._request_kill()

        self._timeout_timer = threading.Timer(timeout_seconds, on_timeout)
        self._timeout_timer.daemon = True
        self._timeout_timer.start()

    def _cancel_timeout_timer(self) -> None:
        """Cancel the timeout timer if it is running."""
        if self._timeout_timer is not None:
            self._timeout_timer.cancel()
            self._timeout_timer = None

    def _kill_process_tree(self, process: subprocess.Popen) -> None:
        """Kill a process and all its children.

        On Unix: Uses process groups (SIGKILL to pgid) for reliable child termination.
        On Windows: Uses taskkill /T /F for process tree termination.

        Args:
            process: The subprocess to kill
        """
        # Check if process is still running
        if process.poll() is not None:
            return

        pid = process.pid

        if sys.platform == "win32":
            # Windows: taskkill /T kills the process tree, /F forces termination
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=5,
                )
            except Exception:
                # Fallback to basic terminate
                try:
                    process.terminate()
                except Exception:
                    pass
        else:
            # Unix: Kill the entire process group
            # This is necessary because nix develop spawns child processes that
            # don't receive signals when we only terminate the parent
            try:
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                # Process or group already terminated
                pass
            except Exception:
                # Fallback: try regular kill on the process itself
                try:
                    process.kill()
                except Exception:
                    pass

    def _request_kill(self) -> None:
        """Request immediate termination of all running processes.

        Called by logger when user presses 'q' in main view.
        Uses SIGKILL/taskkill for immediate termination since user explicitly
        requested to quit.
        """
        self._kill_event.set()

        # Kill all running subprocesses and their process trees
        with self._processes_lock:
            for process in list(self._running_processes):
                self._kill_process_tree(process)

        # If we have an executor, try to shutdown
        if self._current_executor:
            try:
                self._current_executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass

    def _format_action_key(self, action_key: ActionKey) -> str:
        """Format an action key for display.

        Args:
            action_key: Action key to format

        Returns:
            Formatted string (short ID with symbol/emoji or full context)
        """
        return self.output.action.format_label_plain(action_key, self.use_short_context_ids)

    def _get_action_dirname(self, action_key: ActionKey) -> str:
        """Get the directory name for an action.

        Uses context in dirname only when multiple contexts exist in the graph.

        Args:
            action_key: Action key to get dirname for

        Returns:
            Directory name (with or without context prefix)
        """
        contexts_in_graph = {node.key.context_id for node in self.graph.nodes.values()}
        use_context_in_dirname = len(contexts_in_graph) > 1

        if use_context_in_dirname:
            action_key_str = str(action_key)
            return truncate_dirname(action_key_str.replace(":", "_"))
        else:
            return action_key.id.name

    def _get_action_dir(self, action_key: ActionKey) -> Path:
        """Get the full directory path for an action.

        Args:
            action_key: Action key to get directory for

        Returns:
            Full path to action directory under run_directory
        """
        return self.run_directory / self._get_action_dirname(action_key)

    def _build_action_dir_mapping(self, action_keys: list[ActionKey]) -> dict[str, str]:
        """Build mapping of display names to relative action directory paths.

        Args:
            action_keys: List of action keys in execution order

        Returns:
            Dictionary mapping display names to relative directory paths
        """
        result = {}
        for action_key in action_keys:
            display_name = self._format_action_key(action_key)
            action_dirname = self._get_action_dirname(action_key)
            relative_path = f".mdl/runs/{self.run_directory.name}/{action_dirname}"
            result[display_name] = relative_path

        return result

    def _create_action_logger(self, execution_order: list[ActionKey]) -> ActionLogger:
        """Create and start an ActionLogger based on execution mode.

        Uses ActionLoggerRaw for simple_log/github_actions/verbose modes,
        otherwise uses ActionLoggerInteractive.

        Args:
            execution_order: List of action keys in execution order

        Returns:
            ActionLogger instance (ActionLoggerRaw or ActionLoggerInteractive)
        """
        from .action_logger_raw import ActionLoggerRaw
        from .action_logger_interactive import ActionLoggerInteractive

        # Use raw logger for simple_log, github_actions or verbose modes
        # Use interactive table for normal execution
        if self.simple_log or self.github_actions or self.verbose:
            logger = ActionLoggerRaw(
                action_keys=execution_order,
                output=self.output,
                use_short_ids=self.use_short_context_ids,
                github_actions=self.github_actions,
            )
        else:
            logger = ActionLoggerInteractive(
                execution_order,
                no_color=self.no_color,
                action_dirs=self._build_action_dir_mapping(execution_order),
                show_dirs=self.show_dirs,
                run_directory=self.run_directory,
                keep_running=self.keep_running,
                use_short_ids=self.use_short_context_ids,
            )
    
        logger.start()
        logger.set_kill_callback(self._request_kill)
        return logger

    def _notify_action_start(
        self,
        logger: ActionLogger,
        action_key: ActionKey,
        action_dir: Path,
    ) -> None:
        """Notify that an action is starting.

        Args:
            logger: ActionLogger instance
            action_key: Action key being started
            action_dir: Action directory path
        """
        logger.mark_running(action_key, action_dir)

    def _notify_action_result(
        self,
        logger: ActionLogger,
        action_key: ActionKey,
        action_dir: Path,
        result: ActionResult,
    ) -> None:
        """Notify that an action has completed.

        Args:
            logger: ActionLogger instance
            action_key: Action key that completed
            action_dir: Action directory path
            result: Action execution result
        """
        logger.update_output_sizes(action_key, result.stdout_size, result.stderr_size)
        if result.restored:
            logger.mark_restored(action_key, result.duration_seconds, action_dir)
        elif result.success:
            logger.mark_done(action_key, result.duration_seconds)
        else:
            logger.mark_failed(action_key, result.duration_seconds)

    def _print_action_failure(self, result: ActionResult) -> None:
        """Print diagnostic information for a failed action.

        Args:
            result: The failed action result
        """
        suppress_outputs = self.no_output_on_fail and not (self.github_actions or self.verbose)
        sym = self.output.symbols

        self.output.print(f"\n{sym.Cross} [bold red]Action '{result.action_name}' failed![/bold red]")
        self.output.print(f"{sym.Folder} [dim]Run directory:[/dim] [bold cyan]{self.run_directory}[/bold cyan]")

        self.output.print(f"\n{sym.File} [dim]Stdout:[/dim] [blue]{result.stdout_path}[/blue]")
        if result.stdout_path.exists() and not suppress_outputs:
            self.output.print(result.stdout_path.read_text(encoding="utf-8"))

        self.output.print(f"\n{sym.File} [dim]Stderr:[/dim] [blue]{result.stderr_path}[/blue]")
        if result.stderr_path.exists() and not suppress_outputs:
            self.output.print(result.stderr_path.read_text(encoding="utf-8"))

        if suppress_outputs:
            self.output.print("[dim]Output suppressed; re-run with --verbose or inspect log files for details.[/dim]")

        if result.error_message:
            self.output.print(f"\n{sym.Cross} [bold red]Error:[/bold red] {result.error_message}")

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
        except ValueError:
            return ExecutionResult(
                success=False,
                action_results={},
                run_directory=self.run_directory,
            )

        # Create action logger (interactive table or raw text output)
        logger = self._create_action_logger(execution_order)
        self._current_logger = logger  # Keep for subprocess access

        self._start_timeout_timer()
        try:
            action_outputs: dict[ActionKey, dict[str, Any]] = {}
            action_results: dict[ActionKey, ActionResult] = {}
            restored_actions: list[ActionKey] = []

            for action_key in execution_order:
                if self._kill_event.is_set():
                    logger.stop()
                    return ExecutionResult(
                        success=False,
                        action_results=action_results,
                        run_directory=self.run_directory,
                    )

                action_dir = self._get_action_dir(action_key)
                self._notify_action_start(logger, action_key, action_dir)

                result = self._execute_action(action_key, action_outputs)
                action_results[action_key] = result

                if result.restored:
                    restored_actions.append(action_key)

                self._notify_action_result(logger, action_key, action_dir, result)

                if not result.success:
                    logger.stop()
                    self._print_action_failure(result)

                    return ExecutionResult(
                        success=False,
                        action_results=action_results,
                        run_directory=self.run_directory,
                    )

                action_outputs[action_key] = result.outputs

        finally:
            self._cancel_timeout_timer()
            if not self.keep_running:
                logger.stop()

        # If --it flag, wait for user to quit BEFORE printing final messages
        if self.keep_running:
            logger.wait_for_quit()

        result = self._finalize_successful_execution(action_results, restored_actions, graph_start_time)

        return result

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
        action_outputs: dict[ActionKey, dict[str, Any]] = {}
        action_results: dict[ActionKey, ActionResult] = {}
        lock = threading.Lock()
        running: dict[concurrent.futures.Future[ActionResult], ActionKey] = {}
        max_workers = max(1, min(32, os.cpu_count() or 1))

        # Create action logger
        execution_order = self.graph.get_execution_order()
        logger = self._create_action_logger(execution_order)
        self._current_logger = logger  # Keep for subprocess access

        restored_actions: list[ActionKey] = []

        def submit_action(executor: concurrent.futures.ThreadPoolExecutor, action_key: ActionKey) -> None:
            scheduled.add(action_key)
            action_dir = self._get_action_dir(action_key)
            self._notify_action_start(logger, action_key, action_dir)
            snapshot_outputs = dict(action_outputs)
            future = executor.submit(self._execute_action, action_key, snapshot_outputs)
            running[future] = action_key

        self._start_timeout_timer()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                self._current_executor = executor
                for key in ready:
                    submit_action(executor, key)

                while running:
                    if self._kill_event.is_set():
                        executor.shutdown(wait=False, cancel_futures=True)
                        logger.stop()
                        return ExecutionResult(
                            success=False,
                            action_results=action_results,
                            run_directory=self.run_directory,
                        )

                    done, _ = concurrent.futures.wait(
                        running.keys(),
                        timeout=0.1,
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )

                    if not done:
                        continue

                    for future in done:
                        action_key = running.pop(future)
                        try:
                            result = future.result()
                        except Exception as exc:  # pragma: no cover - defensive
                            result = ActionResult(
                                action_name=str(action_key),
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

                        action_results[action_key] = result

                        if result.restored:
                            with lock:
                                restored_actions.append(action_key)

                        action_dir = self._get_action_dir(action_key)
                        self._notify_action_result(logger, action_key, action_dir, result)

                        if not result.success:
                            logger.stop()
                            self._print_action_failure(result)

                            executor.shutdown(cancel_futures=True)
                            return ExecutionResult(
                                success=False,
                                action_results=action_results,
                                run_directory=self.run_directory,
                            )

                        with lock:
                            action_outputs[action_key] = result.outputs
                            completed.add(action_key)

                        for dependent_key in dependents.get(action_key, set()):
                            if dependent_key in completed or dependent_key in scheduled:
                                continue
                            pending_deps[dependent_key].discard(action_key)
                            if len(pending_deps[dependent_key]) == 0:
                                submit_action(executor, dependent_key)

        except KeyboardInterrupt:
            for future in running:
                future.cancel()
            logger.stop()

            return ExecutionResult(
                success=False,
                action_results=action_results,
                run_directory=self.run_directory,
            )
        finally:
            self._cancel_timeout_timer()
            self._current_executor = None
            if not self.keep_running:
                logger.stop()

        # If --it flag, wait for user to quit BEFORE printing final messages
        if self.keep_running:
            logger.wait_for_quit()

        result = self._finalize_successful_execution(action_results, restored_actions, graph_start_time)

        return result

    def _execute_action(
        self, action_key: ActionKey, action_outputs: dict[ActionKey, dict[str, Any]]
    ) -> ActionResult:
        """Execute a single action identified by its ActionKey."""
        action_key_str = str(action_key)

        # Determine directory name using same logic as preparation
        contexts_in_graph = {node.key.context_id for node in self.graph.nodes.values()}
        use_context_in_dirname = len(contexts_in_graph) > 1
        if use_context_in_dirname:
            action_dirname = truncate_dirname(action_key_str.replace(":", "_"))
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
        self, action_key: ActionKey, action_outputs: dict[ActionKey, dict[str, Any]]
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
            # Truncate long names to avoid filesystem limits
            action_key_str = str(action_key)
            safe_dir_name = truncate_dirname(action_key_str.replace(":", "_"))
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
        script_path.write_text(rendered.content, encoding="utf-8")
        script_path.chmod(0o755)

        stdout_path = action_dir / "stdout.log"
        stderr_path = action_dir / "stderr.log"

        base_exec_cmd = runtime.get_execution_command(script_path)
        exec_cmd = self._build_exec_command(action, base_exec_cmd)

        return PreparedAction(
            action_key=action_key,
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
        action_outputs: dict[ActionKey, dict[str, Any]],
        args: dict[str, str],
        flags: dict[str, bool],
        action_key: ActionKey,
    ) -> ExecutionContext:
        """Build execution context with provided args and flags.

        Args:
            action_dir: Action directory path
            action_outputs: Outputs from previous actions (keyed by ActionKey)
            args: Arguments for this action (may be per-action or global)
            flags: Flags for this action (may be per-action or global)
            action_key: The ActionKey for this action (to resolve dependencies)

        Returns:
            ExecutionContext for the action
        """
        # Map dependency action names to their outputs using the graph's dependencies.
        # With reduced contexts, dependencies may be in different contexts than the
        # action itself. We use the actual dependency ActionKeys from the graph to
        # resolve the correct outputs.
        dependency_outputs: dict[str, dict[str, Any]] = {}
        node = self.graph.get_node(action_key)
        for dep in node.dependencies:
            if dep.action in action_outputs:
                # Use the dependency's action name as the key for ${action.name.var}
                dependency_outputs[dep.action.id.name] = action_outputs[dep.action]

        axis_values = action_key.context_id.to_dict()
        axis_sys_vars = {f"axis.{name}": value for name, value in axis_values.items()}

        return ExecutionContext(
            system_vars={
                "project-root": str(self.project_root),
                "run-dir": str(self.run_directory),
                "action-dir": str(action_dir),
                "nix": not self.without_nix,
                **axis_sys_vars,
            },
            axis_values=axis_values,
            env_vars=dict(os.environ) | self.environment_vars,
            md_env_vars=self.environment_vars,
            args=args,
            flags=flags,
            action_outputs=dependency_outputs,
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

    def _execute_subprocess(
        self,
        prepared: PreparedAction,
        logger: Optional[ActionLogger],
    ) -> SubprocessResult:
        """Execute subprocess and stream output to files.

        Args:
            prepared: Prepared action with paths and command
            logger: Optional action logger for progress updates

        Returns:
            SubprocessResult with returncode and output sizes
        """
        stdout_size = 0
        stderr_size = 0

        with open(prepared.stdout_path, "w", encoding="utf-8") as stdout_file, open(
            prepared.stderr_path, "w", encoding="utf-8"
        ) as stderr_file:
            # Unix: start_new_session=True creates a new process group, allowing us to
            # kill the entire process tree (including nix develop children) via os.killpg.
            # Windows: Not needed - we use taskkill /T which traverses parent-child tree.
            process = subprocess.Popen(
                prepared.exec_cmd,
                cwd=str(self.project_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=os.environ.copy(),
                text=True,
                encoding="utf-8",
                bufsize=1,
                start_new_session=(sys.platform != "win32"),
            )

            with self._processes_lock:
                self._running_processes.add(process)

            try:
                size_lock = threading.Lock()

                def stream_output(
                    pipe: Any,
                    console_stream: Any,
                    file_stream: Any,
                    combined_file: Any,
                    is_stdout: bool,
                ) -> None:
                    nonlocal stdout_size, stderr_size
                    if not pipe:
                        return
                    for line in pipe:
                        if self.github_actions or self.verbose:
                            console_stream.write(line)
                            console_stream.flush()

                        file_stream.write(line)
                        file_stream.flush()

                        if not is_stdout and combined_file:
                            combined_file.write(line)
                            combined_file.flush()

                        line_bytes = len(line.encode("utf-8"))
                        with size_lock:
                            if is_stdout:
                                stdout_size += line_bytes
                            else:
                                stderr_size += line_bytes
                                stdout_size += line_bytes

                            if logger:
                                logger.update_output_sizes(
                                    prepared.action_key, stdout_size, stderr_size
                                )

                stdout_thread = threading.Thread(
                    target=stream_output,
                    args=(process.stdout, sys.stdout, stdout_file, None, True),
                )
                stderr_thread = threading.Thread(
                    target=stream_output,
                    args=(process.stderr, sys.stderr, stderr_file, stdout_file, False),
                )

                stdout_thread.start()
                stderr_thread.start()
                returncode = process.wait()
                stdout_thread.join()
                stderr_thread.join()
            finally:
                with self._processes_lock:
                    self._running_processes.discard(process)

        # Get final file sizes
        if prepared.stdout_path.exists():
            stdout_size = prepared.stdout_path.stat().st_size
        if prepared.stderr_path.exists():
            stderr_size = prepared.stderr_path.stat().st_size

        if logger:
            logger.update_output_sizes(prepared.action_key, stdout_size, stderr_size)

        return SubprocessResult(
            returncode=returncode,
            stdout_size=stdout_size,
            stderr_size=stderr_size,
        )

    def _create_action_result(
        self,
        prepared: PreparedAction,
        success: bool,
        outputs: dict[str, Any],
        start_time_iso: str,
        end_time_iso: str,
        duration: float,
        exit_code: int,
        stdout_size: int,
        stderr_size: int,
        error_message: Optional[str] = None,
    ) -> ActionResult:
        """Create ActionResult with common parameters from PreparedAction.

        Args:
            prepared: Prepared action with paths
            success: Whether action succeeded
            outputs: Action outputs
            start_time_iso: Start time in ISO format
            end_time_iso: End time in ISO format
            duration: Duration in seconds
            exit_code: Process exit code
            stdout_size: Size of stdout in bytes
            stderr_size: Size of stderr in bytes
            error_message: Optional error message

        Returns:
            ActionResult with all fields populated
        """
        self._write_action_meta(
            prepared.action_dir,
            prepared.action.name,
            success=success,
            start_time=start_time_iso,
            end_time=end_time_iso,
            duration=duration,
            exit_code=exit_code,
            error_message=error_message,
            stdout_size=stdout_size,
            stderr_size=stderr_size,
        )
        return ActionResult(
            action_name=prepared.action.name,
            success=success,
            outputs=outputs,
            stdout_path=prepared.stdout_path,
            stderr_path=prepared.stderr_path,
            script_path=prepared.script_path,
            start_time=start_time_iso,
            end_time=end_time_iso,
            duration_seconds=duration,
            exit_code=exit_code,
            error_message=error_message,
            stdout_size=stdout_size,
            stderr_size=stderr_size,
        )

    def _validate_file_outputs(
        self,
        outputs: dict[str, Any],
        return_declarations: list,
    ) -> Optional[str]:
        """Validate FILE/DIRECTORY outputs exist.

        Args:
            outputs: Parsed action outputs
            return_declarations: Return declarations to validate

        Returns:
            Error message if validation fails, None if all valid
        """
        for ret_decl in return_declarations:
            if ret_decl.return_type not in (ReturnType.FILE, ReturnType.DIRECTORY):
                continue

            output_value = outputs.get(ret_decl.name)
            if output_value is None:
                return f"Output '{ret_decl.name}' not found"

            path = Path(output_value)
            if not path.is_absolute():
                path = self.project_root / path

            if not path.exists():
                return (
                    f"{ret_decl.return_type.value.capitalize()} "
                    f"'{ret_decl.name}' does not exist: {output_value}"
                )

            if ret_decl.return_type == ReturnType.FILE and not path.is_file():
                return f"Output '{ret_decl.name}' is not a file: {output_value}"

        return None

    def _run_prepared_action(self, prepared: PreparedAction) -> ActionResult:
        """Execute a prepared action and return the result.

        Args:
            prepared: Prepared action with paths and command

        Returns:
            ActionResult with execution outcome
        """
        action_name = prepared.action.name
        logger = self._current_logger

        # Print GitHub Actions group start
        if self.github_actions:
            self.output.print_raw(f"::group::{action_name}")

        # Print command if verbose
        if self.github_actions or self.verbose:
            self.output.print_command(' '.join(prepared.exec_cmd))

        start_time = datetime.now()
        start_time_iso = start_time.isoformat()

        try:
            # Execute subprocess with output streaming
            subprocess_result = self._execute_subprocess(prepared, logger)

            if self.github_actions:
                self.output.print_raw("::endgroup::")

            end_time = datetime.now()
            end_time_iso = end_time.isoformat()
            duration = (end_time - start_time).total_seconds()

            # Handle non-zero exit code
            if subprocess_result.returncode != 0:
                if prepared.output_json_path.exists():
                    self._add_success_to_output_json(prepared.output_json_path, success=False)
                return self._create_action_result(
                    prepared,
                    success=False,
                    outputs={},
                    start_time_iso=start_time_iso,
                    end_time_iso=end_time_iso,
                    duration=duration,
                    exit_code=subprocess_result.returncode,
                    stdout_size=subprocess_result.stdout_size,
                    stderr_size=subprocess_result.stderr_size,
                    error_message=f"Script exited with code {subprocess_result.returncode}",
                )

            # Handle missing output.json
            if not prepared.output_json_path.exists():
                return self._create_action_result(
                    prepared,
                    success=False,
                    outputs={},
                    start_time_iso=start_time_iso,
                    end_time_iso=end_time_iso,
                    duration=duration,
                    exit_code=subprocess_result.returncode,
                    stdout_size=subprocess_result.stdout_size,
                    stderr_size=subprocess_result.stderr_size,
                    error_message="No output.json generated",
                )

            # Parse outputs
            outputs = self._parse_outputs(prepared.output_json_path)
            self._add_success_to_output_json(prepared.output_json_path, success=True)

            # Validate file/directory outputs
            validation_error = self._validate_file_outputs(
                outputs, prepared.version.return_declarations
            )
            if validation_error:
                return self._create_action_result(
                    prepared,
                    success=False,
                    outputs=outputs,
                    start_time_iso=start_time_iso,
                    end_time_iso=end_time_iso,
                    duration=duration,
                    exit_code=0,
                    stdout_size=subprocess_result.stdout_size,
                    stderr_size=subprocess_result.stderr_size,
                    error_message=validation_error,
                )

            # Success
            return self._create_action_result(
                prepared,
                success=True,
                outputs=outputs,
                start_time_iso=start_time_iso,
                end_time_iso=end_time_iso,
                duration=duration,
                exit_code=0,
                stdout_size=subprocess_result.stdout_size,
                stderr_size=subprocess_result.stderr_size,
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

            return self._create_action_result(
                prepared,
                success=False,
                outputs={},
                start_time_iso=start_time_iso,
                end_time_iso=end_time_iso,
                duration=duration,
                exit_code=-1,
                stdout_size=exc_stdout_size,
                stderr_size=exc_stderr_size,
                error_message=f"Execution error: {e}",
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

        (action_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def _finalize_successful_execution(
        self,
        action_results: dict[ActionKey, ActionResult],
        restored_actions: list[ActionKey],
        graph_start_time: float,
    ) -> ExecutionResult:
        graph_duration = time.time() - graph_start_time
        sym = self.output.symbols

        if restored_actions and not self.github_actions:
            restored_list = ", ".join(str(key) for key in restored_actions)
            self.output.print(f"\n{sym.Recycle} [dim]restored from previous run:[/dim] [bold cyan]{restored_list}[/bold cyan]")

        if not self.keep_run_dir:
            try:
                shutil.rmtree(self.run_directory)
            except Exception as e:
                self.output.print(f"{sym.Warning} [bold yellow]Warning:[/bold yellow] Failed to clean up run directory: {e}")

        if not self.github_actions:
            self.output.print(f"\n[dim]Total wall time:[/dim] [bold cyan]{graph_duration:.1f}s[/bold cyan]")

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
            meta = json.loads(prev_meta_path.read_text(encoding="utf-8"))
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
        meta = json.loads(prev_meta_path.read_text(encoding="utf-8"))

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
            outputs = self._parse_outputs(prev_output_path)

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

    def _parse_outputs(self, output_json_path: Path) -> dict[str, Any]:
        """Parse outputs from output.json.

        Args:
            output_json_path: Path to output.json

        Returns:
            Dictionary of outputs
        """
        try:
            data = json.loads(output_json_path.read_text(encoding="utf-8"))

            # Extract just the values
            outputs = {}
            for name, info in data.items():
                outputs[name] = info["value"]

            return outputs
        except Exception as e:
            raise ValueError(f"Failed to parse output.json: {e}")

    def _add_success_to_output_json(self, output_json_path: Path, success: bool) -> None:
        """Add success field to output.json.

        Args:
            output_json_path: Path to output.json
            success: Whether the action succeeded
        """
        try:
            data = json.loads(output_json_path.read_text(encoding="utf-8"))
            data["success"] = {"type": "bool", "value": success}
            output_json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            # Don't fail if we can't add success field
            self.output.print_warning(f"Failed to add success field to output.json: {e}")
