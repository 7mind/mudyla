"""Integration tests for multi-context execution."""

from pathlib import Path

import pytest

from tests.conftest import MudylaRunner


@pytest.mark.integration
class TestMultiContext:
    """Test multi-context execution features."""

    def test_multiple_contexts_for_same_action(self, mdl: MudylaRunner, clean_test_output):
        """Test that the same action can be invoked with different axis values."""
        result = mdl.run_success([
            ":conditional-build",
            "--axis build-mode:development",
            ":conditional-build",
            "--axis build-mode:release",
        ])

        # Verify both contexts were executed
        # conditional-build only cares about build-mode axis
        mdl.assert_in_output(result, "build-mode:development")
        mdl.assert_in_output(result, "build-mode:release")
        mdl.assert_in_output(result, "conditional-build")

        # create-directory has no axis conditions, so it gets default context
        # and is SHARED between both conditional-build contexts
        mdl.assert_in_output(result, "create-directory")

        # Verify 3 actions (1 shared create-directory + 2 conditional-build)
        mdl.assert_in_output(result, "3 required action(s)")

    def test_context_inheritance_for_dependencies(self, mdl: MudylaRunner, clean_test_output):
        """Test that dependencies get their own reduced context.

        Dependencies no longer inherit full context from parent. Instead, each
        action gets a context with only the axes it cares about. This allows
        axis-independent dependencies to be shared.
        """
        result = mdl.run_success([
            ":conditional-build",
            "--axis build-mode:development",
            ":conditional-build",
            "--axis build-mode:release",
        ])

        # conditional-build only cares about build-mode axis
        mdl.assert_in_output(result, "build-mode:development")
        mdl.assert_in_output(result, "build-mode:release")
        mdl.assert_in_output(result, "conditional-build")

        # create-directory has no axis conditions, gets default context
        mdl.assert_in_output(result, "create-directory")
        mdl.assert_in_output(result, "default")

        # Verify execution completed successfully
        mdl.assert_in_output(result, "Execution completed successfully")

    def test_per_action_arguments(self, mdl: MudylaRunner, clean_test_output):
        """Test that different invocations can have different argument values."""
        result = mdl.run_success([
            ":write-message",
            "--message=FirstMessage",
            ":write-message",
            "--message=SecondMessage",
        ])

        # Verify both invocations executed
        mdl.assert_in_output(result, "write-message")
        mdl.assert_in_output(result, "Execution completed successfully")

        # Verify output file exists (last invocation wins in same context)
        mdl.assert_file_exists("test-output/message.txt")

    def test_graph_unification(self, mdl: MudylaRunner, clean_test_output):
        """Test that duplicate invocations with same context are unified."""
        result = mdl.run_success([
            ":conditional-build",
            "--axis build-mode:release",
            ":conditional-build",
            "--axis build-mode:release",
        ])

        # Verify unification occurred - should only have 2 actions
        # (1 shared create-directory with default context + 1 conditional-build)
        mdl.assert_in_output(result, "2 required action(s)")

        # conditional-build only cares about build-mode
        mdl.assert_in_output(result, "build-mode:release")
        mdl.assert_in_output(result, "create-directory")
        mdl.assert_in_output(result, "conditional-build")

        # Verify execution completed successfully
        mdl.assert_in_output(result, "Execution completed successfully")

    def test_output_isolation_between_contexts(self, mdl: MudylaRunner, clean_test_output):
        """Test that outputs are isolated between contexts."""
        result = mdl.run_success([
            ":conditional-build",
            "--axis build-mode:development",
            ":conditional-build",
            "--axis build-mode:release",
        ])

        # Both should create output file (last one wins for the same path)
        mdl.assert_file_exists("test-output/build-mode.txt")

        # Verify execution completed
        mdl.assert_in_output(result, "Execution completed successfully")

    def test_context_in_rich_table(self, mdl: MudylaRunner, clean_test_output):
        """Test that context column appears in rich table for multi-context execution."""
        result = mdl.run_success([
            ":conditional-build",
            "--axis build-mode:development",
            ":conditional-build",
            "--axis build-mode:release",
        ])

        # Verify table has Context and Action columns
        mdl.assert_in_output(result, "Context")
        mdl.assert_in_output(result, "Action")

        # Verify contexts are shown separately
        mdl.assert_in_output(result, "build-mode:development")
        mdl.assert_in_output(result, "build-mode:release")

        # Verify action names are shown
        mdl.assert_in_output(result, "create-directory")
        mdl.assert_in_output(result, "conditional-build")

    def test_context_format_in_output(self, mdl: MudylaRunner, clean_test_output):
        """Test that context format uses 'context#action' notation."""
        result = mdl.run_success([
            ":conditional-build",
            "--axis build-mode:release",
        ])

        # Verify context#action format
        # conditional-build only cares about build-mode
        mdl.assert_in_output(result, "build-mode:release")
        mdl.assert_in_output(result, "#create-directory")
        mdl.assert_in_output(result, "#conditional-build")

        # Verify NOT using old action@context format
        mdl.assert_not_in_output(result, "create-directory@")
        mdl.assert_not_in_output(result, "conditional-build@")


