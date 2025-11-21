"""Language runtime abstraction for executing actions in different programming languages."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mudyla.ast.models import ActionVersion


@dataclass
class ExecutionContext:
    """Context available to actions during execution."""

    system_vars: dict[str, str]  # sys.* variables (e.g., project-root)
    env_vars: dict[str, str]  # Environment variables
    args: dict[str, Any]  # Command-line arguments
    flags: dict[str, bool]  # Command-line flags
    action_outputs: dict[str, dict[str, Any]]  # Outputs from previous actions


@dataclass
class RenderedScript:
    """A script prepared for execution."""

    content: str  # The script content
    working_dir: Path  # Working directory for execution
    environment: dict[str, str]  # Environment variables to set
    output_json_path: Path  # Path where outputs will be written


class LanguageRuntime(ABC):
    """Abstract base class for language-specific runtimes."""

    @abstractmethod
    def get_language_name(self) -> str:
        """Return the name of this language (e.g., 'bash', 'python')."""
        pass

    @abstractmethod
    def prepare_script(
        self,
        version: ActionVersion,
        context: ExecutionContext,
        output_json_path: Path,
        working_dir: Path,
    ) -> RenderedScript:
        """
        Prepare a script for execution.

        Args:
            version: The action version to execute
            context: Execution context with all available values
            output_json_path: Path where the script should write outputs
            working_dir: Directory where the script will execute

        Returns:
            RenderedScript with content and execution parameters
        """
        pass

    @abstractmethod
    def get_runtime_files(self) -> dict[str, str]:
        """
        Get runtime support files that need to be available.

        Returns:
            Dictionary mapping filename to content
        """
        pass

    @abstractmethod
    def get_execution_command(self, script_path: Path) -> list[str]:
        """
        Get the command to execute the script.

        Args:
            script_path: Path to the prepared script

        Returns:
            Command as list of strings
        """
        pass
