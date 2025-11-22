"""DAG builder for constructing action dependency graphs."""

import platform

from ..ast.expansions import ActionExpansion
from ..ast.models import ParsedDocument
from .graph import ActionGraph, ActionNode, ActionKey


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
        nodes: dict[ActionKey, ActionNode] = {}

        for action_name, action in self.document.actions.items():
            # Select appropriate version
            try:
                selected_version = action.get_version(axis_values, current_platform)
            except ValueError as e:
                # If this action is not needed, we can skip the error for now
                # The validator will check if it's actually required
                selected_version = None

            # Extract dependencies
            dependencies: set[ActionKey] = set()
            if selected_version:
                # Implicit dependencies from ${action.*} expansions
                for expansion in selected_version.expansions:
                    if isinstance(expansion, ActionExpansion):
                        dep_name = expansion.get_dependency_action()
                        dependencies.add(ActionKey.from_name(dep_name))

                # Explicit dependencies from dep declarations
                for dep_decl in selected_version.dependency_declarations:
                    dependencies.add(ActionKey.from_name(dep_decl.action_name))

            node = ActionNode(
                action=action,
                selected_version=selected_version,
                dependencies=dependencies,
            )
            action_key = ActionKey.from_name(action_name)
            nodes[action_key] = node

        # Build reverse edges (dependents)
        for action_key, node in nodes.items():
            for dep_key in node.dependencies:
                if dep_key in nodes:
                    nodes[dep_key].dependents.add(action_key)

        goal_keys = {ActionKey.from_name(goal) for goal in goals}
        return ActionGraph(nodes=nodes, goals=goal_keys)

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
