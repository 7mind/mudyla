"""Execution engine for running actions."""

import json
import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import importlib.resources

from ..ast.types import ReturnType
from ..dag.graph import ActionGraph
from ..utils.colors import ColorFormatter
from ..utils.output import OutputFormatter


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

        # Create color formatter and output formatter
        self.color = ColorFormatter(no_color=no_color)
        self.output = OutputFormatter(self.color)

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

        # Copy runtime.sh to .mdl directory
        self._install_runtime()

    def _install_runtime(self) -> None:
        """Install runtime files for all supported languages to .mdl directory."""
        from mudyla.executor.bash_runtime import BashRuntime
        from mudyla.executor.python_runtime import PythonRuntime

        mdl_dir = self.project_root / ".mdl"
        mdl_dir.mkdir(parents=True, exist_ok=True)

        # Install runtime files for each language
        for runtime_class in [BashRuntime, PythonRuntime]:
            runtime = runtime_class()
            for filename, content in runtime.get_runtime_files().items():
                dest_path = mdl_dir / filename
                dest_path.write_text(content)
                if filename.endswith(".sh"):
                    dest_path.chmod(0o755)

    def _get_language_runtime(self, language: str):
        """Get the language runtime for the specified language.

        Args:
            language: Language name (e.g., "bash", "python")

        Returns:
            LanguageRuntime instance

        Raises:
            ValueError: If language is not supported
        """
        from mudyla.executor.bash_runtime import BashRuntime
        from mudyla.executor.python_runtime import PythonRuntime

        runtimes = {
            "bash": BashRuntime,
            "python": PythonRuntime,
        }

        if language not in runtimes:
            raise ValueError(
                f"Unsupported language: {language}. Supported languages: {', '.join(runtimes.keys())}"
            )

        return runtimes[language]()

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
                error_msg = f"Action '{action_name}' failed!"
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

                return ExecutionResult(
                    success=False,
                    action_results=action_results,
                    run_directory=self.run_directory,
                )

            # Store outputs for dependent actions
            action_outputs[action_name] = result.outputs

        # Success - clean up run directory if not keeping it
        if not self.keep_run_dir:
            try:
                shutil.rmtree(self.run_directory)
            except Exception as e:
                # Don't fail on cleanup errors, just warn
                emoji = "!" if self.no_color else "⚠️"
                print(f"{emoji} {self.color.warning('Warning:')} Failed to clean up run directory: {e}")

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

        self.output.print(f"{self.output.emoji('♻️', '✓')} {self.color.info('Executing action:')} {self.color.highlight(action_name)} {self.color.dim('(restored from previous run)')}")

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

        # Get appropriate language runtime
        runtime = self._get_language_runtime(version.language)

        # Prepare execution context
        from mudyla.executor.language_runtime import ExecutionContext

        context = ExecutionContext(
            system_vars={"project-root": str(self.project_root)},
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
        else:
            self.output.print(f"{self.output.emoji('⚡', '▸')} {self.color.info('Executing action:')} {self.color.highlight(action_name)}")

        # Print command in verbose/CI modes
        if self.github_actions or self.verbose:
            print(f"{self.color.dim('Command:')} {' '.join(exec_cmd)}")

        # Record start time
        start_time = datetime.now()
        start_time_iso = start_time.isoformat()

        try:
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

                    def stream_output(pipe, console_stream, file_stream):
                        """Stream output from pipe to both console and file."""
                        if pipe:
                            for line in pipe:
                                console_stream.write(line)
                                console_stream.flush()
                                file_stream.write(line)

                    # Start threads to read stdout and stderr simultaneously
                    stdout_thread = threading.Thread(
                        target=stream_output,
                        args=(process.stdout, sys.stdout, stdout_file),
                    )
                    stderr_thread = threading.Thread(
                        target=stream_output,
                        args=(process.stderr, sys.stderr, stderr_file),
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

            # Record end time
            end_time = datetime.now()
            end_time_iso = end_time.isoformat()
            duration = (end_time - start_time).total_seconds()

            if returncode != 0:
                # Write failure meta.json
                meta = {
                    "action_name": action_name,
                    "success": False,
                    "start_time": start_time_iso,
                    "end_time": end_time_iso,
                    "duration_seconds": duration,
                    "exit_code": returncode,
                    "error_message": f"Script exited with code {returncode}",
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
                    exit_code=returncode,
                    error_message=f"Script exited with code {returncode}",
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
