"""Graph data structures for action dependencies."""

from dataclasses import dataclass, field
from typing import Optional

from ..ast.models import ActionDefinition, ActionVersion
from .context import ContextId


@dataclass(frozen=True)
class ActionId:
    """Unique identifier for an action (wrapper around action name)."""

    name: str
    """The action name"""

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class ActionKey:
    """Key for identifying action nodes in the dependency graph.

    In the multi-context system, actions are identified by both their name
    and their execution context. This allows the same action to be executed
    multiple times with different axis values.
    """

    id: ActionId
    """The action identifier"""

    context_id: ContextId
    """The execution context identifier"""

    def __str__(self) -> str:
        """Format as context#action_name or just action_name for default context."""
        context_str = str(self.context_id)
        if context_str == "default":
            return str(self.id)
        return f"{context_str}#{self.id}"

    @classmethod
    def from_name(cls, name: str, context_id: Optional[ContextId] = None) -> "ActionKey":
        """Create an ActionKey from an action name string.

        Args:
            name: Action name
            context_id: Context identifier (defaults to empty context)

        Returns:
            ActionKey instance
        """
        if context_id is None:
            context_id = ContextId.empty()
        return cls(id=ActionId(name=name), context_id=context_id)


@dataclass(frozen=True)
class Dependency:
    """Represents a dependency between actions.

    A dependency can be:
    - Strong (regular): Target action is always retained
    - Weak: Target is retained only if already required by strong path
    - Soft: Like weak, but a retainer action decides whether to retain

    Soft dependencies run a retainer action when the target is only reachable
    via soft dependencies. If the retainer calls retain(), the target is kept.
    """

    action: ActionKey
    """The action being depended on"""

    weak: bool = False
    """Whether this is a weak dependency (does not force retention)"""

    soft: bool = False
    """Whether this is a soft dependency (requires retainer to decide)"""

    retainer_action: Optional[ActionKey] = None
    """For soft dependencies, the action that decides whether to retain"""

    def __str__(self) -> str:
        if self.soft and self.retainer_action:
            return f"soft {self.action} retain.{self.retainer_action}"
        qualifier = "weak " if self.weak else ""
        return f"{qualifier}{self.action}"


