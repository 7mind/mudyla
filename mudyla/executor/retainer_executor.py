"""Executor for retainer actions that decide soft dependency retention."""

import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..ast.models import ActionDefinition, ActionVersion
from ..dag.graph import ActionGraph, ActionKey, Dependency
from .runtime_registry import RuntimeRegistry
from .bash_runtime import BashRuntime
from .python_runtime import PythonRuntime
from .language_runtime import ExecutionContext


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

    def execute_retainers(self) -> set[ActionKey]:
        """Execute retainer actions and return soft dependency targets to retain.

        Returns:
            Set of ActionKeys for soft dependency targets that should be retained.
        """
        pending_soft_deps = self.graph.get_pending_soft_dependencies()

        if not pending_soft_deps:
            return set()

        retained_targets: set[ActionKey] = set()

        # Group by retainer to avoid running the same retainer multiple times
        retainers_to_run: dict[ActionKey, list[Dependency]] = {}
        for dep in pending_soft_deps:
            if dep.retainer_action:
                if dep.retainer_action not in retainers_to_run:
                    retainers_to_run[dep.retainer_action] = []
                retainers_to_run[dep.retainer_action].append(dep)

        # Execute each unique retainer
        for retainer_key, soft_deps in retainers_to_run.items():
            should_retain = self._execute_retainer(retainer_key)
            if should_retain:
                # Add all targets that this retainer is responsible for
                for dep in soft_deps:
                    retained_targets.add(dep.action)

        return retained_targets

    def _execute_retainer(self, retainer_key: ActionKey) -> bool:
        """Execute a single retainer action.

        Args:
            retainer_key: Key of the retainer action to execute

        Returns:
            True if the retainer signaled to retain, False otherwise
        """
        if retainer_key not in self.graph.nodes:
            return False

        retainer_node = self.graph.nodes[retainer_key]
        action = retainer_node.action
        version = retainer_node.selected_version

        if not version:
            return False

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
                    return False

                # Check for retain signal
                return retain_signal_file.exists()

            except subprocess.TimeoutExpired:
                return False
            except Exception:
                return False

    def _build_retainer_context(self, retain_signal_file: Path) -> ExecutionContext:
        """Build minimal execution context for a retainer action."""
        import os

        # Build environment variables
        env_vars = dict(self.environment_vars)
        for var_name in self.passthrough_env_vars:
            if var_name in os.environ:
                env_vars[var_name] = os.environ[var_name]

        return ExecutionContext(
            system_vars={"project-root": str(self.project_root)},
            env_vars=env_vars,
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
