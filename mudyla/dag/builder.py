"""DAG builder for constructing action dependency graphs.

DEPRECATED: This builder is kept for backward compatibility with single-context
execution. For multi-context support, use DAGCompiler instead.
"""

import platform

from ..ast.expansions import ActionExpansion, WeakActionExpansion
from ..ast.models import ParsedDocument
from .context import ContextId
from .graph import ActionGraph, ActionNode, ActionKey, Dependency


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
        """Build dependency graph for the given goals (single-context mode).

        DEPRECATED: Use DAGCompiler for multi-context support.

        Args:
            goals: List of goal action names
            axis_values: Current axis values

        Returns:
            Action graph with single context

        Raises:
            ValueError: If goals or dependencies are invalid
        """
        # Validate goals exist
        for goal in goals:
            if goal not in self.document.actions:
                raise ValueError(f"Goal action '{goal}' not found")

        # Get current platform
        current_platform = get_normalized_platform()

        # Create a single context from axis values
        context_id = ContextId.from_dict(axis_values)

        # Create nodes for all actions
        nodes: dict[ActionKey, ActionNode] = {}

        for action_name, action in self.document.actions.items():
            # Create action key with context
            action_key = ActionKey.from_name(action_name, context_id)

            # Select appropriate version
            try:
                selected_version = action.get_version(axis_values, current_platform)
            except ValueError as e:
                # If this action is not needed, we can skip the error for now
                # The validator will check if it's actually required
                selected_version = None

            # Extract dependencies
            dependencies: set[Dependency] = set()
            if selected_version:
                # Implicit dependencies from ${action.*} and ${action.weak.*} expansions
                for expansion in selected_version.expansions:
                    if isinstance(expansion, (ActionExpansion, WeakActionExpansion)):
                        dep_name = expansion.get_dependency_action()
                        # Dependencies in same context
                        dep_key = ActionKey.from_name(dep_name, context_id)
                        is_weak = isinstance(expansion, WeakActionExpansion)
                        dependencies.add(Dependency(action=dep_key, weak=is_weak))

                # Explicit dependencies from dep/weak declarations
                for dep_decl in selected_version.dependency_declarations:
                    # Dependencies in same context
                    dep_key = ActionKey.from_name(dep_decl.action_name, context_id)
                    dependencies.add(Dependency(action=dep_key, weak=dep_decl.weak))

            node = ActionNode(
                key=action_key,
                action=action,
                selected_version=selected_version,
                dependencies=dependencies,
            )
            nodes[action_key] = node

        # Build reverse edges (dependents)
        for action_key, node in nodes.items():
            for dep in node.dependencies:
                if dep.action in nodes:
                    # Create reverse dependency with same weak flag
                    nodes[dep.action].dependents.add(
                        Dependency(action=action_key, weak=dep.weak)
                    )

        goal_keys = {ActionKey.from_name(goal, context_id) for goal in goals}
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