@pytest.mark.integration
class TestMultiContextEdgeCases:
    """Test edge cases and error handling in multi-context execution."""

    def test_contradictory_axis_values_rejected(self, mdl: MudylaRunner, clean_test_output):
        """Test that contradictory axis values are rejected."""
        # This should fail because it sets global axis and per-action axis
        result = mdl.run_failure([
            "--axis build-mode:development",  # Global axis
            ":conditional-build",
            "--axis build-mode:release",  # Per-action axis (different value)
        ])

        # Verify error message
        mdl.assert_in_output(result, "Contradictory axis values")

    def test_default_context_when_no_multi_context(self, mdl: MudylaRunner, clean_test_output):
        """Test that actions without axis conditions get default context."""
        result = mdl.run_success([":create-directory"])

        # create-directory has no axis conditions, so it gets default context
        mdl.assert_in_output(result, "default")
        mdl.assert_in_output(result, "#create-directory")

        # Verify execution completed
        mdl.assert_in_output(result, "Execution completed successfully")

    def test_complex_multi_axis_context(self, mdl: MudylaRunner, clean_test_output):
        """Test that actions only get axes they care about."""
        result = mdl.run_success([
            ":conditional-build",
            "--axis build-mode:release",
        ])

        # conditional-build only cares about build-mode, not cross-platform or platform
        mdl.assert_in_output(result, "build-mode:release")
        mdl.assert_in_output(result, "#conditional-build")

        # create-directory has no conditions, gets default
        mdl.assert_in_output(result, "default")
        mdl.assert_in_output(result, "#create-directory")

    def test_shared_dependency_across_contexts(self, mdl: MudylaRunner, clean_test_output):
        """Test that axis-independent dependencies are shared across contexts.

        When platform-build depends on generate-sources, and we run platform-build
        for multiple platforms, generate-sources runs only ONCE because it doesn't
        care about the cross-platform axis.

        This tests the reduced context feature - actions only get contexts
        containing axes they actually care about.
        """
        result = mdl.run_success([
            ":platform-build",
            "--axis cross-platform:jvm",
            ":platform-build",
            "--axis cross-platform:js",
        ])

        # With reduced contexts: 3 actions (1 shared generate-sources + 2 platform-build)
        # generate-sources gets empty/default context since it has no axis conditions
        mdl.assert_in_output(result, "3 required action(s)")

        # Both platform-build contexts should appear
        mdl.assert_in_output(result, "cross-platform:jvm")
        mdl.assert_in_output(result, "cross-platform:js")

        # generate-sources should have default context (shown in contexts list)
        mdl.assert_in_output(result, "default")
        mdl.assert_in_output(result, "#generate-sources")

        # Both generate-sources and platform-build should appear in plan
        mdl.assert_in_output(result, "generate-sources")
        mdl.assert_in_output(result, "platform-build")

        mdl.assert_in_output(result, "Execution completed successfully")
