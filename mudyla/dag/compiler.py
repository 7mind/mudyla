"""Compiler that transforms Raw AST + CLI inputs into contextualized DAG AST.

This compiler implements the multi-context execution model inspired by DIStage:
1. Parse raw AST and CLI inputs
2. Compute execution contexts for each action invocation
3. Build separate dependency graphs for each context
4. Unify graphs by merging nodes with identical keys
"""

import platform
from dataclasses import dataclass
from typing import Dict, List, Set

from ..ast.expansions import ActionExpansion, WeakActionExpansion
from ..ast.models import ParsedDocument
from ..cli_args import ParsedCLIInputs, ActionInvocation
from .context import ContextId, ExecutionContext
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


class CompilationError(Exception):
    """Raised when compilation fails."""


@dataclass(frozen=True)
class ContextualInvocation:
    """An action invocation with its computed execution context."""

    action_name: str
    execution_context: ExecutionContext


class DAGCompiler:
    """Compiles raw AST with CLI inputs into contextualized DAG AST."""

    def __init__(self, document: ParsedDocument, cli_inputs: ParsedCLIInputs):
        """Initialize the compiler.

        Args:
            document: Parsed document (raw AST)
            cli_inputs: Parsed CLI inputs with action invocations
        """
        self.document = document
        self.cli_inputs = cli_inputs
        self.current_platform = get_normalized_platform()

    def compile(self) -> ActionGraph:
        """Compile raw AST + CLI inputs into DAG AST.

        Returns:
            Unified action graph with contextualized keys

        Raises:
            CompilationError: If compilation fails
        """
        # Step 1: Compute execution contexts for each action invocation
        contextual_invocations = self._compute_contexts()

        # Step 2: Build separate graphs for each invocation
        individual_graphs: List[ActionGraph] = []
        for invocation in contextual_invocations:
            graph = self._build_graph_for_invocation(invocation)
            individual_graphs.append(graph)

        # Step 3: Unify all graphs
        unified_graph = self._unify_graphs(individual_graphs)

        return unified_graph

    def _compute_contexts(self) -> List[ContextualInvocation]:
        """Compute execution contexts for all action invocations.

        Merges global and per-action configurations, applies defaults.

        Returns:
            List of contextual invocations
        """
        contextual_invocations: List[ContextualInvocation] = []

        for invocation in self.cli_inputs.action_invocations:
            # Merge axes: global + per-action
            merged_axes = dict(self.cli_inputs.global_axes)
            merged_axes.update(invocation.axes)

            # Apply axis defaults
            for axis_name, axis_def in self.document.axis.items():
                if axis_name not in merged_axes:
                    default_value = axis_def.get_default_value()
                    if default_value:
                        merged_axes[axis_name] = default_value

            # Create context ID from all axis values
            context_id = ContextId.from_dict(merged_axes)

            # Merge args: global + per-action
            merged_args = dict(self.cli_inputs.global_args)
            merged_args.update(invocation.args)

            # Apply argument defaults
            for arg_name, arg_def in self.document.arguments.items():
                if arg_name not in merged_args and arg_def.default_value is not None:
                    merged_args[arg_name] = arg_def.default_value

            # Merge flags: global + per-action
            merged_flags = dict(self.cli_inputs.global_flags)
            merged_flags.update(invocation.flags)

            # Initialize all defined flags to False if not present
            for flag_name in self.document.flags:
                if flag_name not in merged_flags:
                    merged_flags[flag_name] = False

            # Create execution context
            execution_context = ExecutionContext(
                context_id=context_id,
                args=merged_args,
                flags=merged_flags,
            )

            contextual_invocations.append(
                ContextualInvocation(
                    action_name=invocation.action_name,
                    execution_context=execution_context,
                )
            )

        return contextual_invocations

    def _build_graph_for_invocation(
        self, invocation: ContextualInvocation
    ) -> ActionGraph:
        """Build a dependency graph for a single action invocation.

        Each action gets a reduced context based on the axes it cares about.
        This allows axis-independent actions to be shared across contexts.

        Args:
            invocation: Contextual invocation to build graph for

        Returns:
            Action graph with context-appropriate keys

        Raises:
            CompilationError: If graph building fails
        """
        full_context_id = invocation.execution_context.context_id
        axis_values = invocation.execution_context.axis_values
        goal_action_name = invocation.action_name

        # Validate goal exists
        if goal_action_name not in self.document.actions:
            raise CompilationError(f"Action '{goal_action_name}' not found")

        # Build nodes for all actions with their reduced contexts
        nodes: Dict[ActionKey, ActionNode] = {}

        for action_name, action in self.document.actions.items():
            # Compute reduced context based on axes this action cares about
            required_axes = action.get_required_axes()
            reduced_context_id = full_context_id.reduce_to_axes(required_axes)
            action_key = ActionKey.from_name(action_name, reduced_context_id)

            # Select appropriate version based on FULL axis values
            # (version selection needs all axes to pick the right version)
            try:
                selected_version = action.get_version(axis_values, self.current_platform)
            except ValueError:
                # If version selection fails, set to None
                # The validator will check if this action is actually required
                selected_version = None

            # Extract dependencies with their own reduced contexts
            dependencies: Set[Dependency] = set()
            if selected_version:
                # Implicit dependencies from expansions
                for expansion in selected_version.expansions:
                    if isinstance(expansion, (ActionExpansion, WeakActionExpansion)):
                        dep_name = expansion.get_dependency_action()
                        # Get dependency's reduced context based on its required axes
                        dep_action = self.document.actions.get(dep_name)
                        if dep_action:
                            dep_required_axes = dep_action.get_required_axes()
                            dep_context_id = full_context_id.reduce_to_axes(dep_required_axes)
                        else:
                            dep_context_id = reduced_context_id
                        dep_key = ActionKey.from_name(dep_name, dep_context_id)
                        is_weak = isinstance(expansion, WeakActionExpansion)
                        dependencies.add(Dependency(action=dep_key, weak=is_weak))

                # Explicit dependencies
                for dep_decl in selected_version.dependency_declarations:
                    dep_name = dep_decl.action_name
                    # Get dependency's reduced context based on its required axes
                    dep_action = self.document.actions.get(dep_name)
                    if dep_action:
                        dep_required_axes = dep_action.get_required_axes()
                        dep_context_id = full_context_id.reduce_to_axes(dep_required_axes)
                    else:
                        dep_context_id = reduced_context_id
                    dep_key = ActionKey.from_name(dep_name, dep_context_id)

                    if dep_decl.soft and dep_decl.retainer_action:
                        # Get retainer's reduced context
                        retainer_action = self.document.actions.get(dep_decl.retainer_action)
                        if retainer_action:
                            retainer_required_axes = retainer_action.get_required_axes()
                            retainer_context_id = full_context_id.reduce_to_axes(retainer_required_axes)
                        else:
                            retainer_context_id = reduced_context_id
                        retainer_key = ActionKey.from_name(dep_decl.retainer_action, retainer_context_id)
                        dependencies.add(Dependency(
                            action=dep_key,
                            soft=True,
                            retainer_action=retainer_key,
                        ))
                    else:
                        dependencies.add(Dependency(action=dep_key, weak=dep_decl.weak))

            node = ActionNode(
                key=action_key,
                action=action,
                selected_version=selected_version,
                dependencies=dependencies,
                args=invocation.execution_context.args,
                flags=invocation.execution_context.flags,
            )
            nodes[action_key] = node

        # Build reverse edges (dependents)
        for action_key, node in nodes.items():
            for dep in node.dependencies:
                if dep.action in nodes:
                    nodes[dep.action].dependents.add(
                        Dependency(action=action_key, weak=dep.weak)
                    )

        # Goal uses the goal action's reduced context
        goal_action = self.document.actions[goal_action_name]
        goal_required_axes = goal_action.get_required_axes()
        goal_context_id = full_context_id.reduce_to_axes(goal_required_axes)
        goal_key = ActionKey.from_name(goal_action_name, goal_context_id)
        goal_keys = {goal_key}

        return ActionGraph(nodes=nodes, goals=goal_keys)

    def _unify_graphs(self, graphs: List[ActionGraph]) -> ActionGraph:
        """Unify multiple graphs by merging nodes with identical keys.

        If the same ActionKey appears in multiple graphs:
        - With identical ActionNode: merge (union of edges)
        - With different ActionNode: error (conflicting definitions)

        Args:
            graphs: List of graphs to unify

        Returns:
            Unified graph

        Raises:
            CompilationError: If conflicting definitions are found
        """
        unified_nodes: Dict[ActionKey, ActionNode] = {}
        unified_goals: Set[ActionKey] = set()

        for graph in graphs:
            # Merge goals
            unified_goals.update(graph.goals)

            # Merge nodes
            for key, node in graph.nodes.items():
                if key not in unified_nodes:
                    # New node - add it
                    unified_nodes[key] = node
                else:
                    # Node exists - verify it's identical and merge edges
                    existing_node = unified_nodes[key]
                    if not self._nodes_are_compatible(existing_node, node):
                        raise CompilationError(
                            f"Conflicting definitions for action '{key}'. "
                            f"Same action invoked multiple times with same context "
                            f"but different configurations."
                        )

                    # Merge edges (union) and preserve args/flags
                    # Use existing_node's args/flags (they should be identical if nodes are compatible)
                    unified_nodes[key] = ActionNode(
                        key=key,
                        action=existing_node.action,
                        selected_version=existing_node.selected_version,
                        dependencies=existing_node.dependencies | node.dependencies,
                        dependents=existing_node.dependents | node.dependents,
                        args=existing_node.args,
                        flags=existing_node.flags,
                    )

        return ActionGraph(nodes=unified_nodes, goals=unified_goals)

    def _nodes_are_compatible(self, node1: ActionNode, node2: ActionNode) -> bool:
        """Check if two nodes with the same key are compatible for merging.

        Nodes are compatible if they have the same action and selected version.
        Dependencies and dependents can differ (they will be merged).

        Args:
            node1: First node
            node2: Second node

        Returns:
            True if nodes are compatible
        """
        # Must be the same action
        if node1.action.name != node2.action.name:
            return False

        # Must have selected the same version
        # (Both None or same conditions)
        if node1.selected_version is None and node2.selected_version is None:
            return True

        if node1.selected_version is None or node2.selected_version is None:
            return False

        # Compare versions by their conditions
        return (
            node1.selected_version.conditions == node2.selected_version.conditions
        )

    def validate_action_invocations(self) -> None:
        """Validate that all action invocations reference existing actions.

        Raises:
            CompilationError: If any action doesn't exist
        """
        for invocation in self.cli_inputs.action_invocations:
            if invocation.action_name not in self.document.actions:
                available = ", ".join(sorted(self.document.actions.keys()))
                raise CompilationError(
                    f"Action '{invocation.action_name}' not found. "
                    f"Available actions: {available}"
                )
