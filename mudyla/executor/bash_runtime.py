"""Bash language runtime implementation."""

import os
import platform
import shutil
from importlib import resources
from pathlib import Path

from mudyla.ast.models import ActionVersion
from mudyla.executor.language_runtime import (
    ExecutionContext,
    LanguageRuntime,
    RenderedScript,
)


class BashRuntime(LanguageRuntime):
    """Bash language runtime with interpolation-based value passing."""

    def get_language_name(self) -> str:
        """Return the name of this language."""
        return "bash"

    def prepare_script(
        self,
        version: ActionVersion,
        context: ExecutionContext,
        output_json_path: Path,
        working_dir: Path,
    ) -> RenderedScript:
        """
        Prepare a bash script for execution.

        Interpolates all ${...} expansions into the script.
        """
        rendered = version.bash_script

        # Build resolution context for expansions
        resolution_context = {
            "sys": context.system_vars,
            "axis": context.axis_values,
            "env": context.env_vars,
            "args": context.args,
            "flags": context.flags,
            "actions": context.action_outputs,
        }

        # Resolve all expansions by interpolation
        for expansion in version.expansions:
            resolved_value = expansion.resolve(resolution_context)
            rendered = rendered.replace(expansion.original_text, resolved_value)

        # Build runtime header - source runtime.sh directly from package
        runtime_resource = resources.files("mudyla").joinpath("runtime.sh")
        # Get the actual file path - resources returns a Traversable that we need to convert
        runtime_path = str(runtime_resource)
        if hasattr(runtime_resource, '__fspath__'):
            runtime_path = runtime_resource.__fspath__()

        header = f"""#!/usr/bin/env bash
# Source Mudyla runtime from package
export MDL_OUTPUT_JSON="{output_json_path}"
source "{runtime_path}"

"""

        # Add environment variable exports for MD-defined variables
        env_exports = ""
        if context.md_env_vars:
            env_exports = "# Environment variables\n"
            for var_name, var_value in sorted(context.md_env_vars.items()):
                escaped_value = var_value.replace("\\", "\\\\").replace('"', '\\"')
                env_exports += f'export {var_name}="{escaped_value}"\n'
            env_exports += "\n"

        full_script = header + env_exports + rendered

        return RenderedScript(
            content=full_script,
            working_dir=working_dir,
            environment={},  # Environment is set via exports in script
            output_json_path=output_json_path,
        )

    def get_execution_command(self, script_path: Path) -> list[str]:
        """
        Get the bash execution command.

        On Windows, tries to find Git Bash instead of WSL bash.
        """
        if platform.system() == "Windows":
            # On Windows, find Git Bash (not WSL bash)
            # Try common Git Bash locations first
            git_bash_paths = [
                r"C:\Program Files\Git\bin\bash.exe",
                r"C:\Program Files (x86)\Git\bin\bash.exe",
            ]
            bash_cmd = None
            for path in git_bash_paths:
                if Path(path).exists():
                    bash_cmd = path
                    break

            # Fall back to searching PATH (but this might find WSL bash)
            if bash_cmd is None:
                bash_cmd = shutil.which("bash.exe") or "bash.exe"

            return [bash_cmd, str(script_path)]
        else:
            return ["bash", str(script_path)]
