"""Executor for retainer actions that decide soft dependency retention."""

import os
import codecs
import io
import queue
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Protocol

from ..ast.models import ParsedDocument
from ..dag.graph import ActionGraph, ActionKey, Dependency
from .runtime_registry import RuntimeRegistry
from .runtime_bash import BashRuntime
from .runtime_python import PythonRuntime
from .language_runtime import ExecutionContext, LanguageRuntime
from .process import ProcessCleanupFailure, ProcessFactory, StdinMode

RETAINER_TIMEOUT_SECONDS = 60
RETAINER_CHUNK_BYTES = 4096
RETAINER_CLEANUP_SECONDS = 1.0
RETAINER_POLL_SECONDS = .05


class RetainerOutcome(Enum):
    SUCCEEDED = "succeeded"
    NONZERO = "nonzero"
    TIMED_OUT = "timed_out"
    MISSING_RETAINER = "missing_retainer"
    MISSING_VERSION = "missing_version"
    ERROR = "error"


@dataclass
class RetainerExecutionResult:
    """Internal result from executing a retainer."""

    retained_actions: set[str] | None  # None = don't retain, empty = all, non-empty = selective
    stdout: str
    stderr: str
    outcome: RetainerOutcome


@dataclass
class RetainerResult:
    """Result of executing a single retainer action."""

    retainer_key: ActionKey
    soft_dep_targets: list[ActionKey]
    retained: bool
    execution_time_ms: float
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class RetainerRequest:
    key: ActionKey
    targets: tuple[ActionKey, ...]
    started_perf_counter: float


@dataclass(frozen=True)
class RetainerDecision:
    target: ActionKey
    retained: bool


@dataclass(frozen=True)
class RetainerCompletion:
    request: RetainerRequest
    result: RetainerResult
    outcome: RetainerOutcome
    decisions: tuple[RetainerDecision, ...]


class RetainerObserver(Protocol):
    def begin_retainer(self, request: RetainerRequest) -> None: ...

    def retainer_output(self, key: ActionKey, text: str, stream: Literal["stdout", "stderr"]) -> None: ...

    def end_retainer(self, completion: RetainerCompletion) -> None: ...


@dataclass(frozen=True)
class _RetainerChunk:
    stream: Literal["stdout", "stderr"]
    data: bytes


@dataclass(frozen=True)
class _RetainerEOF:
    stream: Literal["stdout", "stderr"]


@dataclass(frozen=True)
class _RetainerReadFailure:
    error: Exception


class _ObserverFailure(Exception):
    def __init__(self, original: Exception) -> None:
        self.original = original
        super().__init__(str(original))


