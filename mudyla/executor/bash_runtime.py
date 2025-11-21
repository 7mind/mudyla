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
            "env": context.env_vars,
            "args": context.args,
            "flags": context.flags,
            "actions": context.action_outputs,
        }

        # Resolve all expansions by interpolation
        for expansion in version.expansions:
            resolved_value = expansion.resolve(resolution_context)
            rendered = rendered.replace(expansion.original_text, resolved_value)

        # Build runtime header
        project_root = Path(context.system_vars["project-root"])
        runtime_path = project_root / ".mdl" / "runtime.sh"
        header = f"""#!/usr/bin/env bash
# Source Mudyla runtime
export MDL_OUTPUT_JSON="{output_json_path}"
source "{runtime_path}"

"""

        # Add environment variable exports
        env_exports = ""
        if context.env_vars:
            env_exports = "# Environment variables\n"
            for var_name, var_value in sorted(context.env_vars.items()):
                # Skip system environment variables, only export custom ones
                if var_name not in os.environ:
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

    def get_runtime_files(self) -> dict[str, str]:
        """
        Get bash runtime file (runtime.sh).
        """
        runtime_path = resources.files("mudyla").joinpath("runtime.sh")
        runtime_content = runtime_path.read_text()
        return {"runtime.sh": runtime_content}

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
