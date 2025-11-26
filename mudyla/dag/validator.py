"""Validator for action dependency graphs."""

import os
from typing import Optional

from ..ast.expansions import ArgsExpansion, EnvExpansion, FlagsExpansion, ActionExpansion, WeakActionExpansion
from ..ast.models import ParsedDocument
from .graph import ActionGraph, ActionKey


class ValidationError(Exception):
    """Validation error with details."""

    pass


class DAGValidator:
    """Validates action dependency graphs."""

    def __init__(self, document: ParsedDocument, graph: ActionGraph):
        self.document = document
        self.graph = graph
        self._pruned_graph: Optional[ActionGraph] = None

    @property
    def _required_graph(self) -> ActionGraph:
        """Pruned graph cached for all validation passes."""
        if self._pruned_graph is None:
            self._pruned_graph = self.graph.prune_to_goals()
        return self._pruned_graph

    def validate_all(
        self, args: dict[str, str], flags: dict[str, bool], axis_values: dict[str, str]
    ) -> None:
        """Run all validations.

        Args:
            args: Provided arguments
            flags: Provided flags
            axis_values: Provided axis values

        Raises:
            ValidationError: If any validation fails
        """
        errors = []

        # 1. Validate graph is acyclic
        try:
            self._validate_acyclic()
        except ValidationError as e:
            errors.append(str(e))

        # 2. Validate all dependencies exist
        try:
            self._validate_dependencies_exist()
        except ValidationError as e:
            errors.append(str(e))

        # 3. Validate retainer actions have no dependencies
        try:
            self._validate_retainer_actions()
        except ValidationError as e:
            errors.append(str(e))

        # 4. Validate environment variables
        try:
            self._validate_environment_variables()
        except ValidationError as e:
            errors.append(str(e))

        # 5. Validate arguments
        try:
            self._validate_arguments(args)
        except ValidationError as e:
            errors.append(str(e))

        # 6. Validate flags
        try:
            self._validate_flags(flags)
        except ValidationError as e:
            errors.append(str(e))

        # 7. Validate axis values
        try:
            self._validate_axis_values(axis_values)
        except ValidationError as e:
            errors.append(str(e))

        # 8. Validate action outputs
        try:
            self._validate_action_outputs()
        except ValidationError as e:
            errors.append(str(e))

        if errors:
            raise ValidationError("\n".join(errors))

    def _validate_acyclic(self) -> None:
        """Validate that the graph is acyclic."""
        cycle = self.graph.find_cycle()
        if cycle:
            cycle_str = " -> ".join(str(key) for key in cycle)
            raise ValidationError(f"Circular dependency detected: {cycle_str}")

    def _validate_dependencies_exist(self) -> None:
        """Validate that all action dependencies exist."""
        errors = []
        for node in self.graph.nodes.values():
            for dep in node.dependencies:
                if dep.action not in self.graph.nodes:
                    errors.append(
                        f"Action '{node.action.name}' depends on '{dep.action}' "
                        f"which does not exist (at {node.action.location})"
                    )
                # Validate soft dependency retainer actions
                # Note: If the soft dep target IS in the graph (was retained), we don't need
                # the retainer anymore. Only validate retainer if target is NOT in graph.
                if dep.soft and dep.retainer_action:
                    if dep.action not in self.graph.nodes and dep.retainer_action not in self.graph.nodes:
                        errors.append(
                            f"Action '{node.action.name}' has soft dependency on '{dep.action}' "
                            f"with retainer '{dep.retainer_action}' which does not exist "
                            f"(at {node.action.location})"
                        )

        if errors:
            raise ValidationError("\n".join(errors))

    def _validate_retainer_actions(self) -> None:
        """Validate that retainer actions have no dependencies.

        Retainer actions are used to decide whether soft dependencies should be
        retained. They must be self-contained and not depend on other actions.
        """
        errors = []

        # Collect all retainer actions
        retainer_keys: set[ActionKey] = set()
        for node in self.graph.nodes.values():
            for dep in node.dependencies:
                if dep.soft and dep.retainer_action:
                    retainer_keys.add(dep.retainer_action)

        # Validate each retainer has no dependencies
        for retainer_key in retainer_keys:
            if retainer_key not in self.graph.nodes:
                continue  # Already caught by _validate_dependencies_exist

            retainer_node = self.graph.nodes[retainer_key]
            if retainer_node.dependencies:
                dep_names = ", ".join(str(d.action) for d in retainer_node.dependencies)
                errors.append(
                    f"Retainer action '{retainer_key}' must have no dependencies, "
                    f"but depends on: {dep_names} (at {retainer_node.action.location})"
                )

        if errors:
            raise ValidationError("\n".join(errors))

    def _validate_environment_variables(self) -> None:
        """Validate that all required environment variables are present."""
        missing_vars = []

        # Build the effective environment: start with the real env and merge in
        # any explicitly configured values from the document.
        available_env = dict(os.environ) | self.document.environment_vars

        pruned_graph = self._required_graph

        for node in pruned_graph.nodes.values():
            if not node.selected_version:
                continue

            # Check environment variables from expansions
            for expansion in node.selected_version.expansions:
                if isinstance(expansion, EnvExpansion):
                    if expansion.variable_name not in available_env:
                        missing_vars.append(
                            f"Action '{node.action.name}' requires environment "
                            f"variable '{expansion.variable_name}' which is not set "
                            f"(at {node.selected_version.location})"
                        )

            # Check explicit dep env.VAR declarations
            for env_dep in node.selected_version.env_dependencies:
                if env_dep not in available_env:
                    missing_vars.append(
                        f"Action '{node.action.name}' declares dependency on environment "
                        f"variable '{env_dep}' which is not set "
                        f"(at {node.selected_version.location})"
                    )

            # Check documented env vars
            for var_name in node.action.required_env_vars:
                if var_name not in available_env:
                    missing_vars.append(
                        f"Action '{node.action.name}' requires environment "
                        f"variable '{var_name}' which is not set "
                        f"(documented at {node.action.location})"
                    )

        if missing_vars:
            unique_vars = list(set(missing_vars))
            raise ValidationError("\n".join(unique_vars))

    def _validate_arguments(self, args: dict[str, str]) -> None:
        """Validate that all required arguments are provided."""
        errors = []

        pruned_graph = self._required_graph

        # Collect all used arguments
        used_args = set()
        for node in pruned_graph.nodes.values():
            if not node.selected_version:
                continue

            for expansion in node.selected_version.expansions:
                if isinstance(expansion, ArgsExpansion):
                    used_args.add(expansion.argument_name)

        # Check each used argument
        for arg_name in used_args:
            if arg_name not in self.document.arguments:
                errors.append(
                    f"Argument 'args.{arg_name}' is used but not defined in arguments section"
                )
                continue

            arg_def = self.document.arguments[arg_name]

            # Check if mandatory argument is provided
            if arg_def.is_mandatory and arg_name not in args:
                errors.append(
                    f"Mandatory argument 'args.{arg_name}' is not provided "
                    f"(defined at {arg_def.location})"
                )

        if errors:
            raise ValidationError("\n".join(errors))

    def _validate_flags(self, flags: dict[str, bool]) -> None:
        """Validate that all used flags are defined."""
        errors = []

        pruned_graph = self._required_graph

        # Collect all used flags
        used_flags = set()
        for node in pruned_graph.nodes.values():
            if not node.selected_version:
                continue

            for expansion in node.selected_version.expansions:
                if isinstance(expansion, FlagsExpansion):
                    used_flags.add(expansion.flag_name)

        # Check each used flag is defined
        for flag_name in used_flags:
            if flag_name not in self.document.flags:
                errors.append(
                    f"Flag 'flags.{flag_name}' is used but not defined in flags section"
                )

        if errors:
            raise ValidationError("\n".join(errors))

    def _validate_axis_values(self, axis_values: dict[str, str]) -> None:
        """Validate axis values."""
        errors = []

        pruned_graph = self._required_graph

        # Collect required axis
        required_axis = set()
        for node in pruned_graph.nodes.values():
            if node.action.is_multi_version:
                required_axis.update(node.action.get_required_axes())

        # Check all required axis are provided
        for axis_name in required_axis:
            if axis_name not in axis_values:
                # Check if there's a default
                if axis_name not in self.document.axis:
                    errors.append(f"Axis '{axis_name}' is required but not defined")
                    continue

                axis_def = self.document.axis[axis_name]
                default_value = axis_def.get_default_value()

                if default_value is None:
                    actions_needing_axis = [
                        node.action.name
                        for node in pruned_graph.nodes.values()
                        if axis_name in node.action.get_required_axes()
                    ]
                    errors.append(
                        f"Axis '{axis_name}' must be specified (required by: {', '.join(actions_needing_axis)})"
                    )

        # Validate provided values are valid
        for axis_name, axis_value in axis_values.items():
            if axis_name not in self.document.axis:
                errors.append(f"Axis '{axis_name}' is not defined")
                continue

            axis_def = self.document.axis[axis_name]
            try:
                axis_def.validate_value(axis_value)
            except ValueError as e:
                errors.append(str(e))

        if errors:
            raise ValidationError("\n".join(errors))

    def _validate_action_outputs(self) -> None:
        """Validate that all required action outputs are provided.

        Weak dependencies that were pruned are skipped (WeakActionExpansion
        gracefully returns empty string if the action is missing).
        """
        errors = []

        pruned_graph = self._required_graph

        for node in pruned_graph.nodes.values():
            if not node.selected_version:
                errors.append(
                    f"Action '{node.action.name}' has no valid version selected"
                )
                continue

            # Get all required outputs, tracking whether each is from a weak dependency
            required_outputs: dict[str, set[str]] = {}  # dep_action -> {output_names}
            weak_expansions: dict[str, bool] = {}  # dep_action -> is_weak

            for expansion in node.selected_version.expansions:
                if isinstance(expansion, (ActionExpansion, WeakActionExpansion)):
                    dep_action = expansion.get_dependency_action()
                    if dep_action not in required_outputs:
                        required_outputs[dep_action] = set()
                        weak_expansions[dep_action] = isinstance(expansion, WeakActionExpansion)
                    required_outputs[dep_action].add(expansion.variable_name)

            # Check each dependency provides the required outputs
            for dep_action, output_names in required_outputs.items():
                dep_key = ActionKey.from_name(dep_action)
                is_weak = weak_expansions.get(dep_action, False)

                # If this is a weak dependency and the action was pruned, skip validation
                # (WeakActionExpansion will return empty string)
                if is_weak and dep_key not in pruned_graph.nodes:
                    continue

                # For strong dependencies, missing action is an error (caught elsewhere)
                if dep_key not in pruned_graph.nodes:
                    continue  # This will be caught by dependency validation

                dep_node = pruned_graph.nodes[dep_key]
                if not dep_node.selected_version:
                    continue

                # Get all outputs provided by this dependency
                provided_outputs = {
                    ret.name for ret in dep_node.selected_version.return_declarations
                }

                # Check all required outputs are provided
                missing = output_names - provided_outputs
                if missing:
                    # For weak dependencies, missing outputs are tolerated (will be empty string)
                    if not is_weak:
                        errors.append(
                            f"Action '{node.action.name}' requires outputs "
                            f"{{{', '.join(sorted(missing))}}} from '{dep_action}', "
                            f"but '{dep_action}' only provides "
                            f"{{{', '.join(sorted(provided_outputs))}}}"
                        )

        if errors:
            raise ValidationError("\n".join(errors))
