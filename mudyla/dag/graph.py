"""Graph data structures for action dependencies."""

from dataclasses import dataclass, field
from typing import Optional

from ..ast.models import ActionDefinition, ActionVersion


@dataclass
class ActionNode:
    """Node in the action dependency graph."""

    action: ActionDefinition
    """The action definition"""

    selected_version: Optional[ActionVersion] = None
    """The selected version based on axis values"""

    dependencies: set[str] = field(default_factory=set)
    """Names of actions this node depends on"""

    dependents: set[str] = field(default_factory=set)
    """Names of actions that depend on this node"""

    def __hash__(self) -> int:
        return hash(self.action.name)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ActionNode):
            return False
        return self.action.name == other.action.name


@dataclass
class ActionGraph:
    """Dependency graph of actions."""

    nodes: dict[str, ActionNode]
    """All nodes in the graph indexed by action name"""

    goals: set[str]
    """Goal action names (roots of the graph)"""

    def get_node(self, action_name: str) -> ActionNode:
        """Get node by action name.

        Args:
            action_name: Action name

        Returns:
            Action node

        Raises:
            KeyError: If action not found
        """
        if action_name not in self.nodes:
            raise KeyError(f"Action '{action_name}' not found in graph")
        return self.nodes[action_name]

    def get_all_dependencies(self, action_name: str) -> set[str]:
        """Get all transitive dependencies of an action.

        Args:
            action_name: Action name

        Returns:
            Set of all dependency action names
        """
        visited = set()
        self._collect_dependencies(action_name, visited)
        visited.discard(action_name)  # Don't include the action itself
        return visited

    def _collect_dependencies(self, action_name: str, visited: set[str]) -> None:
        """Recursively collect dependencies."""
        if action_name in visited:
            return

        visited.add(action_name)
        node = self.get_node(action_name)

        for dep_name in node.dependencies:
            self._collect_dependencies(dep_name, visited)

    def topological_sort(self) -> list[str]:
        """Get topological sort of the graph.

        Returns:
            List of action names in execution order

        Raises:
            ValueError: If graph contains cycles
        """
        # Kahn's algorithm
        in_degree = {name: 0 for name in self.nodes}

        for node in self.nodes.values():
            for dep_name in node.dependencies:
                in_degree[node.action.name] += 1

        queue = [name for name, degree in in_degree.items() if degree == 0]
        result = []

        while queue:
            # Sort to make order deterministic
            queue.sort()
            action_name = queue.pop(0)
            result.append(action_name)

            node = self.nodes[action_name]
            for dependent_name in node.dependents:
                in_degree[dependent_name] -= 1
                if in_degree[dependent_name] == 0:
                    queue.append(dependent_name)

        if len(result) != len(self.nodes):
            # Graph has a cycle
            remaining = set(self.nodes.keys()) - set(result)
            raise ValueError(
                f"Dependency graph contains cycles. Actions involved: {', '.join(sorted(remaining))}"
            )

        return result

    def find_cycle(self) -> Optional[list[str]]:
        """Find a cycle in the graph if one exists.

        Returns:
            List of action names forming a cycle, or None if no cycle
        """
        visited = set()
        rec_stack = set()
        path = []

        def dfs(action_name: str) -> Optional[list[str]]:
            visited.add(action_name)
            rec_stack.add(action_name)
            path.append(action_name)

            node = self.nodes[action_name]
            for dep_name in node.dependencies:
                if dep_name not in visited:
                    cycle = dfs(dep_name)
                    if cycle:
                        return cycle
                elif dep_name in rec_stack:
                    # Found a cycle
                    cycle_start = path.index(dep_name)
                    return path[cycle_start:] + [dep_name]

            path.pop()
            rec_stack.remove(action_name)
            return None

        for action_name in self.nodes:
            if action_name not in visited:
                cycle = dfs(action_name)
                if cycle:
                    return cycle

        return None

    def prune_to_goals(self) -> "ActionGraph":
        """Create a new graph containing only actions required for goals.

        Returns:
            New pruned graph
        """
        required_actions = set()

        # Collect all dependencies of goals
        for goal in self.goals:
            required_actions.add(goal)
            required_actions.update(self.get_all_dependencies(goal))

        pruned_nodes: dict[str, ActionNode] = {}

        # Create deep copies of the nodes we keep so that pruning never mutates
        # the original graph (validator relies on this multiple times).
        for name, node in self.nodes.items():
            if name not in required_actions:
                continue

            pruned_nodes[name] = ActionNode(
                action=node.action,
                selected_version=node.selected_version,
                dependencies=set(node.dependencies) & required_actions,
                dependents=set(node.dependents) & required_actions,
            )

        pruned_goals = {goal for goal in self.goals if goal in pruned_nodes}
        return ActionGraph(nodes=pruned_nodes, goals=pruned_goals)

    def get_execution_order(self) -> list[str]:
        """Get execution order (topological sort of pruned graph).

        Returns:
            List of action names in execution order
        """
        pruned = self.prune_to_goals()
        return pruned.topological_sort()
