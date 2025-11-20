"""Execution engine for running actions."""

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..ast.types import ReturnType
from ..dag.graph import ActionGraph


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

    RET_FUNCTION_TEMPLATE = """
# Mudyla ret function
MDL_OUTPUT_JSON="{output_json}"
MDL_OUTPUT_LINES=()

ret() {{
    local declaration="$1"
    local name="${{declaration%%:*}}"
    local rest="${{declaration#*:}}"
    local type="${{rest%%=*}}"
    local value="${{rest#*=}}"

    # Store as JSON line
    MDL_OUTPUT_LINES+=("$(printf '%s' "$name:$type:$value")")
}}

# Trap to write JSON on exit
trap 'mudyla_write_outputs' EXIT

mudyla_write_outputs() {{
    echo "{{" > "$MDL_OUTPUT_JSON"
    local first=true
    for line in "${{MDL_OUTPUT_LINES[@]}}"; do
        local name="${{line%%:*}}"
        local rest="${{line#*:}}"
        local type="${{rest%%:*}}"
        local value="${{rest#*:}}"

        if [ "$first" = true ]; then
            first=false
        else
            echo "," >> "$MDL_OUTPUT_JSON"
        fi

        # Escape value for JSON
        local json_value=$(printf '%s' "$value" | python3 -c 'import sys, json; print(json.dumps(sys.stdin.read().strip()))')
        printf '  "%s": {{"type": "%s", "value": %s}}' "$name" "$type" "$json_value" >> "$MDL_OUTPUT_JSON"
    done
    echo "" >> "$MDL_OUTPUT_JSON"
    echo "}}" >> "$MDL_OUTPUT_JSON"
}}

set -euo pipefail

"""

    def __init__(
        self,
        graph: ActionGraph,
        project_root: Path,
        args: dict[str, str],
        flags: dict[str, bool],
        axis_values: dict[str, str],
        passthrough_env_vars: list[str],
        run_directory: Optional[Path] = None,
        previous_run_directory: Optional[Path] = None,
    ):
        self.graph = graph
        self.project_root = project_root
        self.args = args
        self.flags = flags
        self.axis_values = axis_values
        self.passthrough_env_vars = passthrough_env_vars
        self.previous_run_directory = previous_run_directory

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

    def execute_all(self) -> ExecutionResult:
        """Execute all actions in the graph.

        Returns:
            Execution result
        """
        # Get execution order
        try:
            execution_order = self.graph.get_execution_order()
        except ValueError as e:
            return ExecutionResult(
                success=False,
                action_results={},
                run_directory=self.run_directory,
            )

        # Execute actions in order
        action_outputs: dict[str, dict[str, Any]] = {}
        action_results: dict[str, ActionResult] = {}

        for action_name in execution_order:
            node = self.graph.get_node(action_name)

            # Execute action
            result = self._execute_action(node.action.name, action_outputs)
            action_results[action_name] = result

            if not result.success:
                # Action failed - stop execution
                print(f"\nAction '{action_name}' failed!")
                print(f"Run directory: {self.run_directory}")
                print(f"\nStdout: {result.stdout_path}")
                if result.stdout_path.exists():
                    print(result.stdout_path.read_text())
                print(f"\nStderr: {result.stderr_path}")
                if result.stderr_path.exists():
                    print(result.stderr_path.read_text())
                if result.error_message:
                    print(f"\nError: {result.error_message}")

                return ExecutionResult(
                    success=False,
                    action_results=action_results,
                    run_directory=self.run_directory,
                )

            # Store outputs for dependent actions
            action_outputs[action_name] = result.outputs

        # Success - clean up run directory if desired (for now keep it)
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
    ) -> None:
        """Write meta.json for an action."""
        meta = {
            "action_name": action_name,
            "success": success,
            "start_time": start_time,
            "end_time": end_time,
            "duration_seconds": duration,
            "exit_code": exit_code,
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

        print(f"  (restored from previous run)")

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

        # Render script
        rendered_script = self._render_script(
            version.bash_script, action_name, action_outputs
        )

        # Add ret function
        output_json_path = action_dir / "output.json"
        ret_function = self.RET_FUNCTION_TEMPLATE.format(
            output_json=str(output_json_path)
        )
        full_script = ret_function + "\n" + rendered_script

        # Save script
        script_path = action_dir / "script.sh"
        script_path.write_text(full_script)
        script_path.chmod(0o755)

        # Prepare output paths
        stdout_path = action_dir / "stdout.log"
        stderr_path = action_dir / "stderr.log"

        # Build nix develop command
        # Note: We don't use --ignore-env because we want to preserve the environment
        # The passthrough_env_vars and required_env_vars are validated but not explicitly kept
        # since nix develop inherits the environment by default
        nix_cmd = ["nix", "develop", "--command", "bash", str(script_path)]

        # Execute
        print(f"Executing action: {action_name}")

        # Record start time
        start_time = datetime.now()
        start_time_iso = start_time.isoformat()

        try:
            with open(stdout_path, "w") as stdout_file, open(
                stderr_path, "w"
            ) as stderr_file:
                result = subprocess.run(
                    nix_cmd,
                    cwd=str(self.project_root),
                    stdout=stdout_file,
                    stderr=stderr_file,
                    env=os.environ.copy(),
                )

            # Record end time
            end_time = datetime.now()
            end_time_iso = end_time.isoformat()
            duration = (end_time - start_time).total_seconds()

            if result.returncode != 0:
                # Write failure meta.json
                meta = {
                    "action_name": action_name,
                    "success": False,
                    "start_time": start_time_iso,
                    "end_time": end_time_iso,
                    "duration_seconds": duration,
                    "exit_code": result.returncode,
                    "error_message": f"Script exited with code {result.returncode}",
                }
                (action_dir / "meta.json").write_text(json.dumps(meta, indent=2))

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
                    exit_code=result.returncode,
                    error_message=f"Script exited with code {result.returncode}",
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
                    exit_code=result.returncode,
                    error_message="No output.json generated",
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
                    exit_code=result.returncode,
                    error_message="No output.json generated",
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
                        )

                    path = Path(output_value)
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
            )

        except Exception as e:
            # Record end time for exception case
            end_time = datetime.now()
            end_time_iso = end_time.isoformat()
            duration = (end_time - start_time).total_seconds()

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
            )

    def _render_script(
        self, script: str, action_name: str, action_outputs: dict[str, dict[str, Any]]
    ) -> str:
        """Render script with all expansions resolved.

        Args:
            script: Original script
            action_name: Name of the action being rendered
            action_outputs: Outputs from previous actions

        Returns:
            Rendered script
        """
        node = self.graph.get_node(action_name)
        version = node.selected_version

        if version is None:
            return script

        rendered = script

        # Build resolution context
        context = {
            "sys": {"project-root": str(self.project_root)},
            "env": dict(os.environ),
            "args": self.args,
            "flags": self.flags,
            "actions": action_outputs,
        }

        # Resolve all expansions
        for expansion in version.expansions:
            try:
                resolved_value = expansion.resolve(context)
                rendered = rendered.replace(expansion.original_text, resolved_value)
            except ValueError as e:
                raise ValueError(
                    f"Error resolving expansion in action '{action_name}': {e}"
                )

        return rendered

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
