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


@dataclass(frozen=True)
class Dependency:
    """Represents a dependency between actions.

    A dependency can be either strong (regular) or weak. Weak dependencies
    are only retained if the target action is already required by a strong
    dependency path from a goal. This is similar to weak dependencies in
    distage dependency injection.
    """

    action: ActionKey
    """The action being depended on"""

    weak: bool = False
    """Whether this is a weak dependency (does not force retention)"""

    def __str__(self) -> str:
        qualifier = "weak " if self.weak else ""
        return f"{qualifier}{self.action}"


@dataclass
class ActionNode:
    """Node in the action dependency graph."""

    action: ActionDefinition
    """The action definition"""

    selected_version: Optional[ActionVersion] = None
    """The selected version based on axis values"""

    dependencies: set[Dependency] = field(default_factory=set)
    """Dependencies of this node (can be strong or weak)"""

    dependents: set[Dependency] = field(default_factory=set)
    """Nodes that depend on this node (can be strong or weak)"""

    @property
    def key(self) -> ActionKey:
        """Get the key for this node."""
        return ActionKey.from_name(self.action.name)

    def get_dependency_keys(self) -> set[ActionKey]:
        """Get all dependency action keys (both strong and weak)."""
        return {dep.action for dep in self.dependencies}

    def get_strong_dependency_keys(self) -> set[ActionKey]:
        """Get only strong dependency action keys."""
        return {dep.action for dep in self.dependencies if not dep.weak}

    def get_weak_dependency_keys(self) -> set[ActionKey]:
        """Get only weak dependency action keys."""
        return {dep.action for dep in self.dependencies if dep.weak}

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
        """Recursively collect dependencies (both strong and weak)."""
        if key in visited:
            return

        visited.add(key)
        node = self.get_node(key)

        for dep in node.dependencies:
            self._collect_dependencies(dep.action, visited)

    def _collect_strong_dependencies(self, key: ActionKey, visited: set[ActionKey]) -> None:
        """Recursively collect only strong dependencies."""
        if key in visited:
            return

        visited.add(key)
        node = self.get_node(key)

        for dep in node.dependencies:
            if not dep.weak:
                self._collect_strong_dependencies(dep.action, visited)

    def topological_sort(self) -> list[ActionKey]:
        """Get topological sort of the graph.

        Returns:
            List of action keys in execution order

        Raises:
            ValueError: If graph contains cycles
        """
        # Kahn's algorithm - count all dependencies (strong and weak)
        in_degree = {key: 0 for key in self.nodes}

        for node in self.nodes.values():
            # Count number of dependencies (all types)
            in_degree[node.key] = len(node.dependencies)

        queue = [key for key, degree in in_degree.items() if degree == 0]
        result: list[ActionKey] = []

        while queue:
            # Sort to make order deterministic (by action name)
            queue.sort(key=lambda k: k.id.name)
            action_key = queue.pop(0)
            result.append(action_key)

            node = self.nodes[action_key]
            for dependent in node.dependents:
                in_degree[dependent.action] -= 1
                if in_degree[dependent.action] == 0:
                    queue.append(dependent.action)

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

        Checks for cycles considering both strong and weak dependencies.

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
            for dep in node.dependencies:
                if dep.action not in visited:
                    cycle = dfs(dep.action)
                    if cycle:
                        return cycle
                elif dep.action in rec_stack:
                    # Found a cycle
                    cycle_start = path.index(dep.action)
                    return path[cycle_start:] + [dep.action]

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

        This implements weak dependency semantics: weak dependencies are only
        retained if the target action is already required via a strong dependency
        path from a goal.

        Returns:
            New pruned graph
        """
        # First, collect actions reachable via strong dependencies only
        retained_actions: set[ActionKey] = set()
        for goal in self.goals:
            self._collect_strong_dependencies(goal, retained_actions)

        pruned_nodes: dict[ActionKey, ActionNode] = {}

        # Create deep copies of the nodes we keep
        for key, node in self.nodes.items():
            if key not in retained_actions:
                continue

            # Filter dependencies: keep all dependencies (strong and weak)
            # whose targets are in the retained set
            pruned_dependencies = {
                dep for dep in node.dependencies
                if dep.action in retained_actions
            }

            # Filter dependents similarly
            pruned_dependents = {
                dep for dep in node.dependents
                if dep.action in retained_actions
            }

            pruned_nodes[key] = ActionNode(
                action=node.action,
                selected_version=node.selected_version,
                dependencies=pruned_dependencies,
                dependents=pruned_dependents,
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
