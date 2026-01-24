"""Executor for retainer actions that decide soft dependency retention."""

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..ast.models import ParsedDocument
from ..dag.graph import ActionGraph, ActionKey, Dependency
from .runtime_registry import RuntimeRegistry
from .runtime_bash import BashRuntime
from .runtime_python import PythonRuntime
from .language_runtime import ExecutionContext, LanguageRuntime


@dataclass
class RetainerExecutionResult:
    """Internal result from executing a retainer."""

    retained_actions: set[str] | None  # None = don't retain, empty = all, non-empty = selective
    stdout: str
    stderr: str


@dataclass
class RetainerResult:
    """Result of executing a single retainer action."""

    retainer_key: ActionKey
    soft_dep_targets: list[ActionKey]
    retained: bool
    execution_time_ms: float
    stdout: str = ""
    stderr: str = ""


class RetainerExecutor:
    """Executes retainer actions to determine soft dependency retention.

    Retainer actions are special actions that decide whether a soft dependency
    should be retained in the execution graph. They must have no dependencies
    and signal their decision by calling retain() which creates a signal file.
    """

    def __init__(
        self,
        graph: ActionGraph,
        document: ParsedDocument,
        project_root: Path,
        environment_vars: dict[str, str],
        passthrough_env_vars: list[str],
        args: dict[str, Any],
        flags: dict[str, bool],
        axis_values: dict[str, str],
        without_nix: bool = False,
        verbose: bool = False,
    ):
        """Initialize the retainer executor.

        Args:
            graph: The full action graph (before pruning)
            document: Parsed document with action definitions
            project_root: Project root directory
            environment_vars: Environment variables for actions
            passthrough_env_vars: Env vars to pass through from parent
            args: Command-line arguments
            flags: Command-line flags
            axis_values: Axis values for the current context
            without_nix: Whether to skip nix wrapping
            verbose: Whether to capture stdout/stderr for logging
        """
        self.graph = graph
        self.document = document
        self.project_root = project_root
        self.environment_vars = environment_vars
        self.passthrough_env_vars = passthrough_env_vars
        self.args = args
        self.flags = flags
        self.axis_values = axis_values
        self.without_nix = without_nix
        self.verbose = verbose

        # Register runtimes
        for runtime_cls in (BashRuntime, PythonRuntime):
            RuntimeRegistry.ensure_registered(runtime_cls)

    def execute_retainers(self) -> tuple[set[ActionKey], list[RetainerResult]]:
        """Execute retainer actions and return soft dependency targets to retain.

        Returns:
            Tuple of (retained_targets, retainer_results) where:
            - retained_targets: Set of ActionKeys for soft dependency targets to retain
            - retainer_results: List of RetainerResult with execution details
        """
        pending_soft_deps = self.graph.get_pending_soft_dependencies()

        if not pending_soft_deps:
            return set(), []

        retained_targets: set[ActionKey] = set()
        retainer_results: list[RetainerResult] = []

        # Group by retainer to avoid running the same retainer multiple times
        retainers_to_run: dict[ActionKey, list[Dependency]] = {}
        for dep in pending_soft_deps:
            if dep.retainer_action:
                if dep.retainer_action not in retainers_to_run:
                    retainers_to_run[dep.retainer_action] = []
                retainers_to_run[dep.retainer_action].append(dep)

        # Execute each unique retainer
        for retainer_key, soft_deps in retainers_to_run.items():
            start_time = time.perf_counter()
            exec_result = self._execute_retainer(retainer_key)
            elapsed_ms = (time.perf_counter() - start_time) * 1000

            # Determine which targets to actually retain
            actually_retained: list[ActionKey] = []
            if exec_result.retained_actions is not None:
                if len(exec_result.retained_actions) == 0:
                    # Empty set = retain all
                    for dep in soft_deps:
                        retained_targets.add(dep.action)
                        actually_retained.append(dep.action)
                else:
                    # Selective retention: only retain deps where the target action is in the set
                    for dep in soft_deps:
                        # dep.action is the TARGET of the soft dependency
                        # Check if the target's name is in the retained set
                        if dep.action.id.name in exec_result.retained_actions:
                            retained_targets.add(dep.action)
                            actually_retained.append(dep.action)

            retainer_results.append(RetainerResult(
                retainer_key=retainer_key,
                soft_dep_targets=actually_retained if exec_result.retained_actions is not None else [],
                retained=exec_result.retained_actions is not None,
                execution_time_ms=elapsed_ms,
                stdout=exec_result.stdout,
                stderr=exec_result.stderr,
            ))

        return retained_targets, retainer_results

    def _execute_retainer(self, retainer_key: ActionKey) -> RetainerExecutionResult:
        """Execute a single retainer action.

        Args:
            retainer_key: Key of the retainer action to execute

        Returns:
            RetainerExecutionResult with:
            - retained_actions: None if didn't retain, empty set for all, non-empty for selective
            - stdout/stderr: captured output from the retainer
        """
        if retainer_key not in self.graph.nodes:
            return RetainerExecutionResult(retained_actions=None, stdout="", stderr="")

        retainer_node = self.graph.nodes[retainer_key]
        version = retainer_node.selected_version

        if not version:
            return RetainerExecutionResult(retained_actions=None, stdout="", stderr="")

        # Create temporary directory for retainer execution
        with tempfile.TemporaryDirectory(prefix="mdl_retainer_") as temp_dir:
            temp_path = Path(temp_dir)
            retain_signal_file = temp_path / "retain_signal"

            # Prepare script
            runtime = RuntimeRegistry.get(version.language)
            output_json_path = temp_path / "output.json"

            # Build execution context with context-specific args/flags/axis
            context = self._build_retainer_context(retainer_key)

            # Prepare script
            rendered = runtime.prepare_script(
                version, context, output_json_path, temp_path
            )

            # Write script
            script_ext = ".sh" if version.language == "bash" else ".py"
            script_path = temp_path / f"retainer{script_ext}"
            script_path.write_text(rendered.content, encoding="utf-8")
            script_path.chmod(0o755)

            # Build execution command
            exec_cmd = self._build_execution_command(runtime, script_path)

            # Execute
            env = self._build_environment(retain_signal_file)

            try:
                result = subprocess.run(
                    exec_cmd,
                    cwd=str(self.project_root),
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=60,  # 1 minute timeout for retainers
                )

                stdout = result.stdout or ""
                stderr = result.stderr or ""

                # Check if retainer succeeded
                if result.returncode != 0:
                    return RetainerExecutionResult(retained_actions=None, stdout=stdout, stderr=stderr)

                # Check for retain signal
                if not retain_signal_file.exists():
                    return RetainerExecutionResult(retained_actions=None, stdout=stdout, stderr=stderr)

                # Read signal file to determine what to retain
                content = retain_signal_file.read_text(encoding="utf-8").strip()
                if not content:
                    # Empty file = retain all
                    return RetainerExecutionResult(retained_actions=set(), stdout=stdout, stderr=stderr)
                else:
                    # File contains specific action names (one per line)
                    actions = set(line.strip() for line in content.split("\n") if line.strip())
                    return RetainerExecutionResult(retained_actions=actions, stdout=stdout, stderr=stderr)

            except subprocess.TimeoutExpired:
                return RetainerExecutionResult(retained_actions=None, stdout="", stderr="Timeout expired")
            except Exception as e:
                return RetainerExecutionResult(retained_actions=None, stdout="", stderr=str(e))

    def _build_retainer_context(
        self, retainer_key: ActionKey
    ) -> ExecutionContext:
        """Build execution context for a retainer action.

        Uses context-specific args/flags/axis_values from the retainer_key,
        falling back to global values for anything not specified in the context.
        """
        # Build environment variables
        env_vars = dict(self.environment_vars)
        for var_name in self.passthrough_env_vars:
            if var_name in os.environ:
                env_vars[var_name] = os.environ[var_name]

        # Extract context-specific values from retainer_key.context_id
        context_id = retainer_key.context_id

        # Start with global values, then override with context-specific ones
        axis_values = dict(self.axis_values)
        for name, value in context_id.axis_values:
            axis_values[name] = value

        args = dict(self.args)
        for name, value in context_id.args:
            args[name] = value

        flags = dict(self.flags)
        for name, value in context_id.flags:
            flags[name] = value

        return ExecutionContext(
            system_vars={
                "project-root": str(self.project_root),
                "nix": not self.without_nix,
            },
            axis_values=axis_values,
            env_vars=env_vars,
            md_env_vars=self.environment_vars,
            args=args,
            flags=flags,
            action_outputs={},  # Retainers have no dependencies
        )

    def _build_execution_command(
        self, runtime: LanguageRuntime, script_path: Path
    ) -> list[str]:
        """Build the command to execute the retainer script."""
        base_cmd = runtime.get_execution_command(script_path)

        if self.without_nix:
            return base_cmd

        # Wrap with nix if available
        flake_path = self.project_root / "flake.nix"
        if flake_path.exists():
            return [
                "nix",
                "develop",
                str(self.project_root),
                "-c",
            ] + base_cmd

        return base_cmd

    def _build_environment(self, retain_signal_file: Path) -> dict[str, str]:
        """Build environment variables for retainer execution."""
        env = dict(os.environ)
        env["MDL_RETAIN_SIGNAL_FILE"] = str(retain_signal_file)
        return env
