"""Executor for retainer actions that decide soft dependency retention."""

import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..ast.models import ActionDefinition, ActionVersion
from ..dag.graph import ActionGraph, ActionKey, Dependency
from .runtime_registry import RuntimeRegistry
from .bash_runtime import BashRuntime
from .python_runtime import PythonRuntime
from .language_runtime import ExecutionContext


@dataclass
class RetainerResult:
    """Result of executing a single retainer action."""

    retainer_key: ActionKey
    soft_dep_targets: list[ActionKey]
    retained: bool
    execution_time_ms: float


class RetainerExecutor:
    """Executes retainer actions to determine soft dependency retention.

    Retainer actions are special actions that decide whether a soft dependency
    should be retained in the execution graph. They must have no dependencies
    and signal their decision by calling retain() which creates a signal file.
    """

    def __init__(
        self,
        graph: ActionGraph,
        document: "ParsedDocument",
        project_root: Path,
        environment_vars: dict[str, str],
        passthrough_env_vars: list[str],
        without_nix: bool = False,
    ):
        """Initialize the retainer executor.

        Args:
            graph: The full action graph (before pruning)
            document: Parsed document with action definitions
            project_root: Project root directory
            environment_vars: Environment variables for actions
            passthrough_env_vars: Env vars to pass through from parent
            without_nix: Whether to skip nix wrapping
        """
        self.graph = graph
        self.document = document
        self.project_root = project_root
        self.environment_vars = environment_vars
        self.passthrough_env_vars = passthrough_env_vars
        self.without_nix = without_nix

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
            retain_result = self._execute_retainer(retainer_key)
            elapsed_ms = (time.perf_counter() - start_time) * 1000

            # Determine which targets to actually retain
            actually_retained: list[ActionKey] = []
            if retain_result is not None:
                if len(retain_result) == 0:
                    # Empty set = retain all
                    for dep in soft_deps:
                        retained_targets.add(dep.action)
                        actually_retained.append(dep.action)
                else:
                    # Selective retention: only retain deps where the dependent action is in the set
                    for dep in soft_deps:
                        # dep is a soft dependency FROM some action TO dep.action
                        # We need to find who depends on dep.action using this retainer
                        # The dependent action's name should match one in retain_result
                        for node_key, node in self.graph.nodes.items():
                            for node_dep in node.dependencies:
                                if (node_dep.soft and
                                    node_dep.action == dep.action and
                                    node_dep.retainer_action == retainer_key and
                                    node_key.id.name in retain_result):
                                    retained_targets.add(dep.action)
                                    actually_retained.append(dep.action)

            retainer_results.append(RetainerResult(
                retainer_key=retainer_key,
                soft_dep_targets=actually_retained if retain_result is not None else [],
                retained=retain_result is not None,
                execution_time_ms=elapsed_ms,
            ))

        return retained_targets, retainer_results

    def _execute_retainer(self, retainer_key: ActionKey) -> set[str] | None:
        """Execute a single retainer action.

        Args:
            retainer_key: Key of the retainer action to execute

        Returns:
            None if retainer didn't signal to retain anything,
            empty set if retainer signals to retain all soft deps,
            non-empty set of action names if retainer signals selective retention.
        """
        if retainer_key not in self.graph.nodes:
            return None

        retainer_node = self.graph.nodes[retainer_key]
        action = retainer_node.action
        version = retainer_node.selected_version

        if not version:
            return None

        # Create temporary directory for retainer execution
        with tempfile.TemporaryDirectory(prefix="mdl_retainer_") as temp_dir:
            temp_path = Path(temp_dir)
            retain_signal_file = temp_path / "retain_signal"

            # Prepare script
            runtime = RuntimeRegistry.get(version.language)
            output_json_path = temp_path / "output.json"

            # Build execution context (minimal - retainers have no dependencies)
            context = self._build_retainer_context(retain_signal_file)

            # Prepare script
            rendered = runtime.prepare_script(
                version, context, output_json_path, temp_path
            )

            # Write script
            script_ext = ".sh" if version.language == "bash" else ".py"
            script_path = temp_path / f"retainer{script_ext}"
            script_path.write_text(rendered.content)
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
                    timeout=60,  # 1 minute timeout for retainers
                )

                # Check if retainer succeeded
                if result.returncode != 0:
                    return None

                # Check for retain signal
                if not retain_signal_file.exists():
                    return None

                # Read signal file to determine what to retain
                content = retain_signal_file.read_text().strip()
                if not content:
                    # Empty file = retain all
                    return set()
                else:
                    # File contains specific action names (one per line)
                    return set(line.strip() for line in content.split("\n") if line.strip())

            except subprocess.TimeoutExpired:
                return None
            except Exception:
                return None

    def _build_retainer_context(self, retain_signal_file: Path) -> ExecutionContext:
        """Build minimal execution context for a retainer action."""
        import os

        # Build environment variables
        env_vars = dict(self.environment_vars)
        for var_name in self.passthrough_env_vars:
            if var_name in os.environ:
                env_vars[var_name] = os.environ[var_name]

        return ExecutionContext(
            system_vars={
                "project-root": str(self.project_root),
                "nix": not self.without_nix,
            },
            axis_values={},
            env_vars=env_vars,
            md_env_vars=self.environment_vars,
            args={},
            flags={},
            action_outputs={},  # Retainers have no dependencies
        )

    def _build_execution_command(
        self, runtime: Any, script_path: Path
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
        import os

        env = dict(os.environ)
        env["MDL_RETAIN_SIGNAL_FILE"] = str(retain_signal_file)
        return env
