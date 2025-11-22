"""Graph data structures for action dependencies."""

from dataclasses import dataclass, field
from typing import Optional

from ..ast.models import ActionDefinition, ActionVersion


@dataclass(frozen=True)
class ActionId:
    """Unique identifier for an action (wrapper around action name)."""

    name: str
    """The action name"""

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class ActionKey:
    """Key for identifying action nodes in the dependency graph."""

    id: ActionId
    """The action identifier"""

    def __str__(self) -> str:
        return str(self.id)

    @classmethod
    def from_name(cls, name: str) -> "ActionKey":
        """Create an ActionKey from an action name string.

        Args:
            name: Action name

        Returns:
            ActionKey instance
        """
        return cls(id=ActionId(name=name))


@dataclass
class ActionNode:
    """Node in the action dependency graph."""

    action: ActionDefinition
    """The action definition"""

    selected_version: Optional[ActionVersion] = None
    """The selected version based on axis values"""

    dependencies: set[ActionKey] = field(default_factory=set)
    """Keys of actions this node depends on"""

    dependents: set[ActionKey] = field(default_factory=set)
    """Keys of actions that depend on this node"""

    @property
    def key(self) -> ActionKey:
        """Get the key for this node."""
        return ActionKey.from_name(self.action.name)

    def __hash__(self) -> int:
        return hash(self.key)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ActionNode):
            return False
        return self.key == other.key


@dataclass
class ActionGraph:
    """Dependency graph of actions."""

    nodes: dict[ActionKey, ActionNode]
    """All nodes in the graph indexed by action key"""

    goals: set[ActionKey]
    """Goal action keys (roots of the graph)"""

    def get_node(self, key: ActionKey) -> ActionNode:
        """Get node by action key.

        Args:
            key: Action key

        Returns:
            Action node

        Raises:
            KeyError: If action not found
        """
        if key not in self.nodes:
            raise KeyError(f"Action '{key}' not found in graph")
        return self.nodes[key]

    def get_node_by_name(self, action_name: str) -> ActionNode:
        """Get node by action name.

        Args:
            action_name: Action name

        Returns:
            Action node

        Raises:
            KeyError: If action not found
        """
        key = ActionKey.from_name(action_name)
        return self.get_node(key)

    def get_all_dependencies(self, key: ActionKey) -> set[ActionKey]:
        """Get all transitive dependencies of an action.

        Args:
            key: Action key

        Returns:
            Set of all dependency action keys
        """
        visited: set[ActionKey] = set()
        self._collect_dependencies(key, visited)
        visited.discard(key)  # Don't include the action itself
        return visited

    def get_all_dependencies_by_name(self, action_name: str) -> set[ActionKey]:
        """Get all transitive dependencies of an action by name.

        Args:
            action_name: Action name

        Returns:
            Set of all dependency action keys
        """
        key = ActionKey.from_name(action_name)
        return self.get_all_dependencies(key)

    def _collect_dependencies(self, key: ActionKey, visited: set[ActionKey]) -> None:
        """Recursively collect dependencies."""
        if key in visited:
            return

        visited.add(key)
        node = self.get_node(key)

        for dep_key in node.dependencies:
            self._collect_dependencies(dep_key, visited)

    def topological_sort(self) -> list[ActionKey]:
        """Get topological sort of the graph.

        Returns:
            List of action keys in execution order

        Raises:
            ValueError: If graph contains cycles
        """
        # Kahn's algorithm
        in_degree = {key: 0 for key in self.nodes}

        for node in self.nodes.values():
            for dep_key in node.dependencies:
                in_degree[node.key] += 1

        queue = [key for key, degree in in_degree.items() if degree == 0]
        result: list[ActionKey] = []

        while queue:
            # Sort to make order deterministic (by action name)
            queue.sort(key=lambda k: k.id.name)
            action_key = queue.pop(0)
            result.append(action_key)

            node = self.nodes[action_key]
            for dependent_key in node.dependents:
                in_degree[dependent_key] -= 1
                if in_degree[dependent_key] == 0:
                    queue.append(dependent_key)

        if len(result) != len(self.nodes):
            # Graph has a cycle
            remaining = set(self.nodes.keys()) - set(result)
            remaining_names = sorted(str(k) for k in remaining)
            raise ValueError(
                f"Dependency graph contains cycles. Actions involved: {', '.join(remaining_names)}"
            )

        return result

    def find_cycle(self) -> Optional[list[ActionKey]]:
        """Find a cycle in the graph if one exists.

        Returns:
            List of action keys forming a cycle, or None if no cycle
        """
        visited: set[ActionKey] = set()
        rec_stack: set[ActionKey] = set()
        path: list[ActionKey] = []

        def dfs(key: ActionKey) -> Optional[list[ActionKey]]:
            visited.add(key)
            rec_stack.add(key)
            path.append(key)

            node = self.nodes[key]
            for dep_key in node.dependencies:
                if dep_key not in visited:
                    cycle = dfs(dep_key)
                    if cycle:
                        return cycle
                elif dep_key in rec_stack:
                    # Found a cycle
                    cycle_start = path.index(dep_key)
                    return path[cycle_start:] + [dep_key]

            path.pop()
            rec_stack.remove(key)
            return None

        for key in self.nodes:
            if key not in visited:
                cycle = dfs(key)
                if cycle:
                    return cycle

        return None

    def prune_to_goals(self) -> "ActionGraph":
        """Create a new graph containing only actions required for goals.

        Returns:
            New pruned graph
        """
        required_actions: set[ActionKey] = set()

        # Collect all dependencies of goals
        for goal in self.goals:
            required_actions.add(goal)
            required_actions.update(self.get_all_dependencies(goal))

        pruned_nodes: dict[ActionKey, ActionNode] = {}

        # Create deep copies of the nodes we keep so that pruning never mutates
        # the original graph (validator relies on this multiple times).
        for key, node in self.nodes.items():
            if key not in required_actions:
                continue

            pruned_nodes[key] = ActionNode(
                action=node.action,
                selected_version=node.selected_version,
                dependencies=set(node.dependencies) & required_actions,
                dependents=set(node.dependents) & required_actions,
            )

        pruned_goals = {goal for goal in self.goals if goal in pruned_nodes}
        return ActionGraph(nodes=pruned_nodes, goals=pruned_goals)

    def get_execution_order(self) -> list[ActionKey]:
        """Get execution order (topological sort of pruned graph).

        Returns:
            List of action keys in execution order
        """
        pruned = self.prune_to_goals()
        return pruned.topological_sort()
