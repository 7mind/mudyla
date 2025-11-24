"""Pytest configuration and shared fixtures."""

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Return the project root directory."""
    return Path(__file__).parent.parent


@pytest.fixture(scope="session")
def mdl_command(project_root: Path) -> list[str]:
    """Return the command to run mudyla via nix."""
    return ["nix", "run", str(project_root), "--"]


@pytest.fixture
def worker_id(request):
    """Get the pytest-xdist worker ID for parallel testing.

    Returns 'master' for non-parallel runs or 'gw0', 'gw1', etc. for workers.
    """
    if hasattr(request.config, 'workerinput'):
        return request.config.workerinput['workerid']
    return 'master'


@pytest.fixture
def clean_test_output(project_root: Path):
    """Clean up test output directories before and after each test.

    Uses file locking to serialize access in parallel execution.
    """
    import fcntl

    # Use a lock file to serialize access to test directories
    lock_file = project_root / ".mdl" / "test.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    test_output = project_root / "test-output"
    mdl_runs = project_root / ".mdl" / "runs"

    # Acquire exclusive lock
    with open(lock_file, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)

        try:
            # Clean up existing directories
            if test_output.exists():
                shutil.rmtree(test_output)
            if mdl_runs.exists():
                shutil.rmtree(mdl_runs)

            # Create fresh directories
            test_output.mkdir(parents=True, exist_ok=True)
            mdl_runs.mkdir(parents=True, exist_ok=True)

            yield

            # Cleanup after test
            if test_output.exists():
                shutil.rmtree(test_output)
            if mdl_runs.exists():
                shutil.rmtree(mdl_runs)

        finally:
            # Lock is automatically released when file is closed
            pass


class MudylaRunner:
    """Helper class for running mudyla commands and capturing output."""

    def __init__(self, command: list[str], project_root: Path):
        self.command = command
        self.project_root = project_root

    def run(
        self,
        args: list[str],
        check: bool = True,
        capture_output: bool = True,
        text: bool = True,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess:
        """Run mudyla with the given arguments.

        Args:
            args: Arguments to pass to mudyla
            check: If True, raise CalledProcessError on non-zero exit
            capture_output: If True, capture stdout/stderr
            text: If True, decode output as text
            timeout: Command timeout in seconds

        Returns:
            CompletedProcess instance with stdout, stderr, returncode
        """
        full_command = self.command + args
        result = subprocess.run(
            full_command,
            cwd=self.project_root,
            check=check,
            capture_output=capture_output,
            text=text,
            timeout=timeout,
        )
        return result

    def run_success(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        """Run mudyla and assert success."""
        result = self.run(args, check=True, **kwargs)
        assert result.returncode == 0
        return result

    def run_failure(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        """Run mudyla and assert failure."""
        result = self.run(args, check=False, **kwargs)
        assert result.returncode != 0
        return result

    def assert_in_output(self, result: subprocess.CompletedProcess, text: str) -> None:
        """Assert that text appears in stdout or stderr."""
        combined = result.stdout + result.stderr
        assert text in combined, f"Expected '{text}' in output:\n{combined}"

    def assert_not_in_output(self, result: subprocess.CompletedProcess, text: str) -> None:
        """Assert that text does not appear in stdout or stderr."""
        combined = result.stdout + result.stderr
        assert text not in combined, f"Unexpected '{text}' in output:\n{combined}"

    def assert_file_exists(self, path: Path | str) -> None:
        """Assert that a file exists."""
        file_path = Path(path)
        # Support both absolute and relative paths
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path
        assert file_path.exists(), f"Expected file to exist: {file_path}"

    def assert_file_contains(self, path: Path | str, text: str) -> None:
        """Assert that a file exists and contains text."""
        file_path = Path(path)
        # Support both absolute and relative paths
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path
        self.assert_file_exists(file_path)
        content = file_path.read_text()
        assert text in content, f"Expected '{text}' in {file_path}:\n{content}"


@pytest.fixture
def mdl(mdl_command: list[str], project_root: Path) -> MudylaRunner:
    """Return a MudylaRunner instance for running mudyla commands."""
    return MudylaRunner(mdl_command, project_root)
