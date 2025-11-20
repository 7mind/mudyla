"""DAG builder for constructing action dependency graphs."""

import platform

from ..ast.expansions import ActionExpansion
from ..ast.models import ParsedDocument
from .graph import ActionGraph, ActionNode


def get_normalized_platform() -> str:
    """Get the normalized platform name.

    Returns:
        Platform name: windows, linux, or macos
    """
    system = platform.system()
    if system == "Windows":
        return "windows"
    elif system == "Darwin":
        return "macos"
    elif system == "Linux":
        return "linux"
    else:
        return system.lower()


class DAGBuilder:
    """Builds dependency graph from parsed document."""

    def __init__(self, document: ParsedDocument):
        self.document = document

    def build_graph(
        self, goals: list[str], axis_values: dict[str, str]
    ) -> ActionGraph:
        """Build dependency graph for the given goals.

        Args:
            goals: List of goal action names
            axis_values: Current axis values

        Returns:
            Action graph

        Raises:
            ValueError: If goals or dependencies are invalid
        """
        # Validate goals exist
        for goal in goals:
            if goal not in self.document.actions:
                raise ValueError(f"Goal action '{goal}' not found")

        # Get current platform
        current_platform = get_normalized_platform()

        # Create nodes for all actions
        nodes: dict[str, ActionNode] = {}

        for action_name, action in self.document.actions.items():
            # Select appropriate version
            try:
                selected_version = action.get_version(axis_values, current_platform)
            except ValueError as e:
                # If this action is not needed, we can skip the error for now
                # The validator will check if it's actually required
                selected_version = None

            # Extract dependencies
            dependencies = set()
            if selected_version:
                # Implicit dependencies from ${action.*} expansions
                for expansion in selected_version.expansions:
                    if isinstance(expansion, ActionExpansion):
                        dependencies.add(expansion.get_dependency_action())

                # Explicit dependencies from dep declarations
                for dep_decl in selected_version.dependency_declarations:
                    dependencies.add(dep_decl.action_name)

            node = ActionNode(
                action=action,
                selected_version=selected_version,
                dependencies=dependencies,
            )
            nodes[action_name] = node

        # Build reverse edges (dependents)
        for action_name, node in nodes.items():
            for dep_name in node.dependencies:
                if dep_name in nodes:
                    nodes[dep_name].dependents.add(action_name)

        return ActionGraph(nodes=nodes, goals=set(goals))

    def validate_goals(self, goals: list[str]) -> None:
        """Validate that all goals exist.

        Args:
            goals: List of goal action names

        Raises:
            ValueError: If any goal is invalid
        """
        for goal in goals:
            if goal not in self.document.actions:
                available = ", ".join(sorted(self.document.actions.keys()))
                raise ValueError(
                    f"Goal action '{goal}' not found. Available actions: {available}"
                )
