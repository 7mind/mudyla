"""Mudyla Python runtime - provides dep() and ret() functions and context access."""

import atexit
import json
import os
from pathlib import Path
from typing import Any


class MudilaContext:
    """Context object for accessing Mudyla values in Python scripts."""

    def __init__(self, context_data: dict[str, Any], output_collector: "_OutputCollector"):
        """
        Initialize context with data.

        Args:
            context_data: Dictionary with sys, env, args, flags, actions
            output_collector: Output collector for ret() calls
        """
        self._data = context_data
        self._output_collector = output_collector

    @property
    def sys(self) -> dict[str, str]:
        """System variables (e.g., project-root)."""
        return self._data.get("sys", {})

    @property
    def env(self) -> dict[str, str]:
        """Environment variables."""
        return self._data.get("env", {})

    @property
    def axis(self) -> dict[str, str]:
        """Axis values for the current context."""
        axis_values = self._data.get("axis")
        if axis_values is None:
            raise KeyError("Axis values are not available in this context")
        return axis_values

    def axis_value(self, axis_name: str) -> str:
        """Get a specific axis value, failing fast if missing."""
        axis_values = self.axis
        if axis_name not in axis_values:
            raise KeyError(f"Axis '{axis_name}' not found in context")
        return axis_values[axis_name]

    @property
    def args(self) -> dict[str, Any]:
        """Command-line arguments."""
        return self._data.get("args", {})

    @property
    def flags(self) -> dict[str, bool]:
        """Command-line flags."""
        return self._data.get("flags", {})

    @property
    def actions(self) -> dict[str, dict[str, Any]]:
        """Outputs from previous actions."""
        return self._data.get("actions", {})

    def is_retained(self, action_name: str) -> bool:
        """
        Check if an action was retained (its output is available).
        
        Useful for checking weak/soft dependencies.
        
        Args:
            action_name: Name of the action to check
            
        Returns:
            True if the action was retained and executed, False otherwise
        """
        return action_name in self.actions

    def dep(self, dependency: str) -> None:
        """
        Declare a dependency (no-op at runtime, used for parsing).

        Args:
            dependency: Dependency string (e.g., "action.build" or "env.PATH")

        Example:
            mdl.dep("action.build")
            mdl.dep("env.HOME")
        """
        # Dependencies are extracted at parse time, this is a no-op at runtime
        pass

    def weak(self, dependency: str) -> None:
        """
        Declare a weak dependency (no-op at runtime, used for parsing).

        Args:
            dependency: Dependency string (e.g., "action.optional")

        Example:
            mdl.weak("action.optional-feature")
        """
        # Weak dependencies are extracted at parse time, this is a no-op at runtime
        pass

    def soft(self, dependency: str, retainer: str) -> None:
        """
        Declare a soft dependency (no-op at runtime, used for parsing).

        Args:
            dependency: Dependency string (e.g., "action.feature")
            retainer: Retainer action string (e.g., "action.check-feature")

        Example:
            mdl.soft("action.optional-feature", "action.check-optional")
        """
        # Soft dependencies are extracted at parse time, this is a no-op at runtime
        pass

    def use(self, resource: str) -> None:
        """
        Declare usage of an argument (no-op at runtime, used for parsing).

        This is used to bind the action to a context that includes the specified
        argument. Without this declaration, Python actions don't get context-bound
        to args since they don't use ${args.X} expansions like bash scripts.

        Args:
            resource: Resource string (e.g., "args.name")

        Example:
            mdl.use("args.items")
            mdl.use("args.count")
        """
        # Use declarations are extracted at parse time, this is a no-op at runtime
        pass

    def retain(self, *actions: str) -> None:
        """
        Signal that a soft dependency should be retained.

        Call this in a retainer action to indicate which soft dependencies
        should be included in the execution graph.

        Args:
            *actions: Optional action names to retain. If not provided, retains all
                     soft dependencies using this retainer. Action names can be
                     specified with or without the "action." prefix.

        Examples:
            mdl.retain()                    # Retain all soft deps using this retainer
            mdl.retain("action.foo")        # Retain only where foo depends on target
            mdl.retain("foo", "bar")        # Retain multiple specific soft deps
        """
        retain_signal_file = os.environ.get("MDL_RETAIN_SIGNAL_FILE")
        if retain_signal_file:
            path = Path(retain_signal_file)
            if not actions:
                # No arguments: retain all (create empty file)
                path.touch()
            else:
                # Specific actions: write each to the file
                with open(path, "a", encoding="utf-8") as f:
                    for action in actions:
                        # Strip "action." prefix if present
                        name = action[7:] if action.startswith("action.") else action
                        f.write(f"{name}\n")

    def ret(self, name: str, value: Any, type_str: str) -> None:
        """
        Return a value from the action.

        Args:
            name: Name of the output variable
            value: Value to return
            type_str: Type of the value ("int", "string", "bool", "file", "directory")

        Example:
            mdl.ret("success", True, "bool")
            mdl.ret("output-file", "/path/to/file", "file")
        """
        # Validate type
        valid_types = {"int", "string", "bool", "file", "directory"}
        if type_str not in valid_types:
            raise ValueError(f"Invalid return type: {type_str}. Must be one of {valid_types}")

        # Convert value to appropriate type
        if type_str == "int":
            value = int(value)
        elif type_str == "string":
            value = str(value)
        elif type_str == "bool":
            value = bool(value)
        elif type_str in ("file", "directory"):
            value = str(value)

        self._output_collector.add_output(name, type_str, value)


class _OutputCollector:
    """Internal class for collecting outputs."""

    def __init__(self):
        self.outputs: dict[str, dict[str, Any]] = {}
        self.output_path: str | None = None

    def add_output(self, name: str, type_str: str, value: Any):
        """Add an output value."""
        self.outputs[name] = {"type": type_str, "value": value}

    def write_outputs(self):
        """Write outputs to JSON file on exit."""
        if self.output_path:
            output_file = Path(self.output_path)
            with output_file.open("w", encoding="utf-8") as f:
                json.dump(self.outputs, f, indent=2)


# Global collector instance
_collector = _OutputCollector()


def _initialize_runtime(context_json_path: str, output_json_path: str):
    """
    Initialize the runtime (called automatically).

    Args:
        context_json_path: Path to context JSON file
        output_json_path: Path where outputs should be written
    """
    # Load context
    with open(context_json_path, encoding="utf-8") as f:
        context_data = json.load(f)

    # Set output path
    _collector.output_path = output_json_path

    # Register output writer to run on exit
    atexit.register(_collector.write_outputs)

    # Create context object and make it globally available
    global mdl
    mdl = MudilaContext(context_data, _collector)


# Global context object (initialized by _initialize_runtime)
mdl: MudilaContext = None  # type: ignore