class RetainerCleanupFailure(RuntimeError):
    pass


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
        observer: RetainerObserver,
        processes: ProcessFactory,
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
        self.observer = observer
        self.processes = processes
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
            request = RetainerRequest(retainer_key, tuple(dep.action for dep in soft_deps), start_time)
            self.observer.begin_retainer(request)
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

            result = RetainerResult(
                retainer_key=retainer_key,
                soft_dep_targets=actually_retained if exec_result.retained_actions is not None else [],
                retained=exec_result.retained_actions is not None,
                execution_time_ms=elapsed_ms,
                stdout=exec_result.stdout,
                stderr=exec_result.stderr,
            )
            retainer_results.append(result)
            self.observer.end_retainer(RetainerCompletion(request, result, exec_result.outcome,
                tuple(RetainerDecision(target, target in actually_retained) for target in request.targets)))

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
            return RetainerExecutionResult(None, "", "", RetainerOutcome.MISSING_RETAINER)

        retainer_node = self.graph.nodes[retainer_key]
        version = retainer_node.selected_version

        if not version:
            return RetainerExecutionResult(None, "", "", RetainerOutcome.MISSING_VERSION)

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
                result = self._capture_retainer(retainer_key, exec_cmd, env)

                stdout = result.stdout or ""
                stderr = result.stderr or ""

                # Check if retainer succeeded
                if result.returncode != 0:
                    return RetainerExecutionResult(None, stdout, stderr, RetainerOutcome.NONZERO)

                # Check for retain signal
                if not retain_signal_file.exists():
                    return RetainerExecutionResult(None, stdout, stderr, RetainerOutcome.SUCCEEDED)

                # Read signal file to determine what to retain
                content = retain_signal_file.read_text(encoding="utf-8").strip()
                if not content:
                    # Empty file = retain all
                    return RetainerExecutionResult(set(), stdout, stderr, RetainerOutcome.SUCCEEDED)
                else:
                    # File contains specific action names (one per line)
                    actions = set(line.strip() for line in content.split("\n") if line.strip())
                    return RetainerExecutionResult(actions, stdout, stderr, RetainerOutcome.SUCCEEDED)

            except subprocess.TimeoutExpired:
                return RetainerExecutionResult(None, "", "Timeout expired", RetainerOutcome.TIMED_OUT)
            except _ObserverFailure as error:
                raise error.original
            except (RetainerCleanupFailure, ProcessCleanupFailure):
                raise
            except Exception as e:
                return RetainerExecutionResult(None, "", str(e), RetainerOutcome.ERROR)

    def _capture_retainer(self, key: ActionKey, command: list[str], environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
        events: queue.Queue[_RetainerChunk | _RetainerEOF | _RetainerReadFailure] = queue.Queue()

        def read_stream(pipe: io.BufferedReader, stream: Literal["stdout", "stderr"]) -> None:
            try:
                while chunk := pipe.read1(RETAINER_CHUNK_BYTES):
                    events.put(_RetainerChunk(stream, chunk))
            except Exception as error:
                events.put(_RetainerReadFailure(error))
            finally:
                events.put(_RetainerEOF(stream))

        process = self.processes.start(command, cwd=self.project_root, environment=environment,
                                       stdin_mode=StdinMode.INHERIT)
        readers = [threading.Thread(target=read_stream, args=(pipe, stream),
                                    name=f"retainer-{process.pid}-{stream}", daemon=True)
                   for pipe, stream in ((process.stdout, "stdout"), (process.stderr, "stderr"))]
        buffers: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
        decoders = {stream: codecs.getincrementaldecoder("utf-8")(errors="strict") for stream in buffers}
        invalid_streams: set[str] = set()
        ended: set[str] = set()
        deadline = time.monotonic() + RETAINER_TIMEOUT_SECONDS
        completed = False
        try:
            for reader in readers:
                reader.start()
            while len(ended) < len(readers):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, RETAINER_TIMEOUT_SECONDS)
                try:
                    event = events.get(timeout=min(RETAINER_POLL_SECONDS, remaining))
                except queue.Empty:
                    continue
                if isinstance(event, _RetainerReadFailure):
                    raise event.error
                if isinstance(event, _RetainerEOF):
                    ended.add(event.stream)
                    data, final = b"", True
                else:
                    buffers[event.stream].append(event.data)
                    data, final = event.data, False
                if event.stream in invalid_streams:
                    continue
                try:
                    text = decoders[event.stream].decode(data, final=final)
                except UnicodeDecodeError:
                    invalid_streams.add(event.stream)
                    continue
                if not text:
                    continue
                try:
                    self.observer.retainer_output(key, text, event.stream)
                except Exception as error:
                    raise _ObserverFailure(error) from error
            returncode = process.wait(max(0, deadline - time.monotonic()))
            completed = True
            stdout = b"".join(buffers["stdout"]).decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            stderr = b"".join(buffers["stderr"]).decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            return subprocess.CompletedProcess(command, returncode, stdout, stderr)
        finally:
            if not completed:
                process.terminate_tree()
                process.wait(RETAINER_CLEANUP_SECONDS)
            for reader in readers:
                if reader.ident is not None:
                    reader.join(timeout=RETAINER_CLEANUP_SECONDS)
            if any(reader.is_alive() for reader in readers):
                raise RetainerCleanupFailure(f"Retainer {process.pid} output readers survived cleanup")
            process.close()

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
        for name, argument_value in context_id.args:
            args[name] = argument_value

        flags = dict(self.flags)
        for name, flag_value in context_id.flags:
            flags[name] = flag_value

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
            return runtime.get_direct_execution_command(script_path)

        # Wrap with nix if available
        flake_path = self.project_root / "flake.nix"
        if flake_path.exists():
            return [
                "nix",
                "develop",
                str(self.project_root),
                "-c",
            ] + base_cmd

        return runtime.get_direct_execution_command(script_path)

    def _build_environment(self, retain_signal_file: Path) -> dict[str, str]:
        """Build environment variables for retainer execution."""
        env = dict(os.environ)
        env["MDL_RETAIN_SIGNAL_FILE"] = str(retain_signal_file)
        return env
