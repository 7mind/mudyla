"""Python language runtime implementation."""

import json
from importlib import resources
from pathlib import Path

from mudyla.ast.models import ActionVersion
from mudyla.executor.language_runtime import (
    ExecutionContext,
    LanguageRuntime,
    RenderedScript,
)


class PythonRuntime(LanguageRuntime):
    """Python language runtime with object-based value passing."""

    def get_language_name(self) -> str:
        """Return the name of this language."""
        return "python"

    def prepare_script(
        self,
        version: ActionVersion,
        context: ExecutionContext,
        output_json_path: Path,
        working_dir: Path,
    ) -> RenderedScript:
        """
        Prepare a Python script for execution.

        Instead of interpolating values, we:
        1. Write context to a JSON file
        2. Add runtime initialization code to the script
        3. Make context available via the 'mdl' object
        """
        # Write context to JSON file
        context_json_path = working_dir / "context.json"
        context_data = {
            "sys": context.system_vars,
            "axis": context.axis_values,
            "env": context.env_vars,
            "args": context.args,
            "flags": context.flags,
            "actions": context.action_outputs,
        }
        context_json_path.write_text(json.dumps(context_data, indent=2))

        # Build initialization code
        # Import runtime directly from mudyla package
        init_code = f'''#!/usr/bin/env python3

# Initialize Mudyla runtime from package
from mudyla import runtime as _mdl_runtime
_mdl_runtime._initialize_runtime({str(context_json_path)!r}, {str(output_json_path)!r})

# Import mdl context object
from mudyla.runtime import mdl

'''

        # Append user script
        full_script = init_code + version.bash_script

        return RenderedScript(
            content=full_script,
            working_dir=working_dir,
            environment=context.env_vars,
            output_json_path=output_json_path,
        )

    def get_execution_command(self, script_path: Path) -> list[str]:
        """
        Get the Python execution command.
        """
        return ["python3", str(script_path)]