@dataclass
class ActionNode:
    """Node in the action dependency graph."""

    key: ActionKey
    """The unique key for this action (includes context)"""

    action: ActionDefinition
    """The action definition"""

    selected_version: Optional[ActionVersion] = None
    """The selected version based on axis values"""

    dependencies: set[Dependency] = field(default_factory=set)
    """Dependencies of this node (can be strong or weak)"""

    dependents: set[Dependency] = field(default_factory=set)
    """Nodes that depend on this node (can be strong or weak)"""

    args: Optional[dict[str, str]] = None
    """Per-action arguments (for multi-context support)"""

    flags: Optional[dict[str, bool]] = None
    """Per-action flags (for multi-context support)"""

    def get_dependency_keys(self) -> set[ActionKey]:
        """Get all dependency action keys (strong, weak, and soft)."""
        return {dep.action for dep in self.dependencies}

    def get_strong_dependency_keys(self) -> set[ActionKey]:
        """Get only strong dependency action keys (not weak or soft)."""
        return {dep.action for dep in self.dependencies if not dep.weak and not dep.soft}

    def get_weak_dependency_keys(self) -> set[ActionKey]:
        """Get only weak dependency action keys (not soft)."""
        return {dep.action for dep in self.dependencies if dep.weak and not dep.soft}

    def get_soft_dependencies(self) -> set[Dependency]:
        """Get soft dependencies with their retainer info."""
        return {dep for dep in self.dependencies if dep.soft}

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
        """Recursively collect only strong dependencies (not weak or soft)."""
        if key in visited:
            return

        visited.add(key)
        node = self.get_node(key)

        for dep in node.dependencies:
            if not dep.weak and not dep.soft:
                self._collect_strong_dependencies(dep.action, visited)

    def topological_sort(self) -> list[ActionKey]:
        """Get topological sort of the graph.

        Returns:
            List of action keys in execution order

        Raises:
            ValueError: If graph contains cycles
        """
        # Kahn's algorithm - count only dependencies that are in the graph
        in_degree = {key: 0 for key in self.nodes}

        for node in self.nodes.values():
            # Count UNIQUE dependency actions that are in the graph
            # (Multiple Dependency objects can point to the same target with different retainers)
            unique_dep_actions = {dep.action for dep in node.dependencies if dep.action in self.nodes}
            in_degree[node.key] = len(unique_dep_actions)

        queue = [key for key, degree in in_degree.items() if degree == 0]
        result: list[ActionKey] = []

        while queue:
            # Sort to make order deterministic (by action name)
            queue.sort(key=lambda k: k.id.name)
            action_key = queue.pop(0)
            result.append(action_key)

            node = self.nodes[action_key]
            for dependent in node.dependents:
                # Only decrement if the dependent is in the graph
                if dependent.action in in_degree:
                    in_degree[dependent.action] -= 1
                    if in_degree[dependent.action] == 0:
                        queue.append(dependent.action)

        if len(result) != len(self.nodes):
            # Graph has a cycle - find and report the actual cycle path
            cycle = self.find_cycle()
            if cycle:
                cycle_path = " -> ".join(str(k) for k in cycle)
                raise ValueError(f"Dependency graph contains a cycle: {cycle_path}")
            else:
                # Fallback if we can't find the cycle (shouldn't happen)
                remaining = set(self.nodes.keys()) - set(result)
                remaining_names = sorted(str(k) for k in remaining)
                raise ValueError(
                    f"Dependency graph contains cycles. Actions involved: {', '.join(remaining_names)}"
                )

        return result

    def find_cycle(self) -> Optional[list[ActionKey]]:
        """Find a cycle in the graph if one exists.

        Checks for cycles considering both strong and weak dependencies,
        but only for dependencies pointing to nodes in the graph.

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
                # Skip dependencies to nodes not in the graph
                if dep.action not in self.nodes:
                    continue

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

    def get_pending_soft_dependencies(self) -> list[Dependency]:
        """Get soft dependencies that need retainer decisions.

        Returns soft dependencies whose targets are not already reachable via
        strong dependencies. These need their retainers to run before we can
        finalize the pruned graph.

        Only considers soft dependencies from nodes that are reachable via
        strong dependencies from goals.

        Returns:
            List of soft dependencies needing retainer decisions
        """
        # Collect actions reachable via strong dependencies only
        retained_via_strong: set[ActionKey] = set()
        for goal in self.goals:
            self._collect_strong_dependencies(goal, retained_via_strong)

        pending_soft: list[Dependency] = []

        # Only check soft dependencies in nodes reachable via strong deps
        for key in retained_via_strong:
            if key not in self.nodes:
                continue
            node = self.nodes[key]
            for dep in node.dependencies:
                if dep.soft and dep.retainer_action:
                    # If target is already retained via strong path, no retainer needed
                    if dep.action not in retained_via_strong:
                        pending_soft.append(dep)

        return pending_soft

    def prune_to_goals(self, retained_soft_targets: set[ActionKey] | None = None) -> "ActionGraph":
        """Create a new graph containing only actions required for goals.

        This implements weak and soft dependency semantics:
        - Weak dependencies are only retained if the target action is already
          required via a strong dependency path from a goal.
        - Soft dependencies are retained if they are in retained_soft_targets
          (decided by running retainers).

        Args:
            retained_soft_targets: Set of soft dependency targets to retain
                                   (determined by retainer execution).

        Returns:
            New pruned graph
        """
        retained_soft_targets = retained_soft_targets or set()

        # Collect actions reachable via strong dependencies only
        retained_actions: set[ActionKey] = set()
        for goal in self.goals:
            self._collect_strong_dependencies(goal, retained_actions)

        # Add soft dependency targets that were retained by their retainers
        # We need to also add their transitive strong dependencies
        for target in retained_soft_targets:
            if target in self.nodes:
                self._collect_strong_dependencies(target, retained_actions)

        pruned_nodes: dict[ActionKey, ActionNode] = {}

        # Create deep copies of the nodes we keep
        for key, node in self.nodes.items():
            if key not in retained_actions:
                continue

            # Filter dependencies: keep all dependencies (strong, weak, soft)
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
                key=node.key,
                action=node.action,
                selected_version=node.selected_version,
                dependencies=pruned_dependencies,
                dependents=pruned_dependents,
                args=node.args,
                flags=node.flags,
            )

        pruned_goals = {goal for goal in self.goals if goal in pruned_nodes}
        return ActionGraph(nodes=pruned_nodes, goals=pruned_goals)

    def get_execution_order(self) -> list[ActionKey]:
        """Get execution order (topological sort of the graph).

        Returns:
            List of action keys in execution order

        Note: This method assumes the graph is already pruned if needed.
        If you need to prune first, call prune_to_goals() and then
        get_execution_order() on the result.
        """
        return self.topological_sort()
